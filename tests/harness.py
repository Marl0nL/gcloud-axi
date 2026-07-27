"""Shared plumbing for the offline test suite.

Every test runs the real CLI as a subprocess with the fake-gcloud shim first on
PATH and with HOME/XDG pointed at a scratch directory. Nothing here can reach a
real gcloud, a real credential, or the network.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "gcloud-axi")
SHIM_DIR = os.path.join(ROOT, "tests", "shim")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")


class Run(object):
    def __init__(self, code, stdout, stderr, calls):
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.calls = calls

    def __contains__(self, needle):
        return needle in self.stdout

    def line(self, prefix):
        """The first stdout line starting with ``prefix``, stripped."""
        for line in self.stdout.splitlines():
            if line.strip().startswith(prefix):
                return line.strip()
        return None

    def describe(self):
        return "exit=%d\n--- stdout ---\n%s\n--- stderr ---\n%s" % (
            self.code,
            self.stdout,
            self.stderr,
        )


class CliTestCase(unittest.TestCase):
    """Base class giving each test an isolated HOME and a fixture scenario."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="gcloud-axi-test-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.config_path = os.path.join(self.home, "config")
        self.ledger_path = os.path.join(self.home, "ledger.log")
        self.call_log = os.path.join(self.home, "calls.log")

    def write_config(self, body):
        with open(self.config_path, "w") as handle:
            handle.write(body)
        return self.config_path

    def cli(self, *args, **kwargs):
        """Run the CLI. ``scenario`` picks a fixture directory overlay."""
        scenario = kwargs.pop("scenario", "happy")
        extra_env = kwargs.pop("env", {}) or {}
        use_config = kwargs.pop("use_config", True)
        cwd = kwargs.pop("cwd", self.home)
        if kwargs:
            raise TypeError("unexpected kwargs: %s" % sorted(kwargs))

        search = [os.path.join(FIXTURES, scenario)]
        if scenario != "happy":
            search.append(os.path.join(FIXTURES, "happy"))

        env = dict(os.environ)
        env.update(
            {
                "HOME": self.home,
                "XDG_CONFIG_HOME": os.path.join(self.home, ".config"),
                "PATH": SHIM_DIR + os.pathsep + env.get("PATH", ""),
                "FAKE_GCLOUD_FIXTURES": os.pathsep.join(search),
                "FAKE_GCLOUD_LOG": self.call_log,
                "GCLOUD_AXI_LEDGER": self.ledger_path,
                "CLOUDSDK_CONFIG": "",
            }
        )
        env.pop("CLOUDSDK_CONFIG")
        if use_config:
            env["GCLOUD_AXI_CONFIG"] = self.config_path
        env.update(extra_env)

        proc = subprocess.Popen(
            [sys.executable, CLI] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
        out, err = proc.communicate(timeout=60)
        return Run(
            proc.returncode,
            out.decode("utf-8"),
            err.decode("utf-8"),
            self.read_calls(),
        )

    def read_calls(self):
        if not os.path.isfile(self.call_log):
            return []
        with open(self.call_log, "r") as handle:
            return [line.rstrip("\n").split("\x1f") for line in handle if line.strip()]

    # -- assertions --------------------------------------------------------

    def assertOk(self, run):
        self.assertEqual(0, run.code, run.describe())
        return run

    def assertExit(self, run, code):
        self.assertEqual(code, run.code, run.describe())
        return run

    def assertIn_(self, needle, run):
        self.assertIn(needle, run.stdout, run.describe())

    def assertNotIn_(self, needle, run):
        self.assertNotIn(needle, run.stdout, run.describe())

    def assertHasHelp(self, run):
        self.assertRegex(run.stdout, r"help\[\d+\]:", run.describe())

    def assertErrorShape(self, run, code=None):
        self.assertRegex(run.stdout, r"^error: ", run.describe())
        self.assertRegex(run.stdout, r"\ncode: ", run.describe())
        if code:
            self.assertIn("code: %s" % code, run.stdout, run.describe())


TIER_CONFIG = """
# offline test configuration - placeholders only
PROJECT=my-project
REGION=us-central1

TIERS=inspect,operate

TIER_INSPECT_SERVICE_ACCOUNT=inspect@my-project.iam.gserviceaccount.com
TIER_INSPECT_PROJECTS=my-project,my-other-project
TIER_INSPECT_TTL=3600
TIER_INSPECT_DESCRIPTION="read-only inspection"

TIER_OPERATE_SERVICE_ACCOUNT=operate@my-project.iam.gserviceaccount.com
TIER_OPERATE_PROJECTS=my-project
TIER_OPERATE_TTL=1800
"""
