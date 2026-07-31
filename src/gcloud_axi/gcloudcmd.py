"""The single choke point for every `gcloud` invocation.

Nothing else in this package spawns a process. That keeps four invariants
enforceable in one place:

* ``--quiet`` is always passed, so gcloud can never prompt;
* a hard guard refuses any argument vector that would read a secret payload;
* gcloud's failure text is translated into the structured error shapes the
  rest of the tool raises;
* which credential a call runs as is decided here and nowhere else, so the
  read-only restriction on the ADC fallback cannot be forgotten by a call site.
"""

import atexit
import json
import os
import re
import subprocess

from .errors import (
    CredentialExpiredError,
    GcloudError,
    NotFoundError,
    PermissionError_,
    ProviderError,
)

# Argument sequences this tool refuses to build under any circumstance. The
# wrapper is metadata-only about secrets by design; see `gcloud-axi secrets
# --help` for the reasoning.
FORBIDDEN_SEQUENCES = [
    ("secrets", "versions", "access"),
]

# A vector may be re-issued under a different credential only when it names one
# of these verbs. An allow-list on purpose: an unrecognised verb gets no
# fallback and no impersonated retry, so a mutating call added later is excluded
# by default rather than by anyone remembering to exclude it.
#
# The verb is looked for anywhere in the vector rather than at the end, because
# a read frequently carries a positional after it - `logging read <filter>`,
# `sql instances describe <name>`.
READ_ONLY_VERBS = frozenset(
    ["list", "describe", "read", "get-iam-policy", "get-value"]
)

# The second net, for the case the first one cannot see: a resource whose *name*
# happens to be "list" would otherwise carry `run jobs execute list` past the
# allow-list. `run` is absent deliberately - here it is a product name, not a verb.
MUTATING_VERBS = frozenset(
    [
        "execute", "create", "delete", "update", "deploy", "set", "patch",
        "replace", "import", "export", "restore", "enable", "disable", "add",
        "remove", "undelete", "kill", "cancel", "promote", "rollback",
        "set-iam-policy", "add-iam-policy-binding", "remove-iam-policy-binding",
    ]
)

# ...and never for these leading words, whose subject *is* the credential
# plumbing. Re-running `auth list` under a borrowed token answers a different
# question from the one that was asked.
CREDENTIAL_SUBJECTS = frozenset(["auth", "config"])

# Sentinel for "run as the ambient credential, ignoring any active override and
# taking no fallback". Probes use it so that what they measure is the machine's
# own state rather than a substitution this module made on their behalf.
AMBIENT = object()

_override = None          # token-file path in force for every call, or None
_fallback_enabled = True  # whether a lapsed CLI credential may retry under ADC
_adc_scratch = None       # (directory, token_path) minted at most once, lazily
_adc_attempted = False
_notices = []


def is_read_only(args):
    """Whether this vector may be re-issued under a different credential."""
    words = [a for a in args if not a.startswith("-")]
    if not words:
        return False
    if words[0] in CREDENTIAL_SUBJECTS:
        return False
    if any(word in MUTATING_VERBS for word in words):
        return False
    return any(word in READ_ONLY_VERBS for word in words)


class using_credential(object):
    """Run every call in this block under ``token_file`` (``None`` = ambient).

    `diagnose` uses it to ask one question as several identities. It also turns
    the automatic ADC fallback off, because inside an explicit A/B a silent
    substitution would answer a question nobody asked.
    """

    def __init__(self, token_file):
        self._token_file = token_file
        self._saved = None

    def __enter__(self):
        global _override, _fallback_enabled
        self._saved = (_override, _fallback_enabled)
        _override, _fallback_enabled = self._token_file, False
        return self

    def __exit__(self, *_exc):
        global _override, _fallback_enabled
        _override, _fallback_enabled = self._saved
        return False


def add_notice(pairs):
    """Record something the caller must be told about how a call was made."""
    _notices.append(list(pairs))


def drain_notices():
    """Return and clear the notices accumulated during this process."""
    collected = list(_notices)
    del _notices[:]
    return collected


class Result(object):
    def __init__(self, ok, data=None, error=None):
        self.ok = ok
        self.data = data
        self.error = error


def binary():
    return os.environ.get("GCLOUD_AXI_GCLOUD") or "gcloud"


def _check_forbidden(args):
    words = [a for a in args if not a.startswith("-")]
    for seq in FORBIDDEN_SEQUENCES:
        span = len(seq)
        for i in range(0, max(0, len(words) - span + 1)):
            if tuple(words[i : i + span]) == seq:
                raise GcloudError(
                    "refusing to run `gcloud %s` - this tool never reads secret payloads"
                    % " ".join(seq),
                    code="FORBIDDEN_COMMAND",
                    help_lines=[
                        "Run `gcloud-axi secrets --help` to see why payload access is out of scope",
                    ],
                )


def invoke(args, project=None, text=False, env=None, timeout=None, credential=None):
    """Run gcloud and return ``Result``.

    ``args`` is the gcloud argument vector without the binary. ``--quiet`` and,
    for JSON callers, ``--format=json`` are appended here so no call site can
    forget them.

    ``credential`` is a path to a file holding an access token, or :data:`AMBIENT`
    to force the machine's own credential. Left as ``None`` it follows whatever
    :class:`using_credential` block is in force, and - for a read-only vector
    whose only problem is a lapsed CLI credential - falls back to ADC once.
    """
    result = _run(args, project=project, text=text, env=env, timeout=timeout,
                  credential=credential)
    if result.ok or credential is not None:
        return result
    if not isinstance(result.error, CredentialExpiredError):
        return result
    if not _fallback_enabled or not is_read_only(args):
        return result

    token_path = _adc_token_file()
    if token_path is None:
        # ADC cannot help either. Say so on the original error rather than
        # replacing it, so the operator learns both halves are down in one read.
        result.error.help_lines = [
            "Run `gcloud-axi auth` to see which of the two credentials is lapsed",
        ] + result.error.help_lines
        result.error.fields = list(result.error.fields) + [
            ("adcFallback", "attempted; ADC could not mint a token either"),
        ]
        return result

    retried = _run(args, project=project, text=text, env=env, timeout=timeout,
                   credential=token_path)
    if not retried.ok:
        result.error.fields = list(result.error.fields) + [
            ("adcFallback", "attempted; the same call failed under ADC too"),
        ]
        return result

    add_notice([
        ("used", "adc"),
        ("reason", "the gcloud CLI credential is lapsed; this read is a fallback"),
        ("scope", "read-only calls only - nothing is mutated under a fallback credential"),
        ("caution",
         "ADC may hold different permissions than the CLI credential, so results can differ"),
        ("fix", "gcloud auth login"),
    ])
    return retried


def _adc_token_file():
    """The ADC token file for this process, minted at most once."""
    global _adc_scratch, _adc_attempted
    if _adc_attempted:
        return _adc_scratch[1] if _adc_scratch else None
    _adc_attempted = True
    from . import credentials

    directory, path = credentials.mint_adc_token_file()
    if directory is None:
        return None
    _adc_scratch = (directory, path)
    atexit.register(_discard_adc_token_file)
    return path


def _discard_adc_token_file():
    global _adc_scratch
    if _adc_scratch:
        from . import tiering

        tiering.discard_scratch_token(_adc_scratch[0])
        _adc_scratch = None


def _run(args, project=None, text=False, env=None, timeout=None, credential=None):
    _check_forbidden(args)

    argv = [binary()] + list(args)
    if project and not any(
        a == "--project" or a.startswith("--project=") for a in args
    ):
        argv.append("--project=%s" % project)
    if not text and not any(a.startswith("--format") for a in args):
        argv.append("--format=json")
    if "--quiet" not in argv:
        argv.append("--quiet")

    token_file = None if credential is AMBIENT else (credential or _override)
    if token_file and not any(a.startswith("--access-token-file") for a in args):
        # The path, never the value: an argument vector is visible in every
        # process listing on the machine.
        argv.append("--access-token-file=%s" % token_file)

    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    # gcloud otherwise emits update nags and survey prompts into the output we
    # are trying to keep token-cheap.
    run_env.setdefault("CLOUDSDK_CORE_DISABLE_PROMPTS", "1")

    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=run_env,
        )
        out, err = proc.communicate(timeout=timeout)
    except OSError as exc:
        return Result(
            False,
            error=GcloudError(
                "cannot run `%s`: %s" % (binary(), exc),
                code="GCLOUD_NOT_FOUND",
                help_lines=[
                    "Install the Google Cloud SDK, or set GCLOUD_AXI_GCLOUD=<path to gcloud>",
                ],
            ),
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return Result(
            False,
            error=GcloudError(
                "`gcloud %s` timed out after %ss" % (" ".join(args), timeout),
                code="TIMEOUT",
            ),
        )

    stdout = out.decode("utf-8", "replace")
    stderr = err.decode("utf-8", "replace")

    if proc.returncode != 0:
        return Result(False, error=classify(args, stderr or stdout, proc.returncode))

    if text:
        return Result(True, data=stdout)

    stripped = stdout.strip()
    if not stripped:
        return Result(True, data=None)
    try:
        return Result(True, data=json.loads(stripped))
    except ValueError:
        return Result(
            False,
            error=GcloudError(
                "`gcloud %s` returned output that is not JSON" % " ".join(args),
                code="BAD_OUTPUT",
                help_lines=["Run the same command with `gcloud` directly to inspect it"],
            ),
        )


def call(args, project=None, text=False, env=None, timeout=None, credential=None):
    """Like :func:`invoke` but raises the structured error instead."""
    result = invoke(args, project=project, text=text, env=env, timeout=timeout,
                    credential=credential)
    if not result.ok:
        raise result.error
    return result.data


_EXPIRED = re.compile(
    r"(reauthentication|invalid[_ ]grant|token (has )?expired|credentials?[^\n]*expired"
    r"|401|UNAUTHENTICATED|was not found in the credential store"
    # ADC reauth points at a different login command, and its message is the
    # only evidence that distinguishes a stale ADC from one never set up.
    r"|do not have valid credentials|please run:?\s+.?gcloud auth (application-default )?login)",
    re.I,
)
_DENIED = re.compile(
    r"(PERMISSION_DENIED|permission denied|does not have permission|403|forbidden"
    r"|caller does not have|IAM_PERMISSION_DENIED)",
    re.I,
)
_MISSING = re.compile(
    r"(NOT_FOUND|not found|does not exist|404|no such )",
    re.I,
)
_DISABLED_API = re.compile(
    r"(SERVICE_DISABLED|has not been used in project|API .*is not enabled)", re.I
)
# A server-side failure: the request was understood and the far end broke. The
# status code needs context around it, so that a resource whose name happens to
# contain "503" is not read as an outage.
_SERVER = re.compile(
    r"(?:HTTPError\s*|ResponseError:?\s*|status(?:_code)?\s*[:=]\s*\[?|code\s*[:=]\s*\[?"
    r"|error\s*[:=]\s*\[?|\breturned\s+)\s*\[?(5\d\d)\b"
    # The gRPC status names stay case-SENSITIVE. Matched case-insensitively,
    # "internal" and "unavailable" are ordinary English and turn up inside
    # permission and not-found messages, which would then be blamed on Google.
    r"|(?-i:\b(?:INTERNAL|UNAVAILABLE|DEADLINE_EXCEEDED)\b)"
    r"|\b(?:Internal error|Backend Error|Service Unavailable|Not Implemented"
    r"|Bad Gateway|Gateway Timeout)\b",
    re.I,
)


def _server_status(stderr):
    """The 5xx code in ``stderr`` if one is stated, else ``None``.

    Scans every match rather than the first, because a message can name the
    condition ("UNAVAILABLE") before it names the code.
    """
    for match in _SERVER.finditer(stderr):
        if match.group(1):
            return match.group(1)
    return None


def _first_useful_line(stderr):
    for line in stderr.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("WARNING") or line.startswith("Updates are available"):
            continue
        return line
    return stderr.strip().splitlines()[0] if stderr.strip() else "gcloud failed"


def classify(args, stderr, returncode):
    """Map gcloud's stderr onto one of our error types."""
    command = " ".join(a for a in args if not a.startswith("-"))
    detail = _first_useful_line(stderr)
    # Keep the raw text bounded; the full thing is rarely worth the tokens.
    if len(detail) > 400:
        detail = detail[:400].rstrip() + " ..."

    if _EXPIRED.search(stderr):
        return CredentialExpiredError(
            "credential rejected by Google - it is expired or no longer valid",
            help_lines=[
                "Run `gcloud-axi` to see which credential is currently active",
                "If a short-lived token was issued to you, ask its issuer for a fresh one",
                "Otherwise re-authenticate the ambient credential yourself",
            ],
            fields=[("detail", detail), ("command", "gcloud " + command)],
        )
    if _SERVER.search(stderr) and not _DISABLED_API.search(stderr):
        # The lesson this branch encodes: a 5xx is the provider's failure until
        # shown otherwise, and the operator should not have to think of that.
        # Checking the incident feed here is what makes the wrong conclusion -
        # "it must be my identity" - expensive to reach and cheap to disprove.
        error = ProviderError(
            "`gcloud %s` failed server-side - this is the provider's end, not your request"
            % command,
            help_lines=[
                # A placeholder, not the gcloud vector that failed: `gcloud-axi
                # diagnose` takes a gcloud-axi command, and a suggestion the
                # caller cannot paste is worse than none.
                "Run `gcloud-axi diagnose <command>` to re-issue this as another identity",
                "Do not conclude a credential problem from a 5xx until the provider is ruled out",
            ],
            fields=[
                ("detail", detail),
                ("httpStatus", _server_status(stderr) or "(5xx, code not stated)"),
                ("retryable", True),
            ],
        )
        from . import provider

        provider.annotate(error)
        return error
    if _DISABLED_API.search(stderr):
        return GcloudError(
            "the API backing `gcloud %s` is not enabled for this project" % command,
            code="API_DISABLED",
            help_lines=[
                "Run `gcloud services enable <api>.googleapis.com --project <project>` as a project admin",
            ],
            fields=[("detail", detail)],
        )
    if _DENIED.search(stderr):
        return PermissionError_(
            "the active credential is not permitted to run `gcloud %s`" % command,
            help_lines=[
                "Run `gcloud-axi` to confirm which credential and project are active",
                "Run `gcloud-axi iam audit --member <member>` to see what that identity holds",
                "If you hold a scoped short-lived token, request a higher tier from whoever issued it",
            ],
            fields=[("detail", detail)],
        )
    if _MISSING.search(stderr):
        return NotFoundError(
            "`gcloud %s` found no such resource" % command,
            help_lines=["Run `gcloud-axi overview` to see what exists in this project"],
            fields=[("detail", detail)],
        )
    return GcloudError(
        "`gcloud %s` failed with exit %d" % (command, returncode),
        help_lines=["Run the same command with `gcloud` directly to see full output"],
        fields=[("detail", detail)],
    )
