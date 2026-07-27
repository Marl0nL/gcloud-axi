"""`gcloud-axi ledger` - read the append-only issuance log.

The log is append-only by construction: `grant` is the only writer and it only
ever appends. This tool ships no subcommand that edits or deletes a line, on
purpose - a record you can rewrite answers no question worth asking.
"""

import os

from .. import config as config_mod, flags, helptext, tiering, timeutil, toon

LEDGER_FLAGS = {
    "task": flags.VALUE,
    "tier": flags.VALUE,
    "active": flags.BOOL,
    "limit": flags.VALUE,
    "full": flags.BOOL,
}


def help_out():
    return helptext.render(
        "gcloud-axi ledger [flags]",
        description="Every credential this tool has issued: task, tier, service account, window.",
        flags=[
            "--task <slug>   only issuances for this task",
            "--tier <name>   only issuances of this tier",
            "--active        only issuances whose window has not closed yet",
            "--limit <n>     most recent N records (default 50, max 1000)",
            "--full          include reason, config directory and issuing tool version",
            "--help          this text",
        ],
        notes=[
            "Append-only: `grant` appends, nothing edits or deletes. There is deliberately no "
            "subcommand that could",
            "No token value is ever recorded here - the ledger answers who held what, when, and "
            "why, not what the secret was",
            "`active` is computed from the recorded lifetime. It says the window is still open, "
            "not that the credential was never revoked out of band",
            "Ledger path: $GCLOUD_AXI_LEDGER, else LEDGER in the config, else the default under "
            "the user config directory",
        ],
        examples=[
            "gcloud-axi ledger",
            "gcloud-axi ledger --active",
            "gcloud-axi ledger --task my-task --full",
        ],
    )


def dispatch(ctx_factory, argv):
    if flags.wants_help(argv):
        return help_out(), 0
    args = flags.parse(argv, LEDGER_FLAGS, "ledger", max_positional=0)
    cfg = config_mod.load()
    path = config_mod.ledger_path(cfg)
    limit = args.int("limit", default=50, minimum=1, maximum=1000)

    records, malformed = tiering.read_ledger(path)
    total = len(records)

    if args.get("task"):
        records = [r for r in records if r.get("task") == args.get("task")]
    if args.get("tier"):
        wanted = args.get("tier").lower()
        records = [r for r in records if str(r.get("tier", "")).lower() == wanted]
    if args.get("active"):
        records = [r for r in records if tiering.is_active(r) is True]

    matched = len(records)
    records = records[-limit:]
    records.reverse()

    out = toon.Out()
    out.block(
        "ledger",
        [
            ("path", path),
            ("exists", os.path.isfile(path)),
            ("totalRecords", total),
            ("matched", matched),
            ("shown", len(records)),
            ("appendOnly", True),
            ("tokensRecorded", "never"),
        ],
    )

    if not records:
        out.empty("issuances")
        if total == 0:
            out.note(
                "0 issuances recorded - nothing has been granted through this ledger yet"
            )
        else:
            out.note(
                "0 of %d records matched the given filters" % total
            )
        if malformed:
            out.warnings(["%d unparseable line(s) skipped in %s" % (malformed, path)])
        out.help(
            [
                "Run `gcloud-axi ledger` with no filters to see every record",
                "Run `gcloud-axi grant --tier <name> --task <slug>` to issue a credential",
            ]
        )
        return out, 0

    rows = []
    for record in records:
        active = tiering.is_active(record)
        rows.append(
            {
                "task": record.get("task"),
                "tier": record.get("tier"),
                "project": record.get("project"),
                "issued": timeutil.short(record.get("issuedAt")),
                "expires": timeutil.short(record.get("expiresAt")),
                "state": "active" if active else ("expired" if active is False else "unknown"),
            }
        )
    out.table(
        "issuances", ["task", "tier", "project", "issued", "expires", "state"], rows
    )

    if args.get("full"):
        for record in records:
            out.raw("")
            out.block(
                "issuance",
                [
                    ("task", record.get("task")),
                    ("tier", record.get("tier")),
                    ("serviceAccount", record.get("serviceAccount")),
                    ("ttlSeconds", record.get("ttlSeconds")),
                    ("reason", record.get("reason")),
                    ("configDir", record.get("configDir")),
                    ("tool", record.get("tool")),
                ],
            )
    else:
        out.note("service account, reason and config directory omitted - use --full")

    if matched > len(records):
        out.note(
            "showing the %d most recent of %d matching records - raise --limit for more"
            % (len(records), matched)
        )
    if malformed:
        out.warnings(["%d unparseable line(s) skipped in %s" % (malformed, path)])

    out.raw("")
    out.help(
        [
            "Run `gcloud-axi ledger --active` to see only open windows",
            "Run `gcloud-axi ledger --task <slug> --full` for one task's detail",
            "Run `gcloud-axi revoke --tier <name>` for how to end a tier early",
        ]
    )
    return out, 0
