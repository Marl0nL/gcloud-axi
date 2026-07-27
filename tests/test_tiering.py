"""The optional credential-tiering layer: grant, ledger, revoke."""

import json
import os
import stat
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import CliTestCase, TIER_CONFIG  # noqa: E402

FAKE_TOKEN = "ya29.FAKE-TOKEN-FOR-TESTS-ONLY"


class StandsAloneTest(CliTestCase):
    """Override 1: the wrapper is fully usable with no tiering config at all."""

    def test_read_commands_work_with_no_config(self):
        for command in (["overview"], ["run", "status"], ["jobs"], ["sql", "status"],
                        ["secrets"], ["builds"], ["iam", "audit"]):
            run = self.cli(*command, use_config=False)
            self.assertEqual(0, run.code, run.describe())

    def test_grant_without_config_explains_how_to_configure(self):
        run = self.assertExit(self.cli("grant", "--tier", "inspect", "--task", "t"), 1)
        self.assertErrorShape(run, "NOT_CONFIGURED")
        self.assertIn_("Tiering is optional", run)
        self.assertIn_("config.example", run)
        self.assertIn_("configPath:", run)

    def test_ledger_without_config_is_an_empty_ledger_not_an_error(self):
        run = self.assertOk(self.cli("ledger"))
        self.assertIn_("issuances: []", run)
        self.assertIn_("note: 0 issuances recorded", run)

    def test_revoke_without_config_explains_how_to_configure(self):
        run = self.assertExit(self.cli("revoke", "--tier", "inspect"), 1)
        self.assertErrorShape(run, "NOT_CONFIGURED")

    def test_no_tier_names_are_baked_into_the_tool(self):
        """A config declaring an arbitrary tier layout works with no code change."""
        self.write_config(
            "PROJECT=some-other-project\n"
            "TIERS=alpha\n"
            "TIER_ALPHA_SERVICE_ACCOUNT=alpha@some-other-project.iam.gserviceaccount.com\n"
            "TIER_ALPHA_PROJECTS=some-other-project\n"
            "TIER_ALPHA_TTL=900\n"
        )
        run = self.assertOk(
            self.cli("grant", "--tier", "alpha", "--task", "anything")
        )
        self.assertIn_("tier: alpha", run)
        self.assertIn_("ttlSeconds: 900", run)
        self.assertIn_("project: some-other-project", run)


class GrantTest(CliTestCase):
    def setUp(self):
        CliTestCase.setUp(self)
        self.write_config(TIER_CONFIG)
        self.dest = os.path.join(self.home, "scoped")

    def grant(self, *extra, **kwargs):
        args = ["grant", "--tier", "inspect", "--task", "my-task", "--dest", self.dest]
        return self.cli(*(args + list(extra)), **kwargs)

    def test_writes_an_isolated_config_dir(self):
        run = self.assertOk(self.grant())
        self.assertIn_("tier: inspect", run)
        self.assertIn_("configDir: %s" % self.dest, run)
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "access_token")))
        self.assertTrue(
            os.path.isfile(os.path.join(self.dest, "configurations", "config_default"))
        )
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "active_config")))

    def test_config_dir_points_gcloud_at_the_token_file(self):
        self.assertOk(self.grant())
        with open(os.path.join(self.dest, "configurations", "config_default")) as handle:
            body = handle.read()
        self.assertIn("access_token_file = %s" % os.path.join(self.dest, "access_token"), body)
        self.assertIn("project = my-project", body)
        self.assertIn("account = inspect@my-project.iam.gserviceaccount.com", body)
        self.assertNotIn(FAKE_TOKEN, body)

    def test_permissions_are_0700_and_0600(self):
        self.assertOk(self.grant())
        self.assertEqual(0o700, stat.S_IMODE(os.stat(self.dest).st_mode))
        for name in ("access_token", "env.sh", "grant.json"):
            path = os.path.join(self.dest, name)
            self.assertEqual(
                0o600, stat.S_IMODE(os.stat(path).st_mode), "%s mode" % name
            )

    def test_token_value_never_reaches_stdout_stderr_or_ledger(self):
        run = self.assertOk(self.grant())
        self.assertNotIn(FAKE_TOKEN, run.stdout)
        self.assertNotIn(FAKE_TOKEN, run.stderr)
        self.assertIn_("tokenPrinted: false", run)
        with open(self.ledger_path) as handle:
            self.assertNotIn(FAKE_TOKEN, handle.read())
        for name in ("env.sh", "grant.json"):
            with open(os.path.join(self.dest, name)) as handle:
                self.assertNotIn(FAKE_TOKEN, handle.read())
        with open(os.path.join(self.dest, "access_token")) as handle:
            self.assertEqual(FAKE_TOKEN, handle.read().strip())

    def test_env_lines_read_the_file_rather_than_carry_the_value(self):
        run = self.assertOk(self.grant())
        self.assertIn_('export CLOUDSDK_CONFIG="%s"' % self.dest, run)
        self.assertIn_("GOOGLE_OAUTH_ACCESS_TOKEN=\"$(cat %s/access_token)\"" % self.dest, run)

    def test_marker_records_metadata_without_a_token(self):
        self.assertOk(self.grant())
        with open(os.path.join(self.dest, "grant.json")) as handle:
            marker = json.load(handle)
        self.assertEqual("inspect", marker["tier"])
        self.assertEqual("my-task", marker["task"])
        self.assertEqual("my-project", marker["project"])
        self.assertNotIn("token", marker)
        self.assertIn("expiresAt", marker)

    def test_ledger_line_is_appended(self):
        self.assertOk(self.grant("--reason", "quarterly review"))
        with open(self.ledger_path) as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual(1, len(lines))
        record = lines[0]
        self.assertEqual("my-task", record["task"])
        self.assertEqual("inspect", record["tier"])
        self.assertEqual("inspect@my-project.iam.gserviceaccount.com", record["serviceAccount"])
        self.assertEqual(3600, record["ttlSeconds"])
        self.assertEqual("quarterly review", record["reason"])

    def test_ledger_only_ever_grows(self):
        self.assertOk(self.grant())
        self.assertOk(self.grant("--task", "second-task"))
        self.assertOk(self.grant("--task", "third-task"))
        with open(self.ledger_path) as handle:
            lines = [line for line in handle if line.strip()]
        self.assertEqual(3, len(lines))
        run = self.assertOk(self.cli("ledger"))
        self.assertIn_("totalRecords: 3", run)
        self.assertIn_("appendOnly: true", run)

    def test_ledger_file_is_0600(self):
        self.assertOk(self.grant())
        self.assertEqual(0o600, stat.S_IMODE(os.stat(self.ledger_path).st_mode))

    def test_ttl_override_is_honoured(self):
        run = self.assertOk(self.grant("--ttl", "600"))
        self.assertIn_("ttlSeconds: 600", run)
        joined = " ".join(" ".join(call) for call in run.calls)
        self.assertIn("--lifetime=600s", joined)

    def test_tier_default_ttl_is_used_when_not_overridden(self):
        run = self.assertOk(
            self.cli("grant", "--tier", "operate", "--task", "t", "--dest", self.dest)
        )
        self.assertIn_("ttlSeconds: 1800", run)

    def test_impersonation_targets_the_declared_service_account(self):
        run = self.assertOk(self.grant())
        joined = " ".join(" ".join(call) for call in run.calls)
        self.assertIn(
            "--impersonate-service-account=inspect@my-project.iam.gserviceaccount.com",
            joined,
        )

    def test_unknown_tier_is_refused(self):
        run = self.assertExit(
            self.cli("grant", "--tier", "superuser", "--task", "t"), 1
        )
        self.assertErrorShape(run, "UNKNOWN_TIER")
        self.assertIn_("Declared tiers: inspect, operate", run)
        self.assertFalse(os.path.exists(self.dest))

    def test_project_the_tier_does_not_allow_is_refused(self):
        run = self.assertExit(
            self.cli(
                "grant", "--tier", "operate", "--task", "t",
                "--project", "my-other-project", "--dest", self.dest,
            ),
            1,
        )
        self.assertErrorShape(run, "PROJECT_NOT_ALLOWED")
        self.assertIn_("Tier operate allows: my-project", run)
        self.assertFalse(os.path.exists(self.dest))
        self.assertFalse(os.path.exists(self.ledger_path))

    def test_project_the_tier_does_allow_is_accepted(self):
        run = self.assertOk(
            self.cli(
                "grant", "--tier", "inspect", "--task", "t",
                "--project", "my-other-project", "--dest", self.dest,
            )
        )
        self.assertIn_("project: my-other-project", run)

    def test_missing_task_is_a_usage_error(self):
        run = self.assertExit(self.cli("grant", "--tier", "inspect"), 2)
        self.assertErrorShape(run, "MISSING_FLAG")

    def test_missing_tier_is_a_usage_error(self):
        run = self.assertExit(self.cli("grant", "--task", "t"), 2)
        self.assertErrorShape(run, "MISSING_FLAG")

    def test_unknown_flag_exits_2(self):
        run = self.assertExit(self.grant("--sudo"), 2)
        self.assertErrorShape(run, "UNKNOWN_FLAG")

    def test_mint_failure_is_reported_without_writing_anything(self):
        run = self.assertExit(self.grant(scenario="expired"), 1)
        self.assertErrorShape(run, "MINT_FAILED")
        self.assertIn_("serviceAccountTokenCreator", run)
        self.assertFalse(os.path.exists(os.path.join(self.dest, "access_token")))
        self.assertFalse(os.path.exists(self.ledger_path))

    def test_reissue_reports_that_it_replaced_a_previous_grant(self):
        self.assertOk(self.grant())
        run = self.assertOk(self.grant())
        self.assertIn_("replacedPreviousGrant: true", run)

    def test_malformed_tier_declaration_is_a_config_error(self):
        self.write_config("TIERS=broken\n")
        run = self.assertExit(self.cli("grant", "--tier", "broken", "--task", "t"), 1)
        self.assertErrorShape(run, "CONFIG_ERROR")

    def test_tier_with_no_allowed_projects_is_a_config_error(self):
        self.write_config(
            "TIERS=nowhere\n"
            "TIER_NOWHERE_SERVICE_ACCOUNT=nowhere@my-project.iam.gserviceaccount.com\n"
        )
        run = self.assertExit(self.cli("grant", "--tier", "nowhere", "--task", "t"), 1)
        self.assertErrorShape(run, "CONFIG_ERROR")
        self.assertIn_("can never be issued", run)


class LedgerTest(CliTestCase):
    def setUp(self):
        CliTestCase.setUp(self)
        self.write_config(TIER_CONFIG)
        self.dest = os.path.join(self.home, "scoped")

    def test_empty_ledger_is_definitive(self):
        run = self.assertOk(self.cli("ledger"))
        self.assertIn_("issuances: []", run)
        self.assertIn_("count: 0", run)
        self.assertIn_("tokensRecorded: never", run)

    def test_records_are_listed_with_state(self):
        self.assertOk(
            self.cli("grant", "--tier", "inspect", "--task", "alpha", "--dest", self.dest)
        )
        run = self.assertOk(self.cli("ledger"))
        self.assertIn_("issuances[1]{task,tier,project,issued,expires,state}:", run)
        self.assertIn_("alpha,inspect,my-project", run)
        self.assertIn_("active", run)

    def test_task_filter(self):
        for task in ("alpha", "beta"):
            self.assertOk(
                self.cli("grant", "--tier", "inspect", "--task", task, "--dest", self.dest)
            )
        run = self.assertOk(self.cli("ledger", "--task", "beta"))
        self.assertIn_("matched: 1", run)
        self.assertIn_("beta,inspect", run)

    def test_tier_filter_and_active_filter(self):
        self.assertOk(
            self.cli("grant", "--tier", "operate", "--task", "gamma", "--dest", self.dest)
        )
        run = self.assertOk(self.cli("ledger", "--tier", "operate", "--active"))
        self.assertIn_("matched: 1", run)

    def test_filters_that_match_nothing_are_definitive(self):
        self.assertOk(
            self.cli("grant", "--tier", "inspect", "--task", "alpha", "--dest", self.dest)
        )
        run = self.assertOk(self.cli("ledger", "--task", "nope"))
        self.assertIn_("issuances: []", run)
        self.assertIn_("note: 0 of 1 records matched", run)

    def test_full_shows_reason_and_service_account(self):
        self.assertOk(
            self.cli(
                "grant", "--tier", "inspect", "--task", "alpha",
                "--dest", self.dest, "--reason", "because",
            )
        )
        run = self.assertOk(self.cli("ledger", "--full"))
        self.assertIn_("reason: because", run)
        self.assertIn_("serviceAccount: inspect@my-project.iam.gserviceaccount.com", run)

    def test_malformed_lines_are_reported_not_fatal(self):
        with open(self.ledger_path, "w") as handle:
            handle.write("not json at all\n")
        run = self.assertOk(self.cli("ledger"))
        self.assertIn_("unparseable line", run)

    def test_there_is_no_mutating_subcommand(self):
        for attempt in (["ledger", "clear"], ["ledger", "rm"], ["ledger", "edit"]):
            run = self.cli(*attempt)
            self.assertEqual(2, run.code, run.describe())

    def test_unknown_flag_exits_2(self):
        run = self.assertExit(self.cli("ledger", "--delete"), 2)
        self.assertErrorShape(run, "UNKNOWN_FLAG")


class RevokeTest(CliTestCase):
    def setUp(self):
        CliTestCase.setUp(self)
        self.write_config(TIER_CONFIG)
        self.dest = os.path.join(self.home, "scoped")

    def test_prints_three_rungs_and_runs_nothing(self):
        run = self.assertOk(self.cli("revoke", "--tier", "inspect"))
        self.assertIn_("ranAnything: false", run)
        self.assertIn_("rung1:", run)
        self.assertIn_("rung2:", run)
        self.assertIn_("rung3:", run)
        self.assertIn_(
            "gcloud iam service-accounts disable inspect@my-project.iam.gserviceaccount.com", run
        )
        self.assertEqual([], run.calls, run.describe())

    def test_lists_outstanding_issuances(self):
        self.assertOk(
            self.cli("grant", "--tier", "inspect", "--task", "alpha", "--dest", self.dest)
        )
        run = self.assertOk(self.cli("revoke", "--tier", "inspect"))
        self.assertIn_("outstanding[1]{task,issued,expires}:", run)
        self.assertIn_("alpha", run)

    def test_no_outstanding_issuances_is_definitive(self):
        run = self.assertOk(self.cli("revoke", "--tier", "operate"))
        self.assertIn_("outstanding: []", run)
        self.assertIn_("note: 0 issuances of tier operate", run)

    def test_unknown_tier_is_refused(self):
        run = self.assertExit(self.cli("revoke", "--tier", "nope"), 1)
        self.assertErrorShape(run, "UNKNOWN_TIER")

    def test_missing_tier_is_a_usage_error(self):
        run = self.assertExit(self.cli("revoke"), 2)
        self.assertErrorShape(run, "MISSING_FLAG")


class AmbientTierVisibilityTest(CliTestCase):
    def setUp(self):
        CliTestCase.setUp(self)
        self.write_config(TIER_CONFIG)
        self.dest = os.path.join(self.home, "scoped")

    def test_status_reports_the_tier_of_a_scoped_environment(self):
        self.assertOk(
            self.cli("grant", "--tier", "inspect", "--task", "alpha", "--dest", self.dest)
        )
        run = self.assertOk(self.cli(env={"CLOUDSDK_CONFIG": self.dest}))
        self.assertIn_("tier: inspect", run)
        self.assertIn_("issuedFor: alpha", run)
        self.assertIn_("expiresIn:", run)
        self.assertIn_("expired: false", run)

    def test_status_reports_declared_tiers(self):
        run = self.assertOk(self.cli())
        self.assertIn_("2 tier(s) declared: inspect, operate", run)


if __name__ == "__main__":
    unittest.main()
