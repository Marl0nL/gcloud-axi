"""Per-invocation resolution of project, region and active credential.

Project resolution order, in full, and stated in ``--help``:

1. the ``--project`` flag
2. ``PROJECT`` in the config file
3. whatever gcloud itself is configured with

There is no built-in default. If all three come up empty the command fails with
a structured error rather than guessing.
"""

import json
import os

from . import config as config_mod
from . import gcloudcmd
from .errors import AxiError

GRANT_MARKER = "grant.json"


class Context(object):
    def __init__(self, cfg=None, project_flag=None, region_flag=None):
        self.config = cfg if cfg is not None else config_mod.load()
        self._project_flag = project_flag
        self._region_flag = region_flag
        self._project = None
        self._project_source = None
        self._region = None
        self._region_source = None
        self._resolved_project = False
        self._resolved_region = False

    # -- project ------------------------------------------------------------

    def _resolve_project(self):
        if self._resolved_project:
            return
        self._resolved_project = True
        if self._project_flag:
            self._project, self._project_source = self._project_flag, "--project flag"
            return
        from_config = self.config.get("PROJECT")
        if from_config:
            self._project, self._project_source = from_config, "config (%s)" % (
                self.config.path
            )
            return
        result = gcloudcmd.invoke(
            ["config", "get-value", "core/project", "--format=value(.)"], text=True
        )
        if result.ok and result.data:
            value = result.data.strip()
            if value and value not in ("(unset)", "None"):
                self._project, self._project_source = value, "gcloud configuration"
                return
        self._project, self._project_source = None, None

    def project(self, required=True):
        self._resolve_project()
        if not self._project and required:
            raise AxiError(
                "no project resolved",
                code="NO_PROJECT",
                help_lines=[
                    "Run `gcloud-axi <command> --project <project-id>` to name one explicitly",
                    "Or set PROJECT=<project-id> in %s" % self.config.path,
                    "Or set gcloud's own default: `gcloud config set project <project-id>`",
                ],
            )
        return self._project

    def project_source(self):
        self._resolve_project()
        return self._project_source

    # -- region -------------------------------------------------------------

    def _resolve_region(self):
        if self._resolved_region:
            return
        self._resolved_region = True
        if self._region_flag:
            self._region, self._region_source = self._region_flag, "--region flag"
            return
        from_config = self.config.get("REGION")
        if from_config:
            self._region, self._region_source = from_config, "config"
            return
        result = gcloudcmd.invoke(
            ["config", "get-value", "run/region", "--format=value(.)"], text=True
        )
        if result.ok and result.data:
            value = result.data.strip()
            if value and value not in ("(unset)", "None"):
                self._region, self._region_source = value, "gcloud configuration"
                return
        self._region, self._region_source = None, None

    def region(self):
        self._resolve_region()
        return self._region

    def region_args(self):
        """``--region`` when one is known, otherwise let gcloud span regions."""
        region = self.region()
        return ["--region=%s" % region] if region else []

    # -- gcloud helpers -----------------------------------------------------

    def call(self, args, **kwargs):
        return gcloudcmd.call(args, project=self.project(), **kwargs)

    def invoke(self, args, **kwargs):
        return gcloudcmd.invoke(args, project=self.project(required=False), **kwargs)


def active_credential():
    """Best-effort description of the credential gcloud would use right now.

    Identity only - this says nothing about whether the credential still works.
    :func:`gcloud_axi.credentials.probe_cli` is the one that proves that, and
    `gcloud-axi auth` is where a caller should be sent for the answer.
    """
    from . import credentials

    return credentials.cli_identity()


def grant_marker(path=None):
    """Read the metadata a `grant` wrote next to an isolated config dir.

    The marker records tier, task, project and expiry. It never contains a
    token; the token lives in a sibling file this function does not touch.
    """
    if path is None:
        cloudsdk = os.environ.get("CLOUDSDK_CONFIG")
        if not cloudsdk:
            return None
        path = os.path.join(cloudsdk, GRANT_MARKER)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        data.pop("token", None)
        data.pop("access_token", None)
        return data
    return None
