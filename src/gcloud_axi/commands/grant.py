"""`gcloud-axi grant` - issue a short-lived scoped credential.

Requires a config file declaring at least one tier. With no such config the
command explains how to create one and changes nothing.
"""

import datetime
import os

from .. import __version__, config as config_mod, flags, gcloudcmd, helptext
from .. import tiering, timeutil, toon
from ..errors import AxiError, UsageError

GRANT_FLAGS = {
    "tier": flags.VALUE,
    "task": flags.VALUE,
    "ttl": flags.VALUE,
    "dest": flags.VALUE,
    "reason": flags.VALUE,
    "project": flags.VALUE,
}

DEFAULT_DEST = "./.gcloud-agent"


def help_out(cfg=None):
    declared = ", ".join(cfg.tier_names()) if cfg and cfg.tiering_configured() else None
    return helptext.render(
        "gcloud-axi grant --tier <name> --task <slug> [flags]",
        description=(
            "Mint a short-lived access token by impersonating a tier's service account, "
            "write it into an isolated gcloud config directory, and record the issuance."
        ),
        flags=[
            "--tier <name>    which declared tier to issue (required)",
            "--task <slug>    what the credential is for; recorded in the ledger (required)",
            "--ttl <seconds>  override the tier's default lifetime",
            "--dest <dir>     isolated config directory to write (default %s)" % DEFAULT_DEST,
            "--reason <text>  free-text justification recorded in the ledger",
            "--project <id>   project to issue for; must be one the tier allows",
            "--help           this text",
        ],
        notes=[
            "Tiers are declarative. This tool ships no tier names, no service accounts and no "
            "project allow-list; everything comes from the config file",
            "Declared tiers in the active config: %s" % (declared or "(none - tiering not configured)"),
            "The token value is never printed, never logged and never written to the ledger. It "
            "goes to one file, mode 0600, in a directory created 0700",
            "The printed environment lines contain no token either - they read it from that file",
            "Inside that environment a raw `gcloud` call is scoped to the tier as well, which is "
            "the property that makes the arrangement resistant to accidents",
            "Scope, honestly: the tier is enforced by IAM on the issued token. It does not stop a "
            "process that can read some other credential on the same machine from using it",
        ],
        examples=[
            "gcloud-axi grant --tier inspect --task inspect-staging",
            "gcloud-axi grant --tier operate --task rerun-nightly --ttl 1800 --dest ./.creds",
            "gcloud-axi grant --tier inspect --task audit --project my-project --reason 'quarterly review'",
        ],
    )


def dispatch(ctx_factory, argv):
    cfg = config_mod.load()
    if flags.wants_help(argv):
        return help_out(cfg), 0

    args = flags.parse(argv, GRANT_FLAGS, "grant", max_positional=0)
    tier = tiering.resolve_tier(cfg, args.get("tier"), "grant")

    task = args.get("task")
    if not task:
        raise UsageError(
            "`grant` needs --task <slug> so the issuance can be attributed",
            code="MISSING_FLAG",
            help_lines=["Run `gcloud-axi grant --tier %s --task <slug>`" % tier.name],
        )
    if not _valid_slug(task):
        raise UsageError(
            'task slug "%s" may contain only letters, digits, hyphen, underscore and dot' % task,
            code="INVALID_VALUE",
        )

    ctx = ctx_factory(args)
    project = ctx.project()
    if not tier.allows_project(project):
        raise tiering.refuse_project(tier, project, cfg)

    ttl = args.int("ttl", default=tier.ttl, minimum=1, maximum=config_mod.MAX_TTL)

    dest = args.get("dest") or cfg.get("GRANT_DEST") or DEFAULT_DEST
    dest = os.path.abspath(os.path.expanduser(dest))
    for ch in '"$`\\\n':
        if ch in dest:
            raise UsageError(
                "destination path contains %r, which cannot be carried safely "
                "in the emitted shell environment lines" % ch,
                code="INVALID_VALUE",
                help_lines=[
                    'Choose a --dest without `"`, `$`, backtick, backslash or newline',
                ],
            )
    replaced = os.path.exists(os.path.join(dest, tiering.TOKEN_FILE))

    token = _mint(tier, ttl, project)

    issued_at = timeutil.now()
    expires_at = issued_at + datetime.timedelta(seconds=ttl)

    dest, token_path = tiering.write_isolated_config(
        dest, token, project, tier.service_account
    )
    del token  # the value has no further use in this process

    record = {
        "task": task,
        "tier": tier.name,
        "serviceAccount": tier.service_account,
        "project": project,
        "ttlSeconds": ttl,
        "issuedAt": timeutil.rfc3339(issued_at),
        "expiresAt": timeutil.rfc3339(expires_at),
        "reason": args.get("reason"),
        "configDir": dest,
        "tool": "gcloud-axi %s" % __version__,
    }

    marker_path = tiering.write_marker(dest, record)
    env_path = tiering.write_env_file(dest, token_path)
    ledger = config_mod.ledger_path(cfg)
    tiering.append_ledger(ledger, record)

    out = toon.Out()
    out.block(
        "granted",
        [
            ("tier", tier.name),
            ("tierDescription", tier.description),
            ("task", task),
            ("project", project),
            ("serviceAccount", tier.service_account),
            ("ttlSeconds", ttl),
            ("issuedAt", record["issuedAt"]),
            ("expiresAt", record["expiresAt"]),
            ("configDir", dest),
            ("replacedPreviousGrant", replaced),
            ("tokenPrinted", False),
        ],
    )
    out.list_lines("env", tiering.env_lines(dest, token_path))
    out.table(
        "files",
        ["path", "mode", "holds"],
        [
            {"path": token_path, "mode": "0600", "holds": "the access token"},
            {
                "path": os.path.join(dest, "configurations", "config_" + tiering.CONFIG_NAME),
                "mode": "0600",
                "holds": "gcloud settings pointing at that token",
            },
            {"path": env_path, "mode": "0600", "holds": "the env lines above; no token value"},
            {"path": marker_path, "mode": "0600", "holds": "issuance metadata; no token value"},
        ],
    )
    out.note(
        "the token value was written to disk only - it is absent from this output, "
        "from %s and from any log" % ledger
    )
    out.note(
        "the recipient can neither renew nor widen this credential; when it expires, "
        "issuing a replacement is a decision that passes through you again"
    )
    out.help(
        [
            "Run `source %s` in the consuming shell to adopt the scoped credential" % env_path,
            "Run `gcloud-axi ledger --task %s` to see this issuance recorded" % task,
            "Run `gcloud-axi revoke --tier %s` for the revocation options" % tier.name,
            "Run `rm -rf %s` when the work is finished" % dest,
        ]
    )
    return out, 0


def _valid_slug(value):
    return bool(value) and all(
        ch.isalnum() or ch in "-_." for ch in value
    )


def _mint(tier, ttl, project):
    result = gcloudcmd.invoke(
        [
            "auth",
            "print-access-token",
            "--impersonate-service-account=%s" % tier.service_account,
            "--lifetime=%ds" % ttl,
        ],
        text=True,
    )
    if not result.ok:
        error = result.error
        raise AxiError(
            "could not mint a token for tier %s: %s" % (tier.name, error.message),
            code="MINT_FAILED",
            help_lines=[
                "The identity you are running as needs roles/iam.serviceAccountTokenCreator "
                "on %s" % tier.service_account,
                "Confirm the target service account exists and is enabled",
                "Run `gcloud-axi iam audit --member %s` to inspect its bindings"
                % tier.service_account,
            ],
            fields=[
                ("tier", tier.name),
                ("serviceAccount", tier.service_account),
                ("project", project),
                ("underlyingCode", error.code),
            ],
        )
    token = (result.data or "").strip()
    if not token:
        raise AxiError(
            "the token mint for tier %s returned nothing" % tier.name,
            code="MINT_EMPTY",
            help_lines=[
                "Run `gcloud auth print-access-token --impersonate-service-account=%s` "
                "yourself to see what it reports" % tier.service_account
            ],
        )
    if any(ch.isspace() for ch in token):
        raise AxiError(
            "the token mint for tier %s returned unexpected multi-token output" % tier.name,
            code="MINT_MALFORMED",
            help_lines=["Nothing was written to disk; re-run once the mint is healthy"],
        )
    return token
