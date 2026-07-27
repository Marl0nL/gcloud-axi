"""`gcloud-axi run status|revisions` - Cloud Run services."""

from .. import flags, helptext, resources, timeutil, toon
from ..errors import NotFoundError, UsageError

COMMON = {"project": flags.VALUE, "region": flags.VALUE, "full": flags.BOOL}

STATUS_FLAGS = dict(COMMON)
REVISION_FLAGS = dict(COMMON, limit=flags.VALUE)


def help_out():
    return helptext.render(
        "gcloud-axi run <subcommand> [service] [flags]",
        description="Cloud Run service state: what is serving, on which image, since when.",
        subcommands=[
            "status [service]     - traffic split, serving revision, image digest, env/secret NAMES",
            "revisions [service]  - recent revisions with created time, traffic share and status",
        ],
        flags=helptext.GLOBAL_FLAGS
        + [
            "--limit <n>      revisions only; how many to list (default 10)",
            "--full           show every field without truncation",
        ],
        notes=[
            "Environment variables are reported by NAME only - this tool never reads config or secret values",
            "With no [service] argument every service in scope is listed",
        ],
        examples=[
            "gcloud-axi run status",
            "gcloud-axi run status my-service --project my-project",
            "gcloud-axi run revisions my-service --limit 5",
        ],
    )


def dispatch(ctx_factory, argv):
    if not argv or flags.wants_help(argv[:1]):
        return help_out(), 0
    sub, rest = argv[0], argv[1:]
    if sub == "status":
        return status(ctx_factory, rest)
    if sub == "revisions":
        return revisions(ctx_factory, rest)
    raise UsageError(
        'unknown subcommand "run %s"' % sub,
        code="UNKNOWN_SUBCOMMAND",
        help_lines=["Run `gcloud-axi run --help` to see the subcommands"],
    )


def list_services(ctx, name=None):
    args = ["run", "services", "list"] + ctx.region_args()
    services = ctx.call(args) or []
    if not isinstance(services, list):
        services = [services]
    if name:
        services = [s for s in services if resources.service_name(s) == name]
    return services


def status(ctx_factory, argv):
    if flags.wants_help(argv):
        return help_out(), 0
    args = flags.parse(argv, STATUS_FLAGS, "run status", max_positional=1)
    ctx = ctx_factory(args)
    name = args.positional[0] if args.positional else None

    services = list_services(ctx, name)
    out = toon.Out()
    out.field("project", ctx.project())

    if not services:
        if name:
            raise NotFoundError(
                'no Cloud Run service named "%s" in project %s' % (name, ctx.project()),
                help_lines=[
                    "Run `gcloud-axi run status` to list every service in scope",
                    "Run `gcloud-axi run status --region <region>` if it lives elsewhere",
                ],
            )
        out.empty("services")
        out.help(
            [
                "Run `gcloud-axi overview` for the whole-project picture",
                "Run `gcloud-axi run status --region <region>` to look in a specific region",
            ]
        )
        return out, 0

    out.field("count", len(services))
    for svc in services:
        svc_name = resources.service_name(svc)
        image = resources.service_image(svc)
        plain_env, secret_env = resources.env_names(svc)
        last_deploy = resources.service_last_deploy(svc)
        out.raw("")
        out.block(
            "service",
            [
                ("name", svc_name),
                ("region", resources.service_region(svc)),
                ("status", resources.ready_state(svc)),
                ("statusDetail", resources.ready_message(svc)),
                ("url", resources.service_url(svc)),
                ("servingRevision", resources.serving_revision(svc)),
                ("image", resources.image_repo(image)),
                ("imageDigest", resources.image_digest(image)),
                ("serviceAccount", resources.service_account_of(svc)),
                ("lastDeploy", timeutil.short(last_deploy)),
                ("lastDeployAge", timeutil.relative(last_deploy)),
                ("lastModifier", resources.service_last_modifier(svc)),
            ],
        )
        traffic = resources.service_traffic(svc)
        if traffic:
            out.table(
                "traffic",
                ["revision", "percent", "tag"],
                [
                    {
                        "revision": t["revision"],
                        "percent": t["percent"],
                        "tag": t["tag"],
                    }
                    for t in traffic
                ],
                indent=1,
            )
        else:
            out.empty("traffic", indent=1)

        _emit_names(out, "envNames", plain_env, args.get("full"))
        if secret_env:
            out.table(
                "envFromSecrets",
                ["env", "secret", "version"],
                secret_env,
                indent=1,
            )
        else:
            out.empty("envFromSecrets", indent=1)

    out.raw("")
    out.help(
        [
            "Run `gcloud-axi run revisions <service>` to see revision history",
            "Run `gcloud-axi logs <service> --since 1h --severity error` for recent failures",
            "Run `gcloud-axi secrets` for secret metadata (names and versions only)",
        ]
    )
    return out, 0


def _emit_names(out, label, names, full):
    if not names:
        out.empty(label, indent=1)
        return
    shown = names if full else names[:20]
    out.raw("  %s[%d]:" % (label, len(names)))
    for item in shown:
        out.raw("    " + toon.scalar(item))
    if len(shown) < len(names):
        out.raw(
            "    (truncated, %d names total - use --full for all)" % len(names)
        )


def revisions(ctx_factory, argv):
    if flags.wants_help(argv):
        return help_out(), 0
    args = flags.parse(argv, REVISION_FLAGS, "run revisions", max_positional=1)
    ctx = ctx_factory(args)
    limit = args.int("limit", default=10, minimum=1, maximum=500)
    name = args.positional[0] if args.positional else None

    call = ["run", "revisions", "list", "--limit=%d" % limit] + ctx.region_args()
    if name:
        call.append("--service=%s" % name)
    revs = ctx.call(call) or []
    if not isinstance(revs, list):
        revs = [revs]

    traffic_by_revision = {}
    if name:
        for svc in list_services(ctx, name):
            for entry in resources.service_traffic(svc):
                if entry.get("revision"):
                    traffic_by_revision[entry["revision"]] = entry.get("percent")

    out = toon.Out()
    out.field("project", ctx.project())
    out.field("service", name or "(all)")
    if not revs:
        out.empty("revisions")
        out.note(
            "0 revisions returned for %s - the service may not exist, or may live in another region"
            % (name or "this project")
        )
        out.help(
            [
                "Run `gcloud-axi run status` to list services in scope",
                "Run `gcloud-axi run revisions <service> --region <region>` to widen the search",
            ]
        )
        return out, 0

    rows = []
    for rev in revs:
        rev_name = resources.revision_name(rev)
        created = resources.revision_created(rev)
        rows.append(
            {
                "name": rev_name,
                "created": timeutil.short(created),
                "age": timeutil.relative(created),
                "traffic": traffic_by_revision.get(rev_name, 0 if name else None),
                "status": resources.ready_state(rev),
            }
        )
    out.table("revisions", ["name", "created", "age", "traffic", "status"], rows)
    if args.get("full"):
        for rev in revs:
            out.raw("")
            out.block(
                "revision",
                [
                    ("name", resources.revision_name(rev)),
                    ("image", resources.revision_image(rev)),
                    ("digest", resources.image_digest(resources.revision_image(rev))),
                    ("statusDetail", resources.ready_message(rev)),
                ],
            )
    else:
        out.note("images and status detail omitted - use --full")
    out.raw("")
    out.help(
        [
            "Run `gcloud-axi run revisions <service> --full` for images and failure detail",
            "Run `gcloud-axi logs <service> --since 1h` for what the current revision is doing",
        ]
    )
    return out, 0
