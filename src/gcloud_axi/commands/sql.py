"""`gcloud-axi sql status|proxy` - Cloud SQL instance state, and a printed
proxy invocation the tool never runs itself."""

from .. import flags, helptext, resources, toon
from ..errors import NotFoundError, UsageError

STATUS_FLAGS = {"project": flags.VALUE, "full": flags.BOOL}
PROXY_FLAGS = {
    "project": flags.VALUE,
    "port": flags.VALUE,
    "binary": flags.VALUE,
}


def help_out():
    return helptext.render(
        "gcloud-axi sql <subcommand> [instance] [flags]",
        description="Cloud SQL instance state, and the exact local proxy command to run.",
        subcommands=[
            "status [instance]  - state, version, tier, availability, backups, flags",
            "proxy [instance]   - PRINT the cloud-sql-proxy invocation; runs nothing",
        ],
        flags=[
            "--project <id>   project to act on (flag > config PROJECT > gcloud's own configured project)",
            "--full           status only; include every database flag and authorized network",
            "--port <n>       proxy only; local port to print in the invocation (default 5432)",
            "--binary <path>  proxy only; proxy binary name to print (default cloud-sql-proxy)",
            "--help           this text",
        ],
        notes=[
            "`sql proxy` starts no process and opens no connection - it prints a command for you to run",
            "Database credentials are never read, stored or printed by this tool",
        ],
        examples=[
            "gcloud-axi sql status",
            "gcloud-axi sql status my-instance --full",
            "gcloud-axi sql proxy my-instance --port 6543",
        ],
    )


def dispatch(ctx_factory, argv):
    if not argv or flags.wants_help(argv[:1]):
        return help_out(), 0
    sub, rest = argv[0], argv[1:]
    if sub == "status":
        return status(ctx_factory, rest)
    if sub == "proxy":
        return proxy(ctx_factory, rest)
    raise UsageError(
        'unknown subcommand "sql %s"' % sub,
        code="UNKNOWN_SUBCOMMAND",
        help_lines=["Run `gcloud-axi sql --help` to see the subcommands"],
    )


def _instances(ctx, name=None):
    instances = ctx.call(["sql", "instances", "list"]) or []
    if not isinstance(instances, list):
        instances = [instances]
    if name:
        instances = [i for i in instances if i.get("name") == name]
    return instances


def _ip_addresses(instance):
    rows = []
    for entry in resources.dig(instance, "ipAddresses", default=[]) or []:
        if isinstance(entry, dict):
            rows.append({"type": entry.get("type"), "address": entry.get("ipAddress")})
    return rows


def status(ctx_factory, argv):
    if flags.wants_help(argv):
        return help_out(), 0
    args = flags.parse(argv, STATUS_FLAGS, "sql status", max_positional=1)
    ctx = ctx_factory(args)
    name = args.positional[0] if args.positional else None
    instances = _instances(ctx, name)

    out = toon.Out()
    out.field("project", ctx.project())
    if not instances:
        if name:
            raise NotFoundError(
                'no Cloud SQL instance named "%s" in project %s' % (name, ctx.project()),
                help_lines=["Run `gcloud-axi sql status` to list every instance"],
            )
        out.empty("instances")
        out.note("0 Cloud SQL instances in this project")
        out.help(["Run `gcloud-axi overview` for the whole-project picture"])
        return out, 0

    out.field("count", len(instances))
    for instance in instances:
        settings = instance.get("settings") or {}
        ip_config = settings.get("ipConfiguration") or {}
        backup = settings.get("backupConfiguration") or {}
        db_flags = settings.get("databaseFlags") or []
        networks = ip_config.get("authorizedNetworks") or []
        out.raw("")
        out.block(
            "instance",
            [
                ("name", instance.get("name")),
                ("state", instance.get("state")),
                ("databaseVersion", instance.get("databaseVersion")),
                ("tier", settings.get("tier")),
                ("region", instance.get("region")),
                ("availability", settings.get("availabilityType")),
                ("diskGb", settings.get("dataDiskSizeGb")),
                ("connectionName", instance.get("connectionName")),
                ("publicIpEnabled", bool(ip_config.get("ipv4Enabled"))),
                ("privateNetwork", ip_config.get("privateNetwork")),
                ("requireSsl", ip_config.get("requireSsl")),
                ("backupsEnabled", bool(backup.get("enabled"))),
                ("authorizedNetworks", len(networks)),
                ("databaseFlags", len(db_flags)),
            ],
        )
        addresses = _ip_addresses(instance)
        if addresses:
            out.table("addresses", ["type", "address"], addresses, indent=1)
        else:
            out.empty("addresses", indent=1)

        if args.get("full"):
            if db_flags:
                out.table(
                    "flags",
                    ["name", "value"],
                    [
                        {"name": f.get("name"), "value": f.get("value")}
                        for f in db_flags
                        if isinstance(f, dict)
                    ],
                    indent=1,
                )
            else:
                out.empty("flags", indent=1)
            if networks:
                out.table(
                    "networks",
                    ["name", "value"],
                    [
                        {"name": n.get("name"), "value": n.get("value")}
                        for n in networks
                        if isinstance(n, dict)
                    ],
                    indent=1,
                )
            else:
                out.empty("networks", indent=1)

    if not args.get("full"):
        out.note("database flags and authorized networks counted only - use --full to list them")
    out.raw("")
    out.help(
        [
            "Run `gcloud-axi sql proxy <instance>` to get the local proxy command",
            "Run `gcloud-axi sql status <instance> --full` for flags and networks",
        ]
    )
    return out, 0


def _default_port(database_version):
    text = str(database_version or "").upper()
    if text.startswith("POSTGRES"):
        return 5432
    if text.startswith("SQLSERVER"):
        return 1433
    if text.startswith("MYSQL"):
        return 3306
    return 5432


def proxy(ctx_factory, argv):
    if flags.wants_help(argv):
        return help_out(), 0
    args = flags.parse(argv, PROXY_FLAGS, "sql proxy", max_positional=1)
    ctx = ctx_factory(args)
    name = args.positional[0] if args.positional else None
    instances = _instances(ctx, name)

    if not instances:
        raise NotFoundError(
            'no Cloud SQL instance %s in project %s'
            % ('named "%s"' % name if name else "found", ctx.project()),
            help_lines=["Run `gcloud-axi sql status` to list every instance"],
        )
    if len(instances) > 1 and not name:
        out = toon.Out()
        out.field("project", ctx.project())
        out.table(
            "instances",
            ["name", "state", "connectionName"],
            [
                {
                    "name": i.get("name"),
                    "state": i.get("state"),
                    "connectionName": i.get("connectionName"),
                }
                for i in instances
            ],
        )
        out.note("more than one instance - name the one you want")
        out.help(["Run `gcloud-axi sql proxy <instance>` to get its proxy command"])
        return out, 0

    instance = instances[0]
    connection = instance.get("connectionName")
    binary = args.get("binary", "cloud-sql-proxy")
    port = args.int("port", default=_default_port(instance.get("databaseVersion")),
                    minimum=1, maximum=65535)

    out = toon.Out()
    out.block(
        "proxy",
        [
            ("instance", instance.get("name")),
            ("connectionName", connection),
            ("databaseVersion", instance.get("databaseVersion")),
            ("localPort", port),
            ("started", False),
        ],
    )
    out.list_lines(
        "command",
        ["%s --port %d %s" % (binary, port, connection)],
    )
    out.list_lines(
        "then",
        [
            "connect your client to 127.0.0.1:%d" % port,
            "the proxy authenticates with the credential active in your environment",
        ],
    )
    out.note("nothing was started - this command is printed for you to run")
    out.help(
        [
            "Run `gcloud-axi sql status %s` for instance state" % instance.get("name"),
            "Run `%s --help` for proxy options such as --auto-iam-authn" % binary,
        ]
    )
    return out, 0
