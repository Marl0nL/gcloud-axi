"""`gcloud-axi` with no arguments - ambient status.

Content first: this prints live state, not help text. It must render something
useful under every condition - no config file, no tiering, an expired
credential, gcloud missing entirely - so every lookup here is best effort and
failures become fields rather than exceptions.
"""

import os

from .. import __version__, context, credentials, flags, gcloudcmd
from .. import helptext, resources, timeutil, toon

STATUS_FLAGS = {"project": flags.VALUE, "region": flags.VALUE, "no-health": flags.BOOL}


def help_out():
    return helptext.render(
        "gcloud-axi [command] [args] [flags]",
        description="Agent-ergonomic wrapper around gcloud. With no arguments it prints ambient state.",
        subcommands=[
            "(none)      - active credential, resolved project, service health",
            "auth        - probe BOTH credentials (CLI and ADC) and report which is live",
            "diagnose    - is a failing read you, your permissions, the resource, or Google?",
            "overview    - whole-project aggregate in one call",
            "run         - Cloud Run service status and revisions",
            "logs        - bounded log reads for a service or job",
            "jobs        - Cloud Run jobs; `jobs run <job>` starts one execution",
            "sql         - Cloud SQL state; `sql proxy` prints the proxy command",
            "secrets     - Secret Manager METADATA only, never a payload",
            "iam         - `iam audit`: bindings pre-joined by member",
            "builds      - recent Cloud Build runs",
            "grant       - issue a scoped short-lived credential (needs declared tiers)",
            "ledger      - read the append-only issuance log",
            "revoke      - print the revocation options for a tier; runs nothing",
        ],
        flags=[
            "--project <id>   project to act on",
            "--region <id>    region to act on",
            "--no-health      ambient status only; skip the service health probe",
            "--help           this text; every subcommand has its own",
            "-v/--version     print the version",
        ],
        notes=[
            "Project resolution, in order: the --project flag, then PROJECT in the config file, "
            "then whatever gcloud itself is configured with. There is no built-in default",
            "Region resolution follows the same shape: --region, then REGION in the config, then "
            "gcloud's run/region. With none set, region-scoped listings span all regions",
            "Config path: $GCLOUD_AXI_CONFIG, else the default under the user config directory. "
            "The file is optional - every read command works without one",
            "Credential tiering is optional and entirely declarative. `grant`, `ledger` and "
            "`revoke` explain how to configure it when no tiers are declared",
            "The CLI credential and ADC lapse independently. This view shows both but proves "
            "neither; `gcloud-axi auth` mints against each to prove which is actually live",
            "When the CLI credential is lapsed, read verbs fall back to ADC and say so in a "
            "`credentialFallback:` block. Mutating verbs never fall back",
            "This tool never reads a secret payload and never prints a token value",
        ],
        examples=[
            "gcloud-axi",
            "gcloud-axi overview --project my-project",
            "gcloud-axi run status my-service",
            "gcloud-axi logs my-service --since 1h --severity error",
            "gcloud-axi grant --tier inspect --task my-task",
        ],
    )


def dispatch(ctx_factory, argv):
    if flags.wants_help(argv):
        return help_out(), 0
    args = flags.parse(argv, STATUS_FLAGS, "", max_positional=0)
    ctx = ctx_factory(args)
    cfg = ctx.config

    out = toon.Out()
    out.field("tool", "gcloud-axi %s" % __version__)
    out.field("path", os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "gcloud-axi")))
    out.field(
        "description",
        "token-efficient, structured gcloud output for agents and automation",
    )

    # -- credential --------------------------------------------------------
    credential = context.active_credential()
    marker = context.grant_marker()
    pairs = [
        ("account", credential.get("account") or "(none active)"),
        ("type", credential.get("type")),
        ("scopedConfigDir", os.environ.get("CLOUDSDK_CONFIG")),
    ]
    if marker:
        expires = marker.get("expiresAt")
        remaining = _remaining(expires)
        pairs += [
            ("tier", marker.get("tier")),
            ("issuedFor", marker.get("task")),
            ("expiresAt", expires),
            ("expiresIn", remaining),
            ("expired", remaining == "expired"),
        ]
    else:
        pairs.append(("tier", "(not a tiered credential)"))
    if credential.get("error"):
        pairs.append(("lookupError", credential["error"]))
    out.raw("")
    out.block("credential", pairs)

    # The other credential. ADC has its own refresh state, and a machine where
    # only one of the two is healthy reads as fine from either half alone - so
    # the ambient view names both. Presence is read from disk; proving liveness
    # costs a token mint per credential, which is `gcloud-axi auth`'s job rather
    # than something every ambient render should pay for.
    adc = credentials.probe_adc(probe=False)
    out.raw("")
    out.block(
        "adc",
        [
            ("declaredType", adc.type or "(no ADC file to declare one)"),
            ("identity", adc.identity or "(not recorded)"),
            ("wouldReadFrom", adc.source),
            ("state", "%s here - run `gcloud-axi auth` to prove liveness" % adc.state),
            ("usedBy", credentials.ADC_USED_BY),
        ],
    )

    # -- project -----------------------------------------------------------
    project = ctx.project(required=False)
    out.raw("")
    out.block(
        "context",
        [
            ("project", project or "(unresolved)"),
            ("projectSource", ctx.project_source() or "(none of flag, config, gcloud)"),
            ("region", ctx.region() or "(all)"),
            ("configPath", cfg.path),
            ("configExists", cfg.exists),
            (
                "tiering",
                "%d tier(s) declared: %s" % (len(cfg.tier_names()), ", ".join(cfg.tier_names()))
                if cfg.tiering_configured()
                else "not configured (optional)",
            ),
        ],
    )

    # -- health ------------------------------------------------------------
    out.raw("")
    if args.get("no-health"):
        health = "skipped (--no-health)"
    elif not project:
        health = "unavailable - no project resolved"
    else:
        health = _health(ctx)
    out.field("health", health)

    out.raw("")
    out.help(_help_lines(cfg, project, marker, health))
    return out, 0


def _remaining(expires):
    parsed = timeutil.parse_timestamp(expires)
    if parsed is None:
        return None
    delta = int((parsed - timeutil.now()).total_seconds())
    if delta <= 0:
        return "expired"
    return timeutil.relative_seconds(delta)


def _health(ctx):
    """One line: how many Cloud Run services are ready, and how many are not."""
    result = gcloudcmd.invoke(
        ["run", "services", "list"] + ctx.region_args(),
        project=ctx.project(required=False),
    )
    if not result.ok:
        return "unavailable - %s" % getattr(result.error, "message", "unknown")
    services = [s for s in (result.data or []) if isinstance(s, dict)]
    if not services:
        return "0 Cloud Run services in scope"
    ready = [s for s in services if resources.ready_state(s) == "READY"]
    if len(ready) == len(services):
        return "%d/%d Cloud Run services READY" % (len(ready), len(services))
    unhealthy = [
        resources.service_name(s) for s in services if resources.ready_state(s) != "READY"
    ]
    return "%d/%d Cloud Run services READY; not ready: %s" % (
        len(ready),
        len(services),
        ", ".join(sorted(unhealthy)[:5]),
    )


def _help_lines(cfg, project, marker, health=None):
    lines = [
        "Run `gcloud-axi overview` for the whole-project picture",
        "Run `gcloud-axi auth` to prove which of the two credentials is actually live",
    ]
    if health and health.startswith("unavailable") and project:
        # The one read this view performs already failed, so the next question
        # is which layer failed - and that is a command, not a guess.
        lines.append(
            "Run `gcloud-axi diagnose run status` - the health probe failed, and that "
            "tells you whether it is your credential, your permissions or Google"
        )
    if not project:
        lines.append(
            "Run `gcloud-axi --project <project-id>` or set PROJECT in %s" % cfg.path
        )
    if marker and _remaining(marker.get("expiresAt")) == "expired":
        lines.append(
            "This scoped credential has expired - ask whoever issued it for a replacement"
        )
    lines.append("Run `gcloud-axi run status` for Cloud Run service detail")
    if cfg.tiering_configured():
        lines.append("Run `gcloud-axi ledger --active` to see open credential windows")
    else:
        lines.append(
            "Run `gcloud-axi grant --help` if you want the optional credential-tiering layer"
        )
    lines.append("Run `gcloud-axi --help` for the full command list")
    return lines
