"""`gcloud-axi jobs` and `gcloud-axi jobs run <job>` - Cloud Run jobs."""

from .. import flags, helptext, resources, timeutil, toon
from ..errors import UsageError

LIST_FLAGS = {
    "project": flags.VALUE,
    "region": flags.VALUE,
    "full": flags.BOOL,
    "limit": flags.VALUE,
}
RUN_FLAGS = {
    "project": flags.VALUE,
    "region": flags.VALUE,
    "args": flags.VALUE,
    "tasks": flags.VALUE,
}


def help_out():
    return helptext.render(
        "gcloud-axi jobs [run <job>] [flags]",
        description="Cloud Run jobs with their schedule and last execution result.",
        subcommands=[
            "(none)      - list every job: schedule, last execution, result",
            "run <job>   - start one execution of an existing job and return immediately",
        ],
        flags=helptext.GLOBAL_FLAGS
        + [
            "--limit <n>      executions considered per job when listing (default 50 overall)",
            "--full           include image and failure detail",
            "--args <a,b,c>   `run` only; container args override for this execution",
            "--tasks <n>      `run` only; task count override for this execution",
        ],
        notes=[
            "`jobs run` creates a new execution and never edits or deletes the job itself",
            "`jobs run` does not wait; poll with `gcloud-axi jobs` or read `gcloud-axi logs <job>`",
            "Schedules are read from Cloud Scheduler, which is a per-location API: a resolved "
            "region is required to query it. Without one - or when the API is unreachable - the "
            "schedule column is null and a warning says which of the two it was",
        ],
        examples=[
            "gcloud-axi jobs",
            "gcloud-axi jobs --full --project my-project",
            "gcloud-axi jobs run my-job",
        ],
    )


def dispatch(ctx_factory, argv):
    if argv and argv[0] == "run":
        return run_job(ctx_factory, argv[1:])
    return list_jobs(ctx_factory, argv)


def schedules(ctx, warnings):
    """Map job name -> Cloud Scheduler schedule, joined strictly on the target URI.

    Only an HTTP target addressing ``.../jobs/<name>[:run]`` names a Cloud Run
    job. Anything else - Pub/Sub targets, unrecognised URIs - yields no entry,
    so the job's schedule column stays an honest null rather than a guess.

    Cloud Scheduler is a per-location API: `gcloud scheduler jobs list` refuses
    to run without ``--location``. With no region resolvable there is nothing to
    query, so this says so rather than issuing a call that cannot succeed.
    """
    region = ctx.region()
    if not region:
        warnings.append(
            "schedules unavailable: Cloud Scheduler is queried per location and no "
            "region is resolvable - pass --region, or set REGION in the config"
        )
        return {}
    result = ctx.invoke(["scheduler", "jobs", "list", "--location=%s" % region])
    if not result.ok:
        warnings.append(
            "schedules unavailable: %s" % getattr(result.error, "message", "unknown")
        )
        return {}
    mapping = {}
    for entry in result.data or []:
        if not isinstance(entry, dict):
            continue
        name = _target_job(resources.dig(entry, "httpTarget", "uri"))
        if name and name not in mapping:
            mapping[name] = {
                "schedule": entry.get("schedule"),
                "state": entry.get("state"),
                "scheduler": resources.short_name(entry.get("name")),
            }
    return mapping


def _target_job(uri):
    """The job name in a Cloud Run trigger URI (``.../jobs/<name>[:run]``), or None."""
    if not uri:
        return None
    path = str(uri).split("?", 1)[0]
    if "://" in path:
        path = path.split("://", 1)[1]
    segments = path.split("/")
    for i in range(len(segments) - 1):
        if segments[i] == "jobs":
            return segments[i + 1].split(":", 1)[0] or None
    return None


def latest_executions(ctx, warnings, limit):
    result = ctx.invoke(
        ["run", "jobs", "executions", "list", "--limit=%d" % limit]
        + ctx.region_args()
    )
    if not result.ok:
        warnings.append(
            "executions unavailable: %s" % getattr(result.error, "message", "unknown")
        )
        return {}
    latest = {}
    for execution in result.data or []:
        name = resources.execution_job_name(execution)
        if not name:
            continue
        started = resources.execution_started(execution)
        current = latest.get(name)
        if current is None or (started or "") > (current.get("_started") or ""):
            latest[name] = {
                "_started": started,
                "execution": resources.execution_name(execution),
                "started": started,
                "completed": resources.execution_completed(execution),
                "result": resources.execution_result(execution),
            }
    return latest


def list_jobs(ctx_factory, argv):
    if flags.wants_help(argv):
        return help_out(), 0
    args = flags.parse(argv, LIST_FLAGS, "jobs", max_positional=0)
    ctx = ctx_factory(args)
    limit = args.int("limit", default=50, minimum=1, maximum=1000)

    jobs = ctx.call(["run", "jobs", "list"] + ctx.region_args()) or []
    if not isinstance(jobs, list):
        jobs = [jobs]

    out = toon.Out()
    out.field("project", ctx.project())
    if not jobs:
        out.empty("jobs")
        out.note("0 Cloud Run jobs in this project%s" % _region_suffix(ctx))
        out.help(
            [
                "Run `gcloud-axi jobs --region <region>` if jobs live in another region",
                "Run `gcloud-axi overview` for the whole-project picture",
            ]
        )
        return out, 0

    warnings = []
    sched_map = schedules(ctx, warnings)
    executions = latest_executions(ctx, warnings, limit)

    rows = []
    for job in jobs:
        name = resources.job_name(job)
        sched = sched_map.get(name) or {}
        last = executions.get(name) or {}
        rows.append(
            {
                "name": name,
                "schedule": sched.get("schedule"),
                "lastRun": timeutil.short(last.get("started")),
                "age": timeutil.relative(last.get("started")),
                "result": last.get("result") or "NEVER_RUN",
                "state": resources.ready_state(job),
            }
        )
    out.table(
        "jobs", ["name", "schedule", "lastRun", "age", "result", "state"], rows
    )

    if args.get("full"):
        for job in jobs:
            name = resources.job_name(job)
            last = executions.get(name) or {}
            image = resources.job_image(job)
            out.raw("")
            out.block(
                "job",
                [
                    ("name", name),
                    ("region", resources.job_region(job)),
                    ("image", resources.image_repo(image)),
                    ("imageDigest", resources.image_digest(image)),
                    ("lastExecution", last.get("execution")),
                    ("lastCompleted", timeutil.short(last.get("completed"))),
                    ("statusDetail", resources.ready_message(job)),
                    ("scheduler", (sched_map.get(name) or {}).get("scheduler")),
                    ("schedulerState", (sched_map.get(name) or {}).get("state")),
                ],
            )
    else:
        out.note("images, execution ids and scheduler detail omitted - use --full")

    out.warnings(warnings)
    out.raw("")
    out.help(
        [
            "Run `gcloud-axi jobs run <job>` to start one execution",
            "Run `gcloud-axi logs <job> --since 6h` to read a job's output",
            "Run `gcloud-axi jobs --full` for images and execution ids",
        ]
    )
    return out, 0


def _region_suffix(ctx):
    region = ctx.region()
    return " in region %s" % region if region else " (all regions)"


def run_job(ctx_factory, argv):
    if flags.wants_help(argv):
        return help_out(), 0
    args = flags.parse(argv, RUN_FLAGS, "jobs run", max_positional=1)
    if not args.positional:
        raise UsageError(
            "`jobs run` needs the name of an existing job",
            code="MISSING_ARGUMENT",
            help_lines=[
                "Run `gcloud-axi jobs` to list job names",
                "Run `gcloud-axi jobs run <job>`",
            ],
        )
    ctx = ctx_factory(args)
    name = args.positional[0]

    call = ["run", "jobs", "execute", name] + ctx.region_args()
    if args.has("args"):
        call.append("--args=%s" % args.get("args"))
    if args.has("tasks"):
        call.append("--tasks=%d" % args.int("tasks", minimum=1, maximum=10000))

    execution = ctx.call(call)
    execution_name = None
    if isinstance(execution, dict):
        execution_name = resources.execution_name(execution)

    out = toon.Out()
    out.block(
        "started",
        [
            ("job", name),
            ("project", ctx.project()),
            ("region", ctx.region()),
            ("execution", execution_name),
            ("state", resources.ready_state(execution) if execution else "UNKNOWN"),
            ("waited", False),
        ],
    )
    out.note("a new execution was created; the job definition was not modified")
    out.help(
        [
            "Run `gcloud-axi jobs` to see this execution's result once it finishes",
            "Run `gcloud-axi logs %s --since 15m` to read its output" % name,
        ]
    )
    return out, 0
