"""Shared pieces of the optional credential-tiering layer.

Two rules govern everything in this module:

* **No policy in code.** Which tiers exist, which service account each targets,
  which projects each may be issued for, and for how long, all come from the
  config file. This module knows the *shape* of a tier, never its content.
* **No token in output.** A minted token is written to one file, mode 0600,
  inside a directory created 0700. It is never printed, never logged, never
  written to the ledger, and never placed in an environment line - the env
  lines this tool prints read the file instead.
"""

import json
import os

from . import config as config_mod
from . import timeutil
from .errors import AxiError, UsageError

TOKEN_FILE = "access_token"
MARKER_FILE = "grant.json"
ENV_FILE = "env.sh"
CONFIG_NAME = "default"


def require_tiering(cfg, subcommand):
    """Raise the one structured 'how to configure' message when tiering is absent."""
    if not cfg.tiering_configured():
        raise config_mod.not_configured_error(subcommand, cfg)


def resolve_tier(cfg, name, subcommand):
    """Look a tier up, refusing anything the config does not declare."""
    require_tiering(cfg, subcommand)
    if not name:
        raise UsageError(
            "`%s` needs --tier <name>" % subcommand,
            code="MISSING_FLAG",
            help_lines=[
                "Declared tiers: %s" % (", ".join(cfg.tier_names()) or "(none)"),
                "Run `gcloud-axi %s --tier <name>`" % subcommand,
            ],
        )
    if not config_mod.TIER_NAME_RE.match(name):
        raise UsageError(
            'tier name "%s" is not a valid identifier' % name,
            code="INVALID_VALUE",
            help_lines=["Tier names may contain letters, digits, hyphen and underscore"],
        )
    tier = cfg.tier(name)
    if tier is None:
        raise AxiError(
            'tier "%s" is not declared in this configuration' % name,
            code="UNKNOWN_TIER",
            help_lines=[
                "Declared tiers: %s" % (", ".join(cfg.tier_names()) or "(none)"),
                "Add it to TIERS in %s together with its TIER_<NAME>_* keys" % cfg.path,
            ],
            fields=[("configPath", cfg.path)],
        )
    return tier


def refuse_project(tier, project, cfg):
    return AxiError(
        'tier "%s" is not allowed to be issued for project "%s"' % (tier.name, project),
        code="PROJECT_NOT_ALLOWED",
        help_lines=[
            "Tier %s allows: %s" % (tier.name, ", ".join(tier.projects)),
            "Run the command with --project <one of those> to issue against an allowed project",
            "To widen the tier, edit TIER_%s_PROJECTS in %s - this tool has no built-in policy"
            % (tier.name.upper().replace("-", "_"), cfg.path),
        ],
        fields=[("tier", tier.name), ("requestedProject", project)],
    )


# -- the isolated config directory -----------------------------------------


def write_isolated_config(dest, token, project, service_account):
    """Write a self-contained CLOUDSDK_CONFIG directory holding the token.

    The layout is gcloud's own: an ``active_config`` pointer plus a
    ``configurations/config_<name>`` INI. Pointing ``auth/access_token_file``
    at a sibling file is what makes a *raw* `gcloud` invocation inside this
    environment scoped too - which is the point of the whole exercise.
    """
    dest = os.path.abspath(os.path.expanduser(dest))
    _mkdir_private(dest)
    conf_dir = os.path.join(dest, "configurations")
    _mkdir_private(conf_dir)

    token_path = os.path.join(dest, TOKEN_FILE)
    _write_private(token_path, token if token.endswith("\n") else token + "\n")

    ini = [
        "[core]",
        "project = %s" % project,
        "account = %s" % service_account,
        "disable_prompts = true",
        "",
        "[auth]",
        "access_token_file = %s" % token_path,
        "",
    ]
    _write_private(
        os.path.join(conf_dir, "config_%s" % CONFIG_NAME), "\n".join(ini)
    )
    _write_private(os.path.join(dest, "active_config"), CONFIG_NAME + "\n")
    return dest, token_path


def env_lines(dest, token_path):
    """Shell lines that establish the scoped environment.

    Neither line contains the token; the second reads it from the 0600 file at
    the moment it is evaluated.
    """
    return [
        'export CLOUDSDK_CONFIG="%s"' % dest,
        'export GOOGLE_OAUTH_ACCESS_TOKEN="$(cat %s)"' % token_path,
    ]


def write_env_file(dest, token_path):
    path = os.path.join(dest, ENV_FILE)
    body = (
        "# Source this file to use the scoped credential.\n"
        "# It contains no token value; the token is read from a 0600 file.\n"
        + "\n".join(env_lines(dest, token_path))
        + "\n"
    )
    _write_private(path, body)
    return path


def write_marker(dest, record):
    """Record what was issued, alongside the token but never containing it."""
    safe = {k: v for k, v in record.items() if k not in ("token", "access_token")}
    path = os.path.join(dest, MARKER_FILE)
    _write_private(path, json.dumps(safe, indent=2, sort_keys=True) + "\n")
    return path


def _mkdir_private(path):
    if not os.path.isdir(path):
        os.makedirs(path, mode=0o700)
    os.chmod(path, 0o700)


def _write_private(path, content):
    flags_ = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags_, 0o600)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


# -- the append-only ledger -------------------------------------------------


def append_ledger(path, record):
    """Append one JSON line. This is the only write path; nothing rewrites it."""
    safe = {k: v for k, v in record.items() if k not in ("token", "access_token")}
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, mode=0o700)
    exists = os.path.exists(path)
    with open(path, "a") as handle:
        handle.write(json.dumps(safe, sort_keys=True) + "\n")
    if not exists:
        os.chmod(path, 0o600)
    return path


def read_ledger(path):
    """Return ``(records, malformed_count)``. Missing file is not an error."""
    if not os.path.isfile(path):
        return [], 0
    records, malformed = [], 0
    with open(path, "r") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
            else:
                malformed += 1
    return records, malformed


def is_active(record, reference=None):
    expires = timeutil.parse_timestamp(record.get("expiresAt"))
    if expires is None:
        return None
    return expires > (reference or timeutil.now())
