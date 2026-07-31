"""The two credentials, probed separately, reported side by side.

A machine authenticated to Google Cloud normally holds **two** credentials with
**two independent refresh states**:

* the **CLI credential** - what `gcloud auth login` establishes and what every
  `gcloud` invocation uses, and therefore what every read verb in this tool
  uses;
* **ADC**, Application Default Credentials - what `gcloud auth application-default
  login` establishes and what client libraries, Terraform/OpenTofu providers and
  direct REST calls use.

They lapse independently. Restoring one does not restore the other, and the
usual failure is a half-restored machine that reports itself as healthy because
whoever looked only looked at one half. Everything in this module exists to
make the other half visible.

Two rules govern the code here:

* **Liveness is proved, not inferred.** A credential is "live" only if a token
  mint for it succeeded just now. An account listed as active proves only that
  a name is on file.
* **No token value leaves this module.** Minting is how liveness is proved, and
  the minted value is discarded without being returned, rendered, logged, or
  put in an error field. When a token has to reach gcloud - the ADC fallback,
  `diagnose` re-issuing a call - it travels as a path to a 0600 file written by
  :mod:`gcloud_axi.tiering`, never as a value.
"""

import json
import os

from . import gcloudcmd, tiering

ADC_FILENAME = "application_default_credentials.json"

# The only keys ever lifted out of an ADC file. That file also holds a refresh
# token or a private key; an allow-list means a future field cannot be picked
# up by accident.
ADC_SAFE_KEYS = ("type", "client_email", "quota_project_id", "audience")

CLI_FIX = "gcloud auth login"
ADC_FIX = "gcloud auth application-default login"

CLI_USED_BY = "`gcloud` itself, and therefore every gcloud-axi read verb"
ADC_USED_BY = "client libraries, Terraform/OpenTofu providers, and direct REST calls"

LIVE = "live"
LAPSED = "lapsed"
ABSENT = "absent"
UNPROBED = "not probed"
# A probe the provider itself failed. A credential whose liveness could not be
# PROVED is not the same as one proved dead, and blaming the operator's
# identity during a provider incident is the costliest possible wrong answer.
UNVERIFIABLE = "unverifiable (provider-side failure)"

UNVERIFIABLE_FIX = (
    "retry shortly, or run `gcloud-axi diagnose` for provider status - "
    "re-authenticating will not fix a provider-side failure"
)


class Probe(object):
    """One credential's answer. Never carries a token value."""

    def __init__(self, kind, state, identity=None, type_=None, source=None,
                 detail=None, fix=None, used_by=None):
        self.kind = kind
        self.state = state
        self.identity = identity
        self.type = type_
        self.source = source
        self.detail = detail
        self.fix = fix
        self.used_by = used_by

    @property
    def live(self):
        return self.state == LIVE

    def pairs(self):
        """The ``(key, value)`` rows this credential renders as."""
        rows = [
            ("state", self.state),
            ("identity", self.identity or "(not recorded)"),
            ("type", self.type or "(unknown)"),
        ]
        if self.source:
            rows.append(("source", self.source))
        rows.append(("usedBy", self.used_by))
        if self.detail:
            rows.append(("detail", self.detail))
        rows.append(("fix", self.fix or "(none needed)"))
        return rows


# -- the CLI credential -----------------------------------------------------


def cli_identity():
    """Which account gcloud would use, without proving it still works.

    Never raises: every caller here has to render something even when gcloud
    is unhappy.
    """
    info = {"account": None, "type": None, "error": None}
    result = gcloudcmd.invoke(["auth", "list", "--filter=status:ACTIVE"])
    if not result.ok:
        info["error"] = getattr(result.error, "message", "unavailable")
        return info
    entries = result.data or []
    if not isinstance(entries, list) or not entries:
        return info
    account = entries[0].get("account")
    info["account"] = account
    if account and account.endswith(".iam.gserviceaccount.com"):
        info["type"] = "service_account"
    elif account:
        info["type"] = "user"
    return info


def probe_cli(probe=True):
    """Identity plus, when ``probe``, a real liveness check."""
    info = cli_identity()
    identity, type_ = info.get("account"), info.get("type")

    if info.get("error"):
        return Probe(
            "cli", LAPSED, identity, type_,
            detail=info["error"], fix=CLI_FIX, used_by=CLI_USED_BY,
        )
    if not identity:
        return Probe(
            "cli", ABSENT, None, None,
            detail="gcloud lists no active account",
            fix=CLI_FIX, used_by=CLI_USED_BY,
        )
    if not probe:
        return Probe(
            "cli", UNPROBED, identity, type_,
            detail="an account is on file; liveness was not proved (--no-probe)",
            fix=None, used_by=CLI_USED_BY,
        )

    state, detail, _code = _mint_state(["auth", "print-access-token"])
    return Probe(
        "cli", state, identity, type_,
        detail=detail, fix=_fix_for(state, CLI_FIX), used_by=CLI_USED_BY,
    )


# -- ADC --------------------------------------------------------------------


def adc_file_path():
    """Where ADC would be read from, following the same order gcloud does."""
    explicit = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if explicit:
        return os.path.expanduser(explicit), "GOOGLE_APPLICATION_CREDENTIALS"
    cloudsdk = os.environ.get("CLOUDSDK_CONFIG")
    if cloudsdk:
        return os.path.join(cloudsdk, ADC_FILENAME), "CLOUDSDK_CONFIG"
    home = os.path.expanduser("~")
    return os.path.join(home, ".config", "gcloud", ADC_FILENAME), "the gcloud config directory"


def adc_declared():
    """Read the non-secret half of the ADC file.

    Returns ``(fields, path, note)``. ``fields`` is empty when there is no
    readable ADC file - which is not itself a failure, because ADC can also come
    from a metadata server with nothing on disk at all.
    """
    path, origin = adc_file_path()
    if not os.path.isfile(path):
        return {}, path, "no ADC file at %s (set by %s)" % (path, origin)
    try:
        with open(path, "r") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        return {}, path, "ADC file present but unreadable: %s" % exc
    if not isinstance(data, dict):
        return {}, path, "ADC file present but not a JSON object"
    return {k: data[k] for k in ADC_SAFE_KEYS if k in data}, path, None


def probe_adc(probe=True):
    fields, path, note = adc_declared()
    type_ = fields.get("type")
    identity = fields.get("client_email") or fields.get("audience")
    source = path if fields else None

    if not identity and type_ == "authorized_user":
        # An authorized_user ADC file holds a refresh token and no account name.
        # Saying so is more useful than leaving the field empty and letting the
        # reader assume it matches the CLI account - which is exactly the
        # assumption that makes a half-restored machine look healthy.
        identity = "(an authorized_user ADC file records no account name)"

    if not probe:
        # Without a mint this cannot say "absent": ADC may still resolve from a
        # metadata server with nothing on disk. So it reports where it looked
        # and declines to draw a conclusion.
        return Probe(
            "adc", UNPROBED, identity, type_, source or path,
            detail=note or "liveness was not proved (--no-probe)",
            fix=None, used_by=ADC_USED_BY,
        )

    state, detail, code = _mint_state(
        ["auth", "application-default", "print-access-token"]
    )
    if state not in (LIVE, UNVERIFIABLE) and not fields and code != "CREDENTIAL_EXPIRED":
        # Nothing on disk, and the refusal was not "this credential is stale":
        # ADC was never set up here. That is a different problem from an ADC
        # that has gone stale, and it takes the operator to the same command by
        # a different route - so it is worth naming separately.
        return Probe(
            "adc", ABSENT, identity, type_, source,
            detail=detail or note, fix=ADC_FIX, used_by=ADC_USED_BY,
        )
    return Probe(
        "adc", state, identity, type_,
        source or "the environment (no ADC file; gcloud resolved it elsewhere)",
        detail=detail if state != LIVE else note,
        fix=_fix_for(state, ADC_FIX),
        used_by=ADC_USED_BY,
    )


def _mint_state(args):
    """Mint a token to prove liveness, then drop it.

    Returns ``(state, detail, code)``. The minted value is never bound to a name
    that outlives this function and never reaches the caller - the point of a
    liveness probe is the verdict, not the material.
    """
    result = gcloudcmd.invoke(args, text=True, credential=gcloudcmd.AMBIENT)
    if result.ok:
        # `result.data` is the token. Nothing reads it; it goes out of scope
        # with this frame.
        if (result.data or "").strip():
            return LIVE, None, None
        return LAPSED, "the token mint returned nothing", "MINT_EMPTY"
    code = getattr(result.error, "code", None)
    message = getattr(result.error, "message", "the token mint failed")
    # A 5xx from the mint is the provider failing to answer, not the credential
    # failing to work - reporting "lapsed" here would blame the operator's
    # identity for a Google outage.
    if code == "PROVIDER_ERROR":
        return UNVERIFIABLE, message, code
    return LAPSED, message, code


def _fix_for(state, login_fix):
    if state == LIVE:
        return None
    if state == UNVERIFIABLE:
        return UNVERIFIABLE_FIX
    return login_fix


# -- materialising ADC for a fallback ---------------------------------------


def mint_adc_token_file():
    """Mint an ADC token into a 0600 file. Returns ``(directory, path)``.

    Returns ``(None, None)`` when ADC cannot produce a token, so the caller can
    report a fallback that was attempted and did not help rather than raising a
    second, less informative failure over the first one.
    """
    result = gcloudcmd.invoke(
        ["auth", "application-default", "print-access-token"],
        text=True,
        credential=gcloudcmd.AMBIENT,
    )
    if not result.ok:
        return None, None
    token = (result.data or "").strip()
    if not token or any(ch.isspace() for ch in token):
        return None, None
    return tiering.write_scratch_token(token)


def summarise(cli, adc):
    """One sentence naming which half is live - the aggregate, computed once.

    Only a state proved dead (lapsed, absent) may be described as dead: an
    unprobed or unverifiable credential got no verdict, and saying "not live"
    about it would assert the very thing that was not checked.
    """
    if cli.live and adc.live:
        return "both credentials are live"
    if cli.live and not adc.live:
        if adc.state == UNPROBED:
            return "the CLI credential is live; ADC liveness was not probed (--no-probe)"
        if adc.state == UNVERIFIABLE:
            return (
                "the CLI credential is live; ADC could not be verified - the probe "
                "failed provider-side, which proves nothing about ADC itself"
            )
        return (
            "the CLI credential is live but ADC is %s - anything using a client "
            "library, Terraform/OpenTofu or a direct REST call will still fail" % adc.state
        )
    if adc.live and not cli.live:
        if cli.state == UNPROBED:
            return "ADC is live; CLI credential liveness was not probed (--no-probe)"
        if cli.state == UNVERIFIABLE:
            return (
                "ADC is live; the CLI credential could not be verified - the probe "
                "failed provider-side, which proves nothing about the credential itself"
            )
        return (
            "ADC is live but the CLI credential is %s - `gcloud` will fail while "
            "client libraries and REST calls keep working" % cli.state
        )
    if UNVERIFIABLE in (cli.state, adc.state):
        return (
            "liveness could not be verified - a probe failed provider-side, and a "
            "credential that could not be proved live is not one proved dead"
        )
    if UNPROBED in (cli.state, adc.state):
        return "liveness was not probed (--no-probe)"
    return "neither credential is live"


def in_step(cli, adc):
    """Whether the two halves agree. ``None`` when either side has no verdict."""
    if UNPROBED in (cli.state, adc.state) or UNVERIFIABLE in (cli.state, adc.state):
        return None
    return cli.live == adc.live
