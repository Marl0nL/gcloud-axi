"""Invariants that hold across the whole tool, not one command.

These are the properties a reviewer should be able to check mechanically:
no secret-payload path exists, no token value can be printed, and the tests
themselves cannot reach a real gcloud.
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import CliTestCase, FIXTURES, ROOT, SHIM_DIR  # noqa: E402

SOURCE_DIRS = [os.path.join(ROOT, "src"), os.path.join(ROOT, "tests")]


def source_files():
    files = [os.path.join(ROOT, "gcloud-axi")]
    for directory in SOURCE_DIRS:
        for base, _, names in os.walk(directory):
            for name in names:
                if name.endswith(".py") or name == "gcloud":
                    files.append(os.path.join(base, name))
    return files


class NoSecretPayloadPathTest(unittest.TestCase):
    """No code path in this tool can read a secret's value."""

    def test_no_source_file_builds_a_payload_access_call(self):
        # The literal is assembled at runtime so this test does not itself put
        # the forbidden call into the tree it is scanning.
        needle = '"secrets", "versions", "access"'
        allowed = {
            os.path.join(ROOT, "src", "gcloud_axi", "gcloudcmd.py"),
            os.path.abspath(__file__),
        }
        for path in source_files():
            if os.path.abspath(path) in allowed:
                continue
            with open(path, "r") as handle:
                body = handle.read()
            self.assertNotIn(needle, body, "%s builds a payload access call" % path)

    def test_the_guard_refuses_such_a_call_at_the_process_boundary(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from gcloud_axi import gcloudcmd
        from gcloud_axi.errors import GcloudError

        with self.assertRaises(GcloudError) as caught:
            gcloudcmd.invoke(["secrets", "versions", "access", "latest", "--secret=x"])
        self.assertEqual("FORBIDDEN_COMMAND", caught.exception.code)

    def test_the_guard_survives_flags_interleaved_with_the_sequence(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from gcloud_axi import gcloudcmd
        from gcloud_axi.errors import GcloudError

        with self.assertRaises(GcloudError):
            gcloudcmd.invoke(
                ["secrets", "--project=my-project", "versions", "access", "1"]
            )


class TestSuiteIsOfflineTest(CliTestCase):
    def test_the_shim_is_what_gets_called(self):
        run = self.assertOk(self.cli("run", "status"))
        self.assertTrue(run.calls, run.describe())

    def test_an_ambient_gcloud_override_cannot_bypass_the_shim(self):
        os.environ["GCLOUD_AXI_GCLOUD"] = "/nonexistent/real-gcloud"
        self.addCleanup(os.environ.pop, "GCLOUD_AXI_GCLOUD", None)
        run = self.assertOk(self.cli("run", "status"))
        self.assertTrue(run.calls, run.describe())

    def test_a_timed_out_gcloud_is_killed_and_reported(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from gcloud_axi import gcloudcmd

        script = os.path.join(self.home, "slow-gcloud")
        with open(script, "w") as handle:
            handle.write("#!/bin/sh\nsleep 30\n")
        os.chmod(script, 0o755)
        os.environ["GCLOUD_AXI_GCLOUD"] = script
        self.addCleanup(os.environ.pop, "GCLOUD_AXI_GCLOUD", None)
        result = gcloudcmd.invoke(["projects", "list"], timeout=1)
        self.assertFalse(result.ok)
        self.assertEqual("TIMEOUT", result.error.code)

    def test_an_unrecorded_call_fails_loudly_rather_than_silently(self):
        env = dict(os.environ)
        env["FAKE_GCLOUD_FIXTURES"] = os.path.join(FIXTURES, "happy")
        proc = subprocess.Popen(
            [os.path.join(SHIM_DIR, "gcloud"), "compute", "instances", "list"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        out, err = proc.communicate(timeout=30)
        self.assertEqual(70, proc.returncode)
        self.assertIn("no fixture", err.decode("utf-8"))

    def test_shim_routes_from_longest_key_to_shortest(self):
        env = dict(os.environ)
        env["FAKE_GCLOUD_FIXTURES"] = os.path.join(FIXTURES, "happy")
        proc = subprocess.Popen(
            [os.path.join(SHIM_DIR, "gcloud"), "run", "jobs", "execute", "my-job",
             "--region=us-central1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        out, _ = proc.communicate(timeout=30)
        self.assertEqual(0, proc.returncode)
        self.assertIn("my-job-b9k3", out.decode("utf-8"))


class OutputContractTest(CliTestCase):
    LIST_COMMANDS = [
        ["run", "status"],
        ["run", "revisions"],
        ["jobs"],
        ["sql", "status"],
        ["secrets"],
        ["builds"],
        ["iam", "audit"],
        ["ledger"],
    ]

    def test_every_listing_carries_a_count(self):
        for command in self.LIST_COMMANDS:
            run = self.assertOk(self.cli(*command))
            self.assertRegex(
                run.stdout,
                r"(count: \d+|\w+\[\d+\]\{)",
                "%s carries no count: %s" % (command, run.describe()),
            )

    def test_every_empty_listing_is_explicit(self):
        for command in self.LIST_COMMANDS:
            run = self.assertOk(self.cli(*command, scenario="empty"))
            self.assertIn("count: 0", run.stdout, "%s: %s" % (command, run.describe()))

    def test_every_result_ends_with_next_step_hints(self):
        for command in self.LIST_COMMANDS + [[], ["overview"], ["logs", "my-service"]]:
            run = self.assertOk(self.cli(*command))
            self.assertRegex(
                run.stdout, r"help\[\d+\]:", "%s: %s" % (command, run.describe())
            )

    def test_unknown_flags_fail_loud_everywhere(self):
        for command in self.LIST_COMMANDS + [[], ["overview"], ["logs", "my-service"],
                                             ["jobs", "run", "my-job"], ["sql", "proxy"],
                                             ["grant"], ["revoke"]]:
            run = self.cli(*(command + ["--definitely-not-a-flag"]))
            self.assertEqual(
                2, run.code, "%s did not exit 2: %s" % (command, run.describe())
            )
            self.assertIn("code: UNKNOWN_FLAG", run.stdout, run.describe())

    def test_errors_go_to_stdout_not_stderr(self):
        run = self.assertExit(self.cli("nosuchcommand"), 2)
        self.assertIn("error:", run.stdout)
        self.assertEqual("", run.stderr.strip(), run.describe())

    def test_help_lines_keep_placeholders_unresolved(self):
        run = self.assertOk(self.cli("run", "status"))
        self.assertIn("<service>", run.stdout, run.describe())


class NoInteractivePromptTest(CliTestCase):
    def test_gcloud_is_always_invoked_with_quiet(self):
        run = self.assertOk(self.cli("overview"))
        self.assertTrue(run.calls)
        for call in run.calls:
            self.assertIn("--quiet", call, " ".join(call))

    def test_commands_do_not_read_stdin(self):
        env = dict(os.environ)
        env.update(
            {
                "HOME": self.home,
                "XDG_CONFIG_HOME": os.path.join(self.home, ".config"),
                "PATH": SHIM_DIR + os.pathsep + env.get("PATH", ""),
                "FAKE_GCLOUD_FIXTURES": os.path.join(FIXTURES, "happy"),
                "GCLOUD_AXI_CONFIG": self.config_path,
                "GCLOUD_AXI_LEDGER": self.ledger_path,
            }
        )
        proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "gcloud-axi"), "overview"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=self.home,
        )
        out, err = proc.communicate(timeout=60)
        self.assertEqual(0, proc.returncode, err.decode("utf-8"))


class PublicSafeFixturesTest(unittest.TestCase):
    """Fixtures and shipped examples must read as generic placeholders."""

    PLACEHOLDER_PROJECTS = ("my-project", "my-other-project")

    def test_every_fixture_is_valid_json(self):
        import json

        for base, _, names in os.walk(FIXTURES):
            for name in names:
                if not name.endswith(".json"):
                    continue
                path = os.path.join(base, name)
                with open(path) as handle:
                    json.loads(handle.read())

    def test_fixture_service_accounts_use_a_placeholder_project(self):
        import re

        pattern = re.compile(r"[\w.+-]+@([\w-]+)\.iam\.gserviceaccount\.com")
        for base, _, names in os.walk(FIXTURES):
            for name in names:
                path = os.path.join(base, name)
                with open(path) as handle:
                    body = handle.read()
                for project in pattern.findall(body):
                    self.assertIn(
                        project, self.PLACEHOLDER_PROJECTS,
                        "%s names a non-placeholder project: %s" % (path, project),
                    )

    def test_config_example_declares_only_placeholders(self):
        import re

        path = os.path.join(ROOT, "config.example")
        with open(path) as handle:
            body = handle.read()
        self.assertIn("TIERS=", body)
        pattern = re.compile(r"[\w.+-]+@([\w-]+)\.iam\.gserviceaccount\.com")
        for project in pattern.findall(body):
            self.assertIn(project, self.PLACEHOLDER_PROJECTS, path)
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.endswith("PROJECTS") or key == "PROJECT":
                for item in value.split(","):
                    self.assertIn(item.strip(), self.PLACEHOLDER_PROJECTS, stripped)


if __name__ == "__main__":
    unittest.main()
