"""`gcloud-axi overview` - the aggregate.

One call answering what would otherwise take half a dozen `describe`/`list`
invocations: what is serving, on which image, which jobs ran and how they went,
what state the databases are in, and how noisy the last hour was.

Every section is independent. A section that cannot be read becomes a warning
and the rest still renders; the command only fails outright when nothing at all
could be read.
"""

from .. import flags, helptext, resources, timeutil, toon
from ..commands import jobs as jobs_cmd

OVERVIEW_FLAGS = {
    "project": flags.VALUE,
    "region": flags.VALUE,
    "errors-since": flags.VALUE,
    "no-errors": flags.BOOL,
    "full": flags.BOOL,
}

ERROR_SCAN_LIMIT = 1000


def help_out():
    return helptext.render(
        "gcloud-axi overview [flags]",
        description="Whole-project state in one call: services, jobs, databases, recent error volume.",
        flags=helptext.GLOBAL_FLAGS
        + [
            "--errors-since <dur>  window for the error count, e.g. 30m, 1h, 24h (default 1h)",
            "--no-errors           skip the log scan (the most expensive section)",
            "--full                include image digests and per-section detail",
        ],
        notes=[
            "Sections degrade independently: an API you cannot reach becomes a warning line, "
            "not a failed command",
            "The error count is capped at %d entries; a capped count is reported as such rather "
            "than silently understated" % ERROR_SCAN_LIMIT,
        ],
        examples=[
            "gcloud-axi overview",
            "gcloud-axi overview --project my-project --errors-since 24h",
            "gcloud-axi overview --no-errors --full",
        ],
    )


def dispatch(ctx_factory, argv):
    if flags.wants_help(argv):
        return help_out(), 0
    args = flags.parse(argv, OVERVIEW_FLAGS, "overview", max_positional=0)
    ctx = ctx_factory(args)
    full = bool(args.get("full"))

    warnings = []
    sections_ok = 0
    out = toon.Out()
    out.block(
        "context",
        [
            ("project", ctx.project()),
            ("projectSource", ctx.project_source()),
            ("region", ctx.region() or "(all)"),
            ("generated", timeutil.rfc3339(timeutil.now())),
        ],
    )

    # -- Cloud Run services ------------------------------------------------
    out.raw("")
    result = ctx.invoke(["run", "services", "list"] + ctx.region_args())
    if not result.ok:
        warnings.append("services: %s" % result.error.message)
        out.field("services", "unavailable")
    else:
        sections_ok += 1
        services = [s for s in (result.data or []) if isinstance(s, dict)]
        if not services:
            out.empty("services")
        else:
            rows = []
            for svc in services:
                image = resources.service_image(svc)
                deploy = resources.service_last_deploy(svc)
                row = {
                    "name": resources.service_name(svc),
                    "status": resources.ready_state(svc),
                    "servingRevision": resources.serving_revision(svc),
                    "digest": _short_digest(resources.image_digest(image), full),
                    "lastDeploy": timeutil.relative(deploy) or timeutil.short(deploy),
                }
                rows.append(row)
            out.table(
                "services",
                ["name", "status", "servingRevision", "digest", "lastDeploy"],
                rows,
            )

    # -- Cloud Run jobs ----------------------------------------------------
    out.raw("")
    result = ctx.invoke(["run", "jobs", "list"] + ctx.region_args())
    if not result.ok:
        warnings.append("jobs: %s" % result.error.message)
        out.field("jobs", "unavailable")
    else:
        sections_ok += 1
        job_list = [j for j in (result.data or []) if isinstance(j, dict)]
        if not job_list:
            out.empty("jobs")
        else:
            sched_map = jobs_cmd.schedules(ctx, warnings)
            executions = jobs_cmd.latest_executions(ctx, warnings, 50)
            rows = []
            for job in job_list:
                name = resources.job_name(job)
                last = executions.get(name) or {}
                rows.append(
                    {
                        "name": name,
                        "schedule": (sched_map.get(name) or {}).get("schedule"),
                        "lastRun": timeutil.relative(last.get("started")),
                        "result": last.get("result") or "NEVER_RUN",
                    }
                )
            out.table("jobs", ["name", "schedule", "lastRun", "result"], rows)

    # -- Cloud SQL ---------------------------------------------------------
    out.raw("")
    result = ctx.invoke(["sql", "instances", "list"])
    if not result.ok:
        warnings.append("sql: %s" % result.error.message)
        out.field("sql", "unavailable")
    else:
        sections_ok += 1
        instances = [i for i in (result.data or []) if isinstance(i, dict)]
        if not instances:
            out.empty("sql")
        else:
            out.table(
                "sql",
                ["name", "state", "databaseVersion", "tier", "availability"],
                [
                    {
                        "name": i.get("name"),
                        "state": i.get("state"),
                        "databaseVersion": i.get("databaseVersion"),
                        "tier": resources.dig(i, "settings", "tier"),
                        "availability": resources.dig(i, "settings", "availabilityType"),
                    }
                    for i in instances
                ],
            )

    # -- error volume ------------------------------------------------------
    out.raw("")
    if args.get("no-errors"):
        out.field("errors", "skipped (--no-errors)")
    else:
        window = args.get("errors-since", "1h")
        seconds = timeutil.parse_duration(window, flag="errors-since")
        log_filter = 'severity>=ERROR AND timestamp>="%s"' % timeutil.ago(seconds)
        result = ctx.invoke(
            [
                "logging",
                "read",
                log_filter,
                "--limit=%d" % ERROR_SCAN_LIMIT,
                "--order=desc",
            ]
        )
        if not result.ok:
            warnings.append("errors: %s" % result.error.message)
            out.field("errors", "unavailable")
        else:
            sections_ok += 1
            entries = [e for e in (result.data or []) if isinstance(e, dict)]
            by_resource = {}
            for entry in entries:
                key = (
                    resources.dig(entry, "resource", "labels", "service_name")
                    or resources.dig(entry, "resource", "labels", "job_name")
                    or resources.dig(entry, "resource", "type")
                    or "(unlabelled)"
                )
                by_resource[key] = by_resource.get(key, 0) + 1
            out.block(
                "errors",
                [
                    ("window", window),
                    ("count", len(entries)),
                    ("capped", len(entries) >= ERROR_SCAN_LIMIT),
                    ("sources", len(by_resource)),
                ],
            )
            if by_resource:
                out.table(
                    "errorsBySource",
                    ["source", "count"],
                    sorted(
                        [{"source": k, "count": v} for k, v in by_resource.items()],
                        key=lambda r: -r["count"],
                    )[: (None if full else 5)],
                    indent=1,
                )
            else:
                out.empty("errorsBySource", indent=1)

    out.raw("")
    out.warnings(warnings)
    if not full:
        out.note("image digests shortened and error sources capped at 5 - use --full")

    if sections_ok == 0:
        from ..errors import AxiError

        raise AxiError(
            "no section of the overview could be read for project %s" % ctx.project(),
            code="OVERVIEW_EMPTY",
            help_lines=[
                "Run `gcloud-axi` to check which credential is active",
                "Run `gcloud-axi overview --project <project-id>` if the project is wrong",
            ],
            fields=[("firstWarning", warnings[0] if warnings else None)],
        )

    out.help(
        [
            "Run `gcloud-axi run status <service>` for one service in detail",
            "Run `gcloud-axi logs <service> --since 1h --severity error` for the failures behind the count",
            "Run `gcloud-axi builds --limit 10` for recent build history",
        ]
    )
    return out, 0


def _short_digest(digest, full):
    if not digest:
        return None
    if full:
        return digest
    body = str(digest).split(":", 1)[-1]
    return "sha256:%s" % body[:12]
