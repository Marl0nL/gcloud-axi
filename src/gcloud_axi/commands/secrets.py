"""`gcloud-axi secrets` - secret METADATA only.

This command exists so that "which secrets are there, and are they wired up?"
is answerable without ever going near a payload. There is deliberately no code
path in this tool that reads a secret's value; see the note in `--help` and the
guard in ``gcloud_axi.gcloudcmd.FORBIDDEN_SEQUENCES``.
"""

from .. import flags, helptext, resources, timeutil, toon

SECRET_FLAGS = {
    "project": flags.VALUE,
    "region": flags.VALUE,
    "versions": flags.BOOL,
    "limit": flags.VALUE,
    "full": flags.BOOL,
}


def help_out():
    return helptext.render(
        "gcloud-axi secrets [name] [flags]",
        description="Secret Manager metadata: names, ages, labels, and where they are mounted.",
        flags=[
            "--project <id>   project to act on (flag > config PROJECT > gcloud's own configured project)",
            "--region <id>    region used when resolving which Cloud Run services mount a secret",
            "--versions       also list version numbers and states (one extra call per secret)",
            "--limit <n>      maximum secrets listed (default 100)",
            "--full           show labels and replication detail",
            "--help           this text",
        ],
        notes=[
            "METADATA ONLY. This command never prints a secret's value, and this tool has no "
            "subcommand or flag that can. `gcloud secrets versions access` is refused at the "
            "process boundary, not merely left unimplemented",
            "Why: agent transcripts, terminal scrollback, shell history and CI logs are all "
            "durable copies. A tool that can print a payload will eventually print one into a "
            "place nobody meant to write it. Fetch payloads from the application runtime, where "
            "they belong, and rotate anything a human or agent has read",
            "The mounted-in column comes from Cloud Run service definitions, which reference "
            "secrets by NAME only",
        ],
        examples=[
            "gcloud-axi secrets",
            "gcloud-axi secrets --versions",
            "gcloud-axi secrets my-secret --full",
        ],
    )


def dispatch(ctx_factory, argv):
    if flags.wants_help(argv):
        return help_out(), 0
    args = flags.parse(argv, SECRET_FLAGS, "secrets", max_positional=1)
    ctx = ctx_factory(args)
    name = args.positional[0] if args.positional else None
    limit = args.int("limit", default=100, minimum=1, maximum=1000)

    secrets = ctx.call(["secrets", "list", "--limit=%d" % limit]) or []
    if not isinstance(secrets, list):
        secrets = [secrets]
    if name:
        secrets = [s for s in secrets if resources.short_name(s.get("name")) == name]

    warnings = []
    mounts = _mounts(ctx, warnings)

    out = toon.Out()
    out.field("project", ctx.project())
    out.field("payloadAccess", "never - this command is metadata only")

    if not secrets:
        out.empty("secrets")
        out.note(
            "0 secrets%s in this project" % (' named "%s"' % name if name else "")
        )
        out.warnings(warnings)
        out.help(["Run `gcloud-axi secrets --help` for what this command will and will not do"])
        return out, 0

    rows = []
    for secret in secrets:
        secret_name = resources.short_name(secret.get("name"))
        created = secret.get("createTime")
        entry = {
            "name": secret_name,
            "created": timeutil.short(created),
            "age": timeutil.relative(created),
            "mountedIn": ";".join(sorted(mounts.get(secret_name, []))) or None,
        }
        if args.get("versions"):
            entry["versions"] = _versions_summary(ctx, secret_name, warnings)
        rows.append(entry)

    fields = ["name", "created", "age", "mountedIn"]
    if args.get("versions"):
        fields.append("versions")
    out.table("secrets", fields, rows)

    if args.get("full"):
        for secret in secrets:
            out.raw("")
            labels = secret.get("labels") or {}
            replication = secret.get("replication") or {}
            out.block(
                "secret",
                [
                    ("name", resources.short_name(secret.get("name"))),
                    (
                        "replication",
                        "automatic" if "automatic" in replication else "user-managed",
                    ),
                    ("expireTime", secret.get("expireTime")),
                    ("rotationNext", resources.dig(secret, "rotation", "nextRotationTime")),
                    ("labels", ";".join("%s=%s" % (k, labels[k]) for k in sorted(labels)) or None),
                ],
            )
    else:
        out.note("labels and replication omitted - use --full")
    if not args.get("versions"):
        out.note("version states omitted - use --versions (one extra call per secret)")

    out.warnings(warnings)
    out.raw("")
    out.help(
        [
            "Run `gcloud-axi secrets --versions` to see which versions are enabled",
            "Run `gcloud-axi run status <service>` to see which env var each secret backs",
        ]
    )
    return out, 0


def _versions_summary(ctx, secret_name, warnings):
    """`3 enabled, latest=7` - counts and states, never a payload."""
    result = ctx.invoke(["secrets", "versions", "list", secret_name, "--limit=50"])
    if not result.ok:
        warnings.append(
            "versions unavailable for %s: %s"
            % (secret_name, getattr(result.error, "message", "unknown"))
        )
        return None
    versions = result.data or []
    if not isinstance(versions, list) or not versions:
        return "0"
    enabled = [v for v in versions if v.get("state") == "ENABLED"]
    latest = resources.short_name((versions[0] or {}).get("name"))
    return "%d total; %d enabled; latest=%s" % (len(versions), len(enabled), latest)


def _mounts(ctx, warnings):
    """secret name -> set of Cloud Run services referencing it, by name only."""
    result = ctx.invoke(["run", "services", "list"] + ctx.region_args())
    if not result.ok:
        warnings.append(
            "mount locations unavailable: %s"
            % getattr(result.error, "message", "unknown")
        )
        return {}
    mapping = {}
    for svc in result.data or []:
        service = resources.service_name(svc)
        _, secret_env = resources.env_names(svc)
        for entry in secret_env:
            if entry.get("secret"):
                mapping.setdefault(entry["secret"], set()).add(service)
    return mapping
