"""`gcloud-axi revoke` - print the revocation options; run nothing.

Revoking a tier is an owner action with consequences for everyone currently
holding that tier. This command therefore hands you the exact commands and the
trade-off between them, and executes none of them itself.
"""

from .. import config as config_mod, flags, helptext, tiering, timeutil, toon

REVOKE_FLAGS = {"tier": flags.VALUE, "project": flags.VALUE}


def help_out(cfg=None):
    declared = ", ".join(cfg.tier_names()) if cfg and cfg.tiering_configured() else None
    return helptext.render(
        "gcloud-axi revoke --tier <name> [flags]",
        description="The three ways to end a tier's outstanding credentials, printed as commands to run.",
        flags=[
            "--tier <name>   which declared tier to describe revocation for (required)",
            "--project <id>  project the commands should target; must be one the tier allows",
            "--help          this text",
        ],
        notes=[
            "This command runs nothing. It reads the config and the ledger, and prints",
            "Declared tiers in the active config: %s" % (declared or "(none - tiering not configured)"),
            "Rung 1 is usually the right answer: short lifetimes mean waiting is a real strategy",
            "Rungs 2 and 3 are owner-level IAM changes and appear in Google's own audit log",
        ],
        examples=[
            "gcloud-axi revoke --tier inspect",
            "gcloud-axi revoke --tier operate --project my-project",
        ],
    )


def dispatch(ctx_factory, argv):
    cfg = config_mod.load()
    if flags.wants_help(argv):
        return help_out(cfg), 0

    args = flags.parse(argv, REVOKE_FLAGS, "revoke", max_positional=0)
    tier = tiering.resolve_tier(cfg, args.get("tier"), "revoke")

    project = args.get("project") or cfg.get("PROJECT") or tier.projects[0]
    if not tier.allows_project(project):
        raise tiering.refuse_project(tier, project, cfg)

    ledger_file = config_mod.ledger_path(cfg)
    records, _ = tiering.read_ledger(ledger_file)
    outstanding = [
        r
        for r in records
        if str(r.get("tier", "")).lower() == tier.name.lower()
        and tiering.is_active(r) is True
    ]

    sa = tier.service_account
    out = toon.Out()
    out.block(
        "revoke",
        [
            ("tier", tier.name),
            ("serviceAccount", sa),
            ("project", project),
            ("ranAnything", False),
        ],
    )

    if outstanding:
        out.table(
            "outstanding",
            ["task", "issued", "expires"],
            [
                {
                    "task": r.get("task"),
                    "issued": timeutil.short(r.get("issuedAt")),
                    "expires": timeutil.short(r.get("expiresAt")),
                }
                for r in outstanding
            ],
        )
        soonest = min(
            (r.get("expiresAt") for r in outstanding if r.get("expiresAt")), default=None
        )
        latest = max(
            (r.get("expiresAt") for r in outstanding if r.get("expiresAt")), default=None
        )
        out.field("windowsCloseBetween", "%s .. %s" % (timeutil.short(soonest), timeutil.short(latest)))
    else:
        out.empty("outstanding")
        out.note(
            "0 issuances of tier %s have an open window according to %s"
            % (tier.name, ledger_file)
        )

    out.raw("")
    out.block(
        "rung1",
        [
            ("name", "natural expiry"),
            ("effect", "every outstanding token dies on its own at the recorded expiry"),
            ("reversible", "n/a"),
            ("command", "(none - wait)"),
        ],
    )
    out.raw("")
    out.block(
        "rung2",
        [
            ("name", "disable the target service account"),
            (
                "effect",
                "outstanding tokens stop working within minutes and new mints fail, "
                "for this tier only",
            ),
            ("reversible", "yes - `gcloud iam service-accounts enable %s`" % sa),
            (
                "command",
                "gcloud iam service-accounts disable %s --project %s" % (sa, project),
            ),
        ],
    )
    out.raw("")
    out.block(
        "rung3",
        [
            ("name", "remove the ability to mint at all"),
            (
                "effect",
                "nobody can impersonate this service account again until the binding is restored",
            ),
            ("reversible", "yes, but it is an infrastructure change - prefer your IaC path"),
            (
                "command",
                "gcloud iam service-accounts remove-iam-policy-binding %s "
                "--member <issuer-member> --role roles/iam.serviceAccountTokenCreator "
                "--project %s" % (sa, project),
            ),
        ],
    )

    out.raw("")
    out.note(
        "this command executed none of the above - copy the rung you want and run it as an owner"
    )
    out.note(
        "rungs 2 and 3 affect every current holder of tier %s, not one recipient" % tier.name
    )
    out.help(
        [
            "Run `gcloud-axi ledger --tier %s --active` to see who is affected" % tier.name,
            "Run `gcloud-axi iam audit --member %s` to confirm what the account can do" % sa,
            "Run `gcloud-axi grant --tier %s --task <slug>` to re-issue afterwards" % tier.name,
        ]
    )
    return out, 0
