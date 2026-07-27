"""`gcloud-axi builds` - recent Cloud Build runs."""

from .. import flags, helptext, resources, timeutil, toon

BUILD_FLAGS = {
    "project": flags.VALUE,
    "limit": flags.VALUE,
    "status": flags.VALUE,
    "full": flags.BOOL,
}

FAILED_STATES = ("FAILURE", "INTERNAL_ERROR", "TIMEOUT", "CANCELLED", "EXPIRED")


def help_out():
    return helptext.render(
        "gcloud-axi builds [flags]",
        description="Recent Cloud Build runs with status, duration and trigger.",
        flags=[
            "--project <id>   project to act on (flag > config PROJECT > gcloud's own configured project)",
            "--limit <n>      how many builds to list (default 5, max 200)",
            "--status <s>     only builds in this status, e.g. success, failure, working",
            "--full           include the source reference and log URL",
            "--help           this text",
        ],
        notes=[
            "The result carries a pre-computed pass/fail summary so the common question - "
            "'is the last build green?' - needs no second call",
        ],
        examples=[
            "gcloud-axi builds",
            "gcloud-axi builds --limit 20 --status failure",
            "gcloud-axi builds --full",
        ],
    )


def _trigger(build):
    return (
        resources.dig(build, "substitutions", "TRIGGER_NAME")
        or build.get("buildTriggerId")
        or resources.dig(build, "source", "repoSource", "repoName")
        or "(manual)"
    )


def _source(build):
    ref = (
        resources.dig(build, "substitutions", "REF_NAME")
        or resources.dig(build, "source", "repoSource", "branchName")
        or resources.dig(build, "source", "repoSource", "tagName")
    )
    sha = resources.dig(build, "substitutions", "SHORT_SHA") or resources.dig(
        build, "source", "repoSource", "commitSha"
    )
    parts = [p for p in (ref, (str(sha)[:12] if sha else None)) if p]
    return "@".join(parts) if parts else None


def dispatch(ctx_factory, argv):
    if flags.wants_help(argv):
        return help_out(), 0
    args = flags.parse(argv, BUILD_FLAGS, "builds", max_positional=0)
    ctx = ctx_factory(args)
    limit = args.int("limit", default=5, minimum=1, maximum=200)

    call = ["builds", "list", "--limit=%d" % limit]
    status_filter = args.get("status")
    if status_filter:
        call.append("--filter=status=%s" % status_filter.upper())

    builds = ctx.call(call) or []
    if not isinstance(builds, list):
        builds = [builds]

    out = toon.Out()
    out.field("project", ctx.project())
    out.field("limit", limit)
    if status_filter:
        out.field("statusFilter", status_filter.upper())

    if not builds:
        out.empty("builds")
        out.note(
            "0 builds returned%s - Cloud Build has no matching history in this project"
            % (' with status %s' % status_filter.upper() if status_filter else "")
        )
        out.help(
            [
                "Run `gcloud-axi builds --limit 50` to look further back",
                "Run `gcloud-axi builds` without --status to see every recent build",
            ]
        )
        return out, 0

    rows = []
    failures = 0
    for build in builds:
        status = build.get("status")
        if status in FAILED_STATES:
            failures += 1
        rows.append(
            {
                "id": str(build.get("id") or "")[:12],
                "status": status,
                "started": timeutil.short(build.get("startTime") or build.get("createTime")),
                "duration": timeutil.duration_between(
                    build.get("startTime"), build.get("finishTime")
                ),
                "trigger": _trigger(build),
            }
        )

    out.block(
        "summary",
        [
            ("total", len(rows)),
            ("failed", failures),
            ("latest", rows[0]["status"] if rows else None),
        ],
    )
    out.table("builds", ["id", "status", "started", "duration", "trigger"], rows)

    if args.get("full"):
        for build in builds:
            out.raw("")
            out.block(
                "build",
                [
                    ("id", build.get("id")),
                    ("status", build.get("status")),
                    ("source", _source(build)),
                    ("logUrl", build.get("logUrl")),
                    ("statusDetail", build.get("statusDetail")),
                ],
            )
    else:
        out.note("source refs and log URLs omitted - use --full")

    out.raw("")
    out.help(
        [
            "Run `gcloud-axi builds --status failure --limit 20` to isolate failures",
            "Run `gcloud-axi builds --full` for source refs and log URLs",
            "Run `gcloud-axi run revisions <service>` to see what actually got deployed",
        ]
    )
    return out, 0
