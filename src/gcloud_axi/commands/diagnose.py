"""`gcloud-axi diagnose` - is it me, or is it them?

A failing call has a small number of causes and they are cheap to tell apart,
but only if something does the telling apart. Left to a human under pressure the
first plausible cause wins, and the first plausible cause is nearly always "my
credential", because that is the one the operator has recently been fighting.

So this walks the ladder in the order that makes the wrong answer expensive to
reach:

1. **Credential liveness** - both halves, probed, not assumed.
2. **The same question as another identity** - re-issue the failing call as ADC,
   and as an impersonated service account when one is named. A failure that
   survives every identity is not about identity, and that is a proof rather
   than an opinion.
3. **The provider's own status** - an open incident on Google's feed reframes
   the whole thing, and reading it costs one unauthenticated GET.

Then it states a verdict, because an operator who has to assemble the verdict
themselves will assemble the one they already believed.

Read-only by construction: only commands on :data:`DIAGNOSABLE` may be re-issued,
and :mod:`gcloud_axi.gcloudcmd` refuses - before any process is spawned, with
``REFUSED_UNDER_SUBSTITUTED_CREDENTIAL`` - to attach a substituted credential to
any vector outside its read-only allow-list.
"""

from .. import config as config_mod
from .. import credentials, flags, gcloudcmd, helptext, provider, tiering, timeutil, toon
from ..errors import AxiError, UsageError

DIAGNOSE_FLAGS = {
    "project": flags.VALUE,
    "region": flags.VALUE,
    "as": flags.VALUE,
    "tier": flags.VALUE,
    "no-provider-status": flags.BOOL,
    "ttl": flags.VALUE,
}

# Commands whose failure may be reproduced here. Everything absent is absent on
# purpose: `jobs run` starts an execution, and `grant`/`revoke` act on
# credentials. A diagnostic that changes the thing it is diagnosing is not one.
DIAGNOSABLE = ("overview", "run", "logs", "jobs", "sql", "secrets", "iam", "builds")
REFUSED = {
    "jobs run": "it starts a job execution",
    "grant": "it issues a credential",
    "revoke": "it acts on a credential",
}

IMPERSONATION_TTL = 900


def help_out():
    return helptext.render(
        "gcloud-axi diagnose [<read command> ...] [flags]",
        description=(
            "Work out whether a failing read is your credential, your permissions, "
            "the resource, or the provider - by testing each, in that order."
        ),
        flags=helptext.GLOBAL_FLAGS
        + [
            "--as <sa>        also re-issue the call impersonating this service account",
            "--tier <name>    same, using a declared tier's service account",
            "--ttl <seconds>  lifetime of the impersonated token (default %d)"
            % IMPERSONATION_TTL,
            "--no-provider-status  skip the incident-feed lookup; reach no network",
        ],
        notes=[
            "With no command it reports credential liveness and provider status - the "
            "'is it me or them' answer when there is no single call to point at",
            "Only read commands may be given: %s. `jobs run`, `grant` and `revoke` are "
            "refused, since a diagnostic must not change what it is diagnosing"
            % ", ".join(DIAGNOSABLE),
            "Each identity runs the same command with the same flags. The comparison is the "
            "point: a failure that survives every identity is not about identity",
            "No token value is printed. An impersonated token is written to a 0600 file for "
            "the length of the attempt and removed afterwards",
            "This exits 0 when the diagnosis completed, whatever it found. Read `verdict:` "
            "for the answer",
        ],
        examples=[
            "gcloud-axi diagnose",
            "gcloud-axi diagnose run status my-service",
            "gcloud-axi diagnose logs my-service --since 1h",
            "gcloud-axi diagnose secrets --as inspect@my-project.iam.gserviceaccount.com",
            "gcloud-axi diagnose builds --tier inspect",
        ],
    )


def dispatch(ctx_factory, argv):
    if flags.wants_help(argv):
        return help_out(), 0

    own, command = _split(argv)
    args = flags.parse(own, DIAGNOSE_FLAGS, "diagnose", max_positional=0)
    if command:
        _validate(command)
    check_provider = not args.get("no-provider-status")

    out = toon.Out()
    out.field("target", " ".join(command) if command else "(no command - credentials and provider only)")

    # -- rungs 1 and 2: probes, then the same question as other identities --
    # Both run with the automatic incident-feed annotation off: every outcome
    # here keeps only code+message, and rung 3 below is this command's one
    # provider consultation - which keeps --no-provider-status meaning what its
    # help text says: reach no network.
    with gcloudcmd.suppress_provider_annotation():
        cli = credentials.probe_cli()
        adc = credentials.probe_adc()
        attempts = _attempt_all(ctx_factory, command, args, adc) if command else []

    out.raw("")
    out.block(
        "credentials",
        [
            ("cli", cli.state),
            ("adc", adc.state),
            ("bothLive", cli.live and adc.live),
            ("inStep", credentials.in_step(cli, adc)),
            ("summary", credentials.summarise(cli, adc)),
        ],
    )

    if command:
        out.raw("")
        out.table(
            "attempts",
            ["identity", "outcome", "code", "detail"],
            [a.row() for a in attempts],
        )

    # -- rung 3: the provider -----------------------------------------------
    incidents, feed_problem = (None, "skipped (--no-provider-status)")
    if check_provider:
        incidents, feed_problem = provider.fetch()
    live_incidents = provider.open_incidents(incidents or [])
    out.raw("")
    _emit_provider(out, incidents, feed_problem, live_incidents)

    # -- the verdict ---------------------------------------------------------
    verdict, reasoning, hints = _judge(cli, adc, command, attempts, live_incidents,
                                       feed_problem, args)
    out.raw("")
    out.field("verdict", verdict)
    out.field("reasoning", reasoning)
    out.raw("")
    out.help(hints)
    return out, 0


def _split(argv):
    """Split into diagnose's own flags and the command to be diagnosed.

    Diagnose's flags come first; the first non-flag token starts the command,
    and everything from there on - flags included - belongs to it and is
    forwarded verbatim. That keeps `--since` meaning what it means to `logs`
    rather than having to be re-declared here, and it keeps the fail-loud
    guarantee intact: an unknown flag before the command is rejected here, and
    an unknown flag after it is rejected by the command itself.
    """
    index = 0
    while index < len(argv):
        token = argv[index]
        if not token.startswith("-"):
            break
        index += 1
        name = token[2:].split("=", 1)[0] if token.startswith("--") else None
        # A value written `--as sa@example` must not have its value mistaken
        # for the start of the command.
        if "=" not in token and DIAGNOSE_FLAGS.get(name) in (flags.VALUE, flags.LIST):
            index += 1
    return list(argv[:index]), list(argv[index:])


def _validate(command):
    joined = " ".join(command)
    for refused, why in REFUSED.items():
        if joined == refused or joined.startswith(refused + " "):
            raise UsageError(
                "`%s` cannot be diagnosed because %s" % (refused, why),
                code="NOT_DIAGNOSABLE",
                help_lines=[
                    "Diagnosable commands: %s" % ", ".join(DIAGNOSABLE),
                    "Run `gcloud-axi diagnose` with no command for credential and "
                    "provider state alone",
                ],
            )
    if command[0] not in DIAGNOSABLE:
        raise UsageError(
            '"%s" is not a diagnosable command' % command[0],
            code="NOT_DIAGNOSABLE",
            help_lines=[
                "Diagnosable commands: %s" % ", ".join(DIAGNOSABLE),
                "Run `gcloud-axi diagnose <command>` with one of those",
            ],
        )


class Attempt(object):
    def __init__(self, identity, outcome, code=None, detail=None):
        self.identity = identity
        self.outcome = outcome
        self.code = code
        self.detail = detail

    def row(self):
        detail, _ = toon.truncate(self.detail or "", 160)
        return {
            "identity": self.identity,
            "outcome": self.outcome,
            "code": self.code or "-",
            "detail": detail or "-",
        }


def _attempt_all(ctx_factory, command, args, adc):
    # Diagnose's own --project/--region still have to reach the command, or the
    # attempts would be scoped differently from the invocation being diagnosed.
    forwarded = list(command[1:])
    for name in ("project", "region"):
        if args.get(name) and not any(
            t == "--" + name or t.startswith("--%s=" % name) for t in forwarded
        ):
            forwarded.append("--%s=%s" % (name, args.get(name)))

    attempts = [_attempt(ctx_factory, command, forwarded, "ambient", None)]

    if adc.live:
        directory, path = credentials.mint_adc_token_file()
        if path:
            try:
                attempts.append(
                    _attempt(ctx_factory, command, forwarded, "adc", path)
                )
            finally:
                tiering.discard_scratch_token(directory)
        else:
            attempts.append(Attempt("adc", "unavailable", "MINT_FAILED",
                                    "ADC probed live but produced no usable token"))

    target = _impersonation_target(args)
    if target:
        attempts.append(
            _attempt_as_service_account(ctx_factory, command, forwarded, target, args)
        )
    return attempts


def _impersonation_target(args):
    if args.get("as"):
        return args.get("as")
    name = args.get("tier")
    if not name:
        return None
    cfg = config_mod.load()
    tier = tiering.resolve_tier(cfg, name, "diagnose")
    return tier.service_account


def _attempt(ctx_factory, command, forwarded, label, token_file):
    """Run the command once under one credential and record only the outcome.

    ``forwarded`` is the handler's complete argv - the command word itself is
    already stripped, so it must not be prepended again here.
    """
    from ..cli import COMMANDS

    handler = COMMANDS[command[0]]
    argv = list(forwarded)
    with gcloudcmd.using_credential(token_file):
        try:
            handler(ctx_factory, argv)
        except UsageError:
            # A malformed command is not a diagnosis. Let it out so it exits 2
            # the way it would have without the wrapper - the fail-loud
            # guarantee has to survive being called through something else.
            raise
        except AxiError as exc:
            return Attempt(label, "failed", exc.code, exc.message)
        except Exception as exc:  # a bug here must not swallow the diagnosis
            return Attempt(label, "failed", "INTERNAL", str(exc))
    return Attempt(label, "succeeded")


def _attempt_as_service_account(ctx_factory, command, forwarded, target, args):
    ttl = args.int("ttl", default=IMPERSONATION_TTL, minimum=1, maximum=config_mod.MAX_TTL)
    result = gcloudcmd.invoke(
        [
            "auth",
            "print-access-token",
            "--impersonate-service-account=%s" % target,
            "--lifetime=%ds" % ttl,
        ],
        text=True,
        credential=gcloudcmd.AMBIENT,
    )
    if not result.ok:
        return Attempt(
            "sa:" + target,
            "unavailable",
            "MINT_FAILED",
            "could not mint a token for %s: %s"
            % (target, getattr(result.error, "message", "unknown")),
        )
    token = (result.data or "").strip()
    if not token or any(ch.isspace() for ch in token):
        return Attempt("sa:" + target, "unavailable", "MINT_EMPTY",
                       "the token mint returned nothing usable")
    directory, path = tiering.write_scratch_token(token)
    del token
    try:
        attempt = _attempt(ctx_factory, command, forwarded, "sa:" + target, path)
    finally:
        tiering.discard_scratch_token(directory)
    return attempt


def _emit_provider(out, incidents, problem, live):
    if problem:
        out.block("provider", [("source", provider.feed_url()), ("status", problem)])
        return
    out.block(
        "provider",
        [
            ("source", provider.feed_url()),
            ("checkedAt", timeutil.rfc3339(timeutil.now())),
            ("openIncidents", len(live)),
        ],
    )
    if not live:
        out.note(
            "Google publishes no open incident right now - a failure here is more "
            "likely local, transient, or specific to this request"
        )
        return
    out.table(
        "incidents",
        ["severity", "service", "began", "summary"],
        provider.rows(live[: provider.SHOW_LIMIT]),
        indent=1,
    )
    if len(live) > provider.SHOW_LIMIT:
        out.note(
            "%d further open incident(s) not shown - see https://status.cloud.google.com/"
            % (len(live) - provider.SHOW_LIMIT)
        )


def _judge(cli, adc, command, attempts, live_incidents, feed_problem, args):
    """One verdict, one sentence of reasoning, and the next commands to run."""
    incident_count = len(live_incidents)
    hints = []

    if not command:
        if credentials.UNVERIFIABLE in (cli.state, adc.state):
            # A probe the provider failed reached no verdict; calling that a
            # lapse would send the operator to a login command during an outage.
            return (
                "credential-state-unverifiable",
                credentials.summarise(cli, adc),
                [
                    "Retry shortly - a provider-side probe failure is usually transient",
                    "Check https://status.cloud.google.com/ before re-authenticating anything",
                    "Run `gcloud-axi auth` for the full per-credential detail",
                ],
            )
        if not cli.live and not adc.live:
            return (
                "both-credentials-lapsed",
                "neither credential could mint a token, so nothing here will work until "
                "one is restored",
                [
                    "Run `%s` to restore the CLI credential" % credentials.CLI_FIX,
                    "Run `%s` to restore ADC" % credentials.ADC_FIX,
                    "Run `gcloud-axi auth` for the full per-credential detail",
                ],
            )
        if not cli.live or not adc.live:
            lapsed = cli if not cli.live else adc
            return (
                "%s-credential-lapsed" % lapsed.kind,
                credentials.summarise(cli, adc),
                [
                    "Run `%s` to restore it" % lapsed.fix,
                    "Run `gcloud-axi diagnose <command>` to test a specific failing call",
                ],
            )
        if incident_count:
            return (
                "provider-incident-open",
                "both credentials are live and Google has %d open incident(s)"
                % incident_count,
                ["Run `gcloud-axi diagnose <command>` to test the call you care about"],
            )
        return (
            "credentials-healthy",
            "both credentials are live and Google publishes no open incident",
            [
                "Run `gcloud-axi diagnose <command>` to test a specific failing call",
                "Run `gcloud-axi` for ambient project and service state",
            ],
        )

    usable = [a for a in attempts if a.outcome in ("succeeded", "failed")]
    failed = [a for a in usable if a.outcome == "failed"]
    succeeded = [a for a in usable if a.outcome == "succeeded"]

    if not failed:
        return (
            "not-reproducible",
            "the command succeeded under %d identity/identities here, so whatever was "
            "seen earlier is not reproducing now" % len(succeeded),
            [
                "Re-run the original call; a failure that does not reproduce is usually "
                "transient",
                "Run `gcloud-axi diagnose <command>` again if it returns",
            ],
        )

    if succeeded:
        names = ", ".join(a.identity for a in succeeded)
        return (
            "identity-specific",
            "the same call failed as %s and succeeded as %s, so the difference is the "
            "identity, not the request or the provider"
            % (", ".join(a.identity for a in failed), names),
            [
                "Run `gcloud-axi iam audit --member <member>` to compare what each identity holds",
                "Run `gcloud-axi auth` if the failing identity is the ambient one",
                "Re-issue the original work as %s" % names.split(",")[0],
            ],
        )

    codes = set(a.code for a in failed)
    identities = ", ".join(a.identity for a in failed)
    single = len(usable) < 2

    if codes == {"PROVIDER_ERROR"}:
        if incident_count:
            return (
                "provider-outage",
                "every identity got the same server-side failure and Google has %d open "
                "incident(s) - this is the provider, not you" % incident_count,
                [
                    "Read the incident at https://status.cloud.google.com/ before "
                    "reporting a cause",
                    "Retry once the incident is marked resolved",
                ],
            )
        if feed_problem:
            # A feed the operator chose to skip is not a feed that failed; the
            # reasoning must not claim a read that was never attempted.
            if feed_problem.startswith("skipped"):
                reasoning = (
                    "every identity got the same server-side failure; the status "
                    "feed was %s, so whether an incident is open was not checked"
                    % feed_problem
                )
            else:
                reasoning = (
                    "every identity got the same server-side failure, and the "
                    "status feed could not be read to confirm whether an incident "
                    "is open"
                )
            return (
                "provider-side-status-unknown",
                reasoning,
                [
                    "Check https://status.cloud.google.com/ by hand",
                    "Retry once; a 5xx is frequently transient",
                ],
            )
        return (
            "provider-side-no-published-incident",
            "every identity got the same server-side failure but Google publishes no open "
            "incident - most likely transient, and still not a credential problem",
            [
                "Retry once before looking any further",
                "Run `gcloud-axi diagnose %s` again if it persists" % " ".join(command),
            ],
        )

    if codes == {"CREDENTIAL_EXPIRED"}:
        return (
            "all-credentials-lapsed",
            "every identity tried was rejected as expired",
            [
                "Run `%s` to restore the CLI credential" % credentials.CLI_FIX,
                "Run `%s` to restore ADC" % credentials.ADC_FIX,
                "Run `gcloud-axi auth` to confirm both are back before retrying",
            ],
        )

    if codes == {"PERMISSION_DENIED"}:
        if single:
            hints.append(
                "Run `gcloud-axi diagnose %s --as <service-account>` to test a second "
                "identity - one identity cannot tell you whether this is about identity"
                % " ".join(command)
            )
        return (
            "denied-for-every-identity-tried" if not single else "denied-single-identity",
            "the call was denied as %s%s"
            % (identities,
               " - only one identity was available, so this does not yet rule identity out"
               if single else ", so the missing permission is not specific to one of them"),
            hints + [
                "Run `gcloud-axi iam audit --member <member>` to see what an identity holds",
                "Grant the missing role on the resource, or ask an admin to",
            ],
        )

    if codes == {"NOT_FOUND"}:
        return (
            "resource-missing",
            "every identity reached the API and none found the resource, so this is about "
            "the resource or the project/region it is looked for in, not access",
            [
                "Run `gcloud-axi overview` to see what exists in this project",
                "Re-run with `--region <region>` if the resource may live elsewhere",
            ],
        )

    if single:
        hints.append(
            "Run `gcloud-axi diagnose %s --as <service-account>` to compare a second identity"
            % " ".join(command)
        )
    return (
        "inconclusive",
        "the call failed as %s with %s and nothing above distinguishes a cause"
        % (identities, ", ".join(sorted(codes))),
        hints + [
            "Run `gcloud-axi auth` to rule the credentials in or out",
            "Run the underlying `gcloud` command directly to see its full output",
        ],
    )
