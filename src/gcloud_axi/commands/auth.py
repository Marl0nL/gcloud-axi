"""`gcloud-axi auth` - both credentials, probed, side by side.

The question this answers is the one that cannot be answered by looking at
either credential alone: *which half is live?* A machine with a live CLI
credential and lapsed ADC looks healthy to `gcloud auth list` and fails every
Terraform plan; a machine with the reverse looks broken to `gcloud` and serves
every REST call. Both are common, and neither is visible from one probe.

Read-only, in both directions: it mints tokens to prove liveness and discards
them, it writes nothing, and it prints no token material under any flag.
"""

import os

from .. import context, credentials, flags, helptext, timeutil, toon

AUTH_FLAGS = {"no-probe": flags.BOOL}


def help_out():
    return helptext.render(
        "gcloud-axi auth [flags]",
        description=(
            "Probe the CLI credential and Application Default Credentials separately "
            "and report which is live, which is lapsed, and what fixes each."
        ),
        flags=[
            "--no-probe       report identity and source only; mint nothing, "
            "reach no network",
            "--help           this text",
        ],
        notes=[
            "The two credentials have separate refresh state. `gcloud auth login` restores "
            "the CLI credential; `gcloud auth application-default login` restores ADC. "
            "Neither restores the other, which is why 'auth is back' is worth checking twice",
            "Liveness is proved by minting a token, not by reading a config file - an account "
            "can be listed as active long after its refresh token stopped working",
            "A probe the provider itself fails reports 'unverifiable (provider-side failure)', "
            "not 'lapsed' - a liveness that could not be proved is not one disproved, and "
            "re-authenticating is not the fix for a Google outage",
            "The minted tokens are discarded. This command prints no token value, writes no "
            "credential, and changes nothing about either one",
            "Read verbs fall back to ADC when the CLI credential is lapsed, and say so in "
            "their output when they do. Mutating verbs never fall back",
            "This exits 0 whether or not a credential is lapsed - it reports state, and the "
            "exit code describes the invocation. Test the `credentials.cli` and "
            "`credentials.adc` fields instead",
        ],
        examples=[
            "gcloud-axi auth",
            "gcloud-axi auth --no-probe",
            "gcloud-axi diagnose run status",
        ],
    )


def dispatch(ctx_factory, argv):
    if flags.wants_help(argv):
        return help_out(), 0
    args = flags.parse(argv, AUTH_FLAGS, "auth", max_positional=0)
    probe = not args.get("no-probe")

    cli = credentials.probe_cli(probe=probe)
    adc = credentials.probe_adc(probe=probe)

    out = toon.Out()
    out.block(
        "credentials",
        [
            ("cli", cli.state),
            ("adc", adc.state),
            ("bothLive", cli.live and adc.live),
            ("inStep", credentials.in_step(cli, adc)),
            ("summary", credentials.summarise(cli, adc)),
            ("probed", probe),
        ],
    )

    out.raw("")
    out.block("cli", cli.pairs())
    out.raw("")
    out.block("adc", adc.pairs())

    marker = context.grant_marker()
    scoped = os.environ.get("CLOUDSDK_CONFIG")
    if scoped or marker:
        out.raw("")
        out.block("scope", _scope_pairs(scoped, marker))

    out.raw("")
    out.note(
        "the two credentials refresh independently - restoring one leaves the other "
        "exactly as it was"
    )
    out.help(_help_lines(cli, adc, marker))
    # Exit 0: the command answered the question it was asked. A lapsed
    # credential is the answer, not a failure of this command - the tool's exit
    # codes describe the invocation, not the health of what it looked at. Test
    # the `credentials.cli` and `credentials.adc` fields for that.
    return out, 0


def _scope_pairs(scoped, marker):
    pairs = [("scopedConfigDir", scoped or "(none - the ambient gcloud configuration)")]
    if not marker:
        return pairs + [("tier", "(not a tiered credential)")]
    remaining = _remaining(marker.get("expiresAt"))
    return pairs + [
        ("tier", marker.get("tier")),
        ("issuedFor", marker.get("task")),
        ("expiresAt", marker.get("expiresAt")),
        ("expiresIn", remaining),
        ("expired", remaining == "expired"),
    ]


def _remaining(expires):
    parsed = timeutil.parse_timestamp(expires)
    if parsed is None:
        return None
    delta = int((parsed - timeutil.now()).total_seconds())
    if delta <= 0:
        return "expired"
    return timeutil.relative_seconds(delta)


def _help_lines(cli, adc, marker):
    lines = []
    # The fix for the broken half leads, because that is the next thing typed.
    # Only a state proved dead earns a login command: an unprobed or
    # unverifiable credential got no verdict to act on.
    if cli.state in (credentials.LAPSED, credentials.ABSENT) and cli.fix:
        if marker:
            lines.append(
                "This is a scoped, tiered credential - ask whoever issued it for a "
                "replacement rather than re-authenticating over it"
            )
        else:
            lines.append("Run `%s` to restore the CLI credential" % cli.fix)
    if adc.state in (credentials.LAPSED, credentials.ABSENT) and adc.fix:
        lines.append("Run `%s` to restore ADC" % adc.fix)
    if credentials.UNVERIFIABLE in (cli.state, adc.state):
        lines.append(
            "A liveness probe failed provider-side - retry shortly, and do not "
            "re-authenticate on the strength of an unverifiable probe"
        )
    if cli.live and adc.live:
        lines.append(
            "Both halves are live - a failure now is more likely the request, the "
            "resource or the provider"
        )
    lines.append(
        "Run `gcloud-axi diagnose <command>` to test a failing call as another identity"
    )
    lines.append("Run `gcloud-axi` for ambient project and service state")
    return lines
