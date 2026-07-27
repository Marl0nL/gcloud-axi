"""The single choke point for every `gcloud` invocation.

Nothing else in this package spawns a process. That keeps three invariants
enforceable in one place:

* ``--quiet`` is always passed, so gcloud can never prompt;
* a hard guard refuses any argument vector that would read a secret payload;
* gcloud's failure text is translated into the structured error shapes the
  rest of the tool raises.
"""

import json
import os
import re
import subprocess

from .errors import (
    CredentialExpiredError,
    GcloudError,
    NotFoundError,
    PermissionError_,
)

# Argument sequences this tool refuses to build under any circumstance. The
# wrapper is metadata-only about secrets by design; see `gcloud-axi secrets
# --help` for the reasoning.
FORBIDDEN_SEQUENCES = [
    ("secrets", "versions", "access"),
]


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


def invoke(args, project=None, text=False, env=None, timeout=None):
    """Run gcloud and return ``Result``.

    ``args`` is the gcloud argument vector without the binary. ``--quiet`` and,
    for JSON callers, ``--format=json`` are appended here so no call site can
    forget them.
    """
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


def call(args, project=None, text=False, env=None, timeout=None):
    """Like :func:`invoke` but raises the structured error instead."""
    result = invoke(args, project=project, text=text, env=env, timeout=timeout)
    if not result.ok:
        raise result.error
    return result.data


_EXPIRED = re.compile(
    r"(reauthentication|invalid[_ ]grant|token (has )?expired|credentials?[^\n]*expired"
    r"|401|UNAUTHENTICATED|was not found in the credential store"
    r"|do not have valid credentials|please run:?\s+.?gcloud auth login)",
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
