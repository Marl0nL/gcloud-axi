"""Declarative configuration.

Everything organisation-specific lives in a config file, never in this code.
There are no built-in project ids, no built-in service accounts, and no
built-in tier names - a fresh installation with no config file at all is a
fully working read-only wrapper.

Format is shell-sourceable ``KEY=VALUE``. It is parsed, never executed.

    PROJECT=my-project
    REGION=us-central1

    TIERS=inspect,operate

    TIER_INSPECT_SERVICE_ACCOUNT=inspect@my-project.iam.gserviceaccount.com
    TIER_INSPECT_PROJECTS=my-project
    TIER_INSPECT_TTL=3600
    TIER_INSPECT_DESCRIPTION="read-only inspection"

A tier is *declared* only if it is listed in ``TIERS`` and carries a
``TIER_<NAME>_SERVICE_ACCOUNT``. Which projects a tier may be issued for is
``TIER_<NAME>_PROJECTS``; there is no implicit allow.
"""

import os
import re

from .errors import ConfigError, NotConfiguredError

TIER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

DEFAULT_TTL = 3600
MAX_TTL = 43200


def config_path():
    override = os.environ.get("GCLOUD_AXI_CONFIG")
    if override:
        return os.path.expanduser(override)
    return os.path.join(_config_home(), "gcloud-axi", "config")


def _config_home():
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return os.path.expanduser(xdg)
    return os.path.join(os.path.expanduser("~"), ".config")


def ledger_path(cfg=None):
    override = os.environ.get("GCLOUD_AXI_LEDGER")
    if override:
        return os.path.expanduser(override)
    if cfg and cfg.get("LEDGER"):
        return os.path.expanduser(cfg.get("LEDGER"))
    return os.path.join(_config_home(), "gcloud-axi", "ledger.log")


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _strip_comment(value):
    """Drop a trailing ``# comment`` from an unquoted value."""
    out = []
    for i, ch in enumerate(value):
        if ch == "#" and (i == 0 or value[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out)


class Tier(object):
    def __init__(self, name, service_account, projects, ttl, description):
        self.name = name
        self.service_account = service_account
        self.projects = projects
        self.ttl = ttl
        self.description = description

    def allows_project(self, project):
        return project in self.projects


class Config(object):
    def __init__(self, values, path, exists):
        self._values = values
        self.path = path
        self.exists = exists

    def get(self, key, default=None):
        value = self._values.get(key)
        if value is None or value == "":
            return default
        return value

    # -- tiers --------------------------------------------------------------

    def tier_names(self):
        raw = self.get("TIERS", "")
        return [n.strip() for n in raw.split(",") if n.strip()]

    def tiers(self):
        result = []
        for name in self.tier_names():
            result.append(self.tier(name))
        return result

    def tier(self, name):
        """Return the declared tier, or ``None`` if this config does not declare it."""
        if not TIER_NAME_RE.match(name or ""):
            return None
        declared = [n.lower() for n in self.tier_names()]
        if name.lower() not in declared:
            return None
        key = "TIER_%s_" % re.sub(r"[^A-Z0-9]", "_", name.upper())
        service_account = self.get(key + "SERVICE_ACCOUNT")
        if not service_account:
            raise ConfigError(
                'tier "%s" is listed in TIERS but has no %sSERVICE_ACCOUNT'
                % (name, key),
                help_lines=[
                    "Edit %s and set %sSERVICE_ACCOUNT=<sa>@<project>.iam.gserviceaccount.com"
                    % (self.path, key),
                ],
            )
        projects = [
            p.strip() for p in (self.get(key + "PROJECTS", "")).split(",") if p.strip()
        ]
        if not projects:
            raise ConfigError(
                'tier "%s" declares no %sPROJECTS - a tier with no allowed project '
                "can never be issued" % (name, key),
                help_lines=[
                    "Edit %s and set %sPROJECTS=<project-id>[,<project-id>...]"
                    % (self.path, key),
                ],
            )
        ttl_raw = self.get(key + "TTL", str(DEFAULT_TTL))
        try:
            ttl = int(str(ttl_raw).strip())
        except ValueError:
            raise ConfigError(
                'tier "%s" has a non-numeric %sTTL ("%s")' % (name, key, ttl_raw),
                help_lines=["Set %sTTL to a whole number of seconds" % key],
            )
        if ttl <= 0 or ttl > MAX_TTL:
            raise ConfigError(
                'tier "%s" has %sTTL=%d, outside the accepted range 1..%d seconds'
                % (name, key, ttl, MAX_TTL),
                help_lines=["Set %sTTL to a value in that range" % key],
            )
        return Tier(
            name=name.lower(),
            service_account=service_account,
            projects=projects,
            ttl=ttl,
            description=self.get(key + "DESCRIPTION"),
        )

    def tiering_configured(self):
        return bool(self.tier_names())


def load(path=None):
    path = path or config_path()
    values = {}
    exists = os.path.isfile(path)
    if exists:
        try:
            with open(path, "r") as handle:
                content = handle.read()
        except OSError as exc:
            raise ConfigError(
                "cannot read config at %s: %s" % (path, exc),
                help_lines=["Check the file's permissions, or unset GCLOUD_AXI_CONFIG"],
            )
        for lineno, line in enumerate(content.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                raise ConfigError(
                    "%s line %d is not KEY=VALUE" % (path, lineno),
                    help_lines=["Comment the line with `#` or fix it to KEY=VALUE form"],
                )
            key, raw = line.split("=", 1)
            key = key.strip()
            raw = raw.strip()
            if raw and raw[0] in ("'", '"'):
                values[key] = _unquote(raw)
            else:
                values[key] = _strip_comment(raw).strip()
    return Config(values, path, exists)


def not_configured_error(subcommand, cfg):
    """The single structured message every tiering subcommand shows with no config."""
    return NotConfiguredError(
        "no credential tiers are declared, so `gcloud-axi %s` has nothing to act on"
        % subcommand,
        help_lines=[
            "Tiering is optional - every read command works without it",
            "Create %s declaring at least one tier, then re-run" % cfg.path,
            "A commented template ships with this tool as `config.example`",
            "Minimum content: TIERS=<name> plus TIER_<NAME>_SERVICE_ACCOUNT, "
            "TIER_<NAME>_PROJECTS and TIER_<NAME>_TTL",
            "Set GCLOUD_AXI_CONFIG=<path> to keep the config somewhere else",
        ],
        fields=[
            ("configPath", cfg.path),
            ("configExists", cfg.exists),
        ],
    )
