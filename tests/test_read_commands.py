"""Every read command: happy path, empty state, and failure shape."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import CliTestCase  # noqa: E402


class AmbientStatusTest(CliTestCase):
    def test_no_args_prints_live_state_not_help(self):
        run = self.assertOk(self.cli(use_config=False))
        self.assertIn_("tool: gcloud-axi", run)
        self.assertIn_("credential:", run)
        self.assertIn_("account: operator@example.com", run)
        self.assertIn_("project: my-project", run)
        self.assertIn_("health:", run)
        self.assertNotIn_("usage:", run)
        self.assertHasHelp(run)

    def test_works_with_no_config_file_at_all(self):
        run = self.assertOk(self.cli(use_config=False))
        self.assertIn_("configExists: false", run)
        self.assertIn_("tiering: not configured (optional)", run)

    def test_reports_project_source_when_config_supplies_it(self):
        self.write_config("PROJECT=my-other-project\n")
        run = self.assertOk(self.cli())
        self.assertIn_("project: my-other-project", run)
        self.assertIn_("projectSource: config", run)

    def test_flag_beats_config(self):
        self.write_config("PROJECT=my-other-project\n")
        run = self.assertOk(self.cli("--project", "flag-project"))
        self.assertIn_("project: flag-project", run)
        self.assertIn_("projectSource: --project flag", run)

    def test_falls_back_to_gcloud_configuration(self):
        run = self.assertOk(self.cli(use_config=False))
        self.assertIn_("projectSource: gcloud configuration", run)

    def test_degrades_when_gcloud_is_unavailable(self):
        run = self.assertOk(
            self.cli(use_config=False, env={"GCLOUD_AXI_GCLOUD": "/nonexistent/gcloud"})
        )
        self.assertIn_("account: (none active)", run)
        self.assertHasHelp(run)

    def test_unknown_flag_exits_2(self):
        run = self.assertExit(self.cli("--bogus"), 2)
        self.assertErrorShape(run, "UNKNOWN_FLAG")

    def test_unknown_command_exits_2(self):
        run = self.assertExit(self.cli("nosuchthing"), 2)
        self.assertErrorShape(run, "UNKNOWN_COMMAND")

    def test_version_flag(self):
        run = self.assertOk(self.cli("--version"))
        self.assertIn("gcloud-axi", run.stdout)


class OverviewTest(CliTestCase):
    def test_aggregates_every_section(self):
        run = self.assertOk(self.cli("overview"))
        self.assertIn_("services[2]{", run)
        self.assertIn_("jobs[2]{", run)
        self.assertIn_("sql[1]{", run)
        self.assertIn_("errors:", run)
        self.assertHasHelp(run)

    def test_empty_project_states_zero_for_each_section(self):
        run = self.assertOk(self.cli("overview", scenario="empty"))
        self.assertIn_("services: []", run)
        self.assertIn_("jobs: []", run)
        self.assertIn_("sql: []", run)
        self.assertIn_("count: 0", run)

    def test_sections_degrade_independently(self):
        # `partial` refuses the SQL and logging APIs but answers everything
        # else, so the command must still render the sections it could read.
        run = self.assertOk(self.cli("overview", scenario="partial"))
        self.assertIn_("services[2]{", run)
        self.assertIn_("sql: unavailable", run)
        self.assertIn_("errors: unavailable", run)
        self.assertIn_("warnings[2]:", run)
        self.assertHasHelp(run)

    def test_a_wholly_unreadable_project_is_an_error_not_an_empty_success(self):
        run = self.assertExit(self.cli("overview", scenario="denied"), 1)
        self.assertErrorShape(run, "OVERVIEW_EMPTY")
        self.assertIn_("firstWarning:", run)

    def test_no_errors_flag_skips_the_log_scan(self):
        run = self.assertOk(self.cli("overview", "--no-errors"))
        self.assertIn_("errors: skipped (--no-errors)", run)
        self.assertFalse(
            any("logging" in call for call in run.calls), run.describe()
        )

    def test_unknown_flag_exits_2(self):
        run = self.assertExit(self.cli("overview", "--nope"), 2)
        self.assertErrorShape(run, "UNKNOWN_FLAG")


class RunTest(CliTestCase):
    def test_status_lists_services_with_digest_and_env_names(self):
        run = self.assertOk(self.cli("run", "status"))
        self.assertIn_("name: my-service", run)
        self.assertIn_("imageDigest: sha256:", run)
        self.assertIn_("servingRevision: my-service-00007-abc", run)
        self.assertIn_("envFromSecrets[2]{env,secret,version}:", run)
        self.assertIn_("DATABASE_PASSWORD,my-secret,latest", run)

    def test_status_never_prints_env_values(self):
        run = self.assertOk(self.cli("run", "status"))
        self.assertNotIn_("redacted-by-fixture", run)

    def test_status_reports_unhealthy_service(self):
        run = self.assertOk(self.cli("run", "status"))
        self.assertIn_("status: RevisionFailed", run)

    def test_status_of_missing_service_is_a_structured_error(self):
        run = self.assertExit(self.cli("run", "status", "no-such-service"), 1)
        self.assertErrorShape(run, "NOT_FOUND")
        self.assertHasHelp(run)

    def test_status_empty_project(self):
        run = self.assertOk(self.cli("run", "status", scenario="empty"))
        self.assertIn_("services: []", run)
        self.assertIn_("count: 0", run)

    def test_revisions_carry_count_and_status(self):
        run = self.assertOk(self.cli("run", "revisions", "my-service"))
        self.assertIn_("revisions[3]{name,created,age,traffic,status}:", run)
        self.assertIn_("ContainerMissing", run)

    def test_revisions_empty_state_is_definitive(self):
        run = self.assertOk(self.cli("run", "revisions", "my-service", scenario="empty"))
        self.assertIn_("revisions: []", run)
        self.assertIn_("note: 0 revisions returned", run)

    def test_revisions_full_adds_images(self):
        run = self.assertOk(self.cli("run", "revisions", "my-service", "--full"))
        self.assertIn_("digest: sha256:", run)

    def test_unknown_subcommand_exits_2(self):
        run = self.assertExit(self.cli("run", "nope"), 2)
        self.assertErrorShape(run, "UNKNOWN_SUBCOMMAND")

    def test_permission_denied_carries_elevation_route(self):
        run = self.assertExit(self.cli("run", "status", scenario="denied"), 1)
        self.assertErrorShape(run, "PERMISSION_DENIED")
        self.assertIn_("request a higher tier", run)

    def test_expired_credential_is_named_as_such(self):
        run = self.assertExit(self.cli("run", "status", scenario="expired"), 1)
        self.assertErrorShape(run, "CREDENTIAL_EXPIRED")
        self.assertIn_("expired", run)


class LogsTest(CliTestCase):
    def test_reports_window_and_entries(self):
        run = self.assertOk(self.cli("logs", "my-service"))
        self.assertIn_("target: my-service", run)
        self.assertIn_("since: 1h", run)
        self.assertIn_("entries[3]{time,severity,revision,message}:", run)

    def test_truncates_with_a_size_hint(self):
        run = self.assertOk(self.cli("logs", "my-service"))
        self.assertIn_("truncated at 200 chars", run)
        self.assertIn_("--full", run)

    def test_full_disables_truncation(self):
        run = self.assertOk(self.cli("logs", "my-service", "--full"))
        self.assertNotIn_("messages truncated", run)
        self.assertIn_("withheld from the default view", run)

    def test_empty_window_is_definitive(self):
        run = self.assertOk(self.cli("logs", "my-service", scenario="empty"))
        self.assertIn_("entries: []", run)
        self.assertIn_("note: 0 entries for my-service in the last 1h", run)

    def test_filter_includes_severity_and_query(self):
        run = self.assertOk(
            self.cli("logs", "my-service", "--severity", "error", "--query", "timeout")
        )
        joined = " ".join(" ".join(call) for call in run.calls)
        self.assertIn("severity>=ERROR", joined)
        self.assertIn('textPayload:"timeout"', joined)

    def test_missing_target_is_a_usage_error(self):
        run = self.assertExit(self.cli("logs"), 2)
        self.assertErrorShape(run, "MISSING_ARGUMENT")

    def test_bad_duration_is_a_usage_error(self):
        run = self.assertExit(self.cli("logs", "my-service", "--since", "yesterday"), 2)
        self.assertErrorShape(run, "INVALID_VALUE")

    def test_bad_severity_is_a_usage_error(self):
        run = self.assertExit(self.cli("logs", "my-service", "--severity", "loud"), 2)
        self.assertErrorShape(run, "INVALID_VALUE")

    def test_missing_flag_value_is_a_usage_error(self):
        run = self.assertExit(self.cli("logs", "my-service", "--since"), 2)
        self.assertErrorShape(run, "MISSING_VALUE")


class JobsTest(CliTestCase):
    def test_lists_jobs_with_schedule_and_last_result(self):
        run = self.assertOk(self.cli("jobs"))
        self.assertIn_("jobs[2]{name,schedule,lastRun,age,result,state}:", run)
        self.assertIn_("0 2 * * *", run)
        self.assertIn_("SUCCEEDED", run)
        self.assertIn_("FAILED", run)

    def test_empty_state(self):
        run = self.assertOk(self.cli("jobs", scenario="empty"))
        self.assertIn_("jobs: []", run)
        self.assertIn_("note: 0 Cloud Run jobs", run)

    def test_run_starts_an_execution_without_waiting(self):
        run = self.assertOk(self.cli("jobs", "run", "my-job"))
        self.assertIn_("execution: my-job-b9k3", run)
        self.assertIn_("waited: false", run)
        joined = [call for call in run.calls if "execute" in " ".join(call)]
        self.assertTrue(joined, run.describe())

    def test_run_without_a_name_is_a_usage_error(self):
        run = self.assertExit(self.cli("jobs", "run"), 2)
        self.assertErrorShape(run, "MISSING_ARGUMENT")

    def test_run_below_capability_refuses_with_elevation_route(self):
        run = self.assertExit(self.cli("jobs", "run", "my-job", scenario="denied"), 1)
        self.assertErrorShape(run, "PERMISSION_DENIED")
        self.assertIn_("request a higher tier", run)


class SqlTest(CliTestCase):
    def test_status_reports_state_and_counts_flags(self):
        run = self.assertOk(self.cli("sql", "status"))
        self.assertIn_("state: RUNNABLE", run)
        self.assertIn_("databaseVersion: POSTGRES_15", run)
        self.assertIn_("databaseFlags: 2", run)

    def test_full_lists_flags(self):
        run = self.assertOk(self.cli("sql", "status", "--full"))
        self.assertIn_("max_connections,200", run)

    def test_empty_state(self):
        run = self.assertOk(self.cli("sql", "status", scenario="empty"))
        self.assertIn_("instances: []", run)

    def test_proxy_prints_a_command_and_runs_nothing(self):
        run = self.assertOk(self.cli("sql", "proxy", "my-instance"))
        self.assertIn_("started: false", run)
        self.assertIn_("cloud-sql-proxy --port 5432 my-project:us-central1:my-instance", run)
        self.assertFalse(
            any("cloud-sql-proxy" in call[0] for call in run.calls), run.describe()
        )

    def test_proxy_port_override(self):
        run = self.assertOk(self.cli("sql", "proxy", "my-instance", "--port", "6543"))
        self.assertIn_("localPort: 6543", run)

    def test_proxy_missing_instance_is_a_structured_error(self):
        run = self.assertExit(self.cli("sql", "proxy", scenario="empty"), 1)
        self.assertErrorShape(run, "NOT_FOUND")


class SecretsTest(CliTestCase):
    def test_lists_metadata_and_mount_points(self):
        run = self.assertOk(self.cli("secrets"))
        self.assertIn_("payloadAccess: never", run)
        self.assertIn_("secrets[2]{name,created,age,mountedIn}:", run)
        self.assertIn_("my-secret", run)
        self.assertIn_("my-service", run)

    def test_versions_flag_reports_states_only(self):
        run = self.assertOk(self.cli("secrets", "--versions"))
        self.assertIn_("2 total; 1 enabled; latest=3", run)

    def test_no_call_ever_accesses_a_payload(self):
        run = self.assertOk(self.cli("secrets", "--versions"))
        for call in run.calls:
            joined = " ".join(call)
            self.assertNotIn("versions access", joined, run.describe())

    def test_empty_state(self):
        run = self.assertOk(self.cli("secrets", scenario="empty"))
        self.assertIn_("secrets: []", run)

    def test_help_explains_the_payload_boundary(self):
        run = self.assertOk(self.cli("secrets", "--help"))
        self.assertIn_("METADATA ONLY", run)
        self.assertIn_("never prints a secret's value", run)


class IamTest(CliTestCase):
    def test_audit_joins_policies_by_member(self):
        run = self.assertOk(self.cli("iam", "audit"))
        self.assertIn_("members[", run)
        self.assertIn_("serviceAccount:inspect@my-project.iam.gserviceaccount.com", run)
        self.assertIn_("scopes: ", run)
        self.assertIn_("gcloudCalls:", run)

    def test_member_filter_narrows(self):
        run = self.assertOk(self.cli("iam", "audit", "--member", "inspect@"))
        self.assertIn_("inspect@my-project", run)
        self.assertNotIn_("user:operator@example.com,", run)

    def test_conditional_bindings_are_flagged(self):
        run = self.assertOk(self.cli("iam", "audit", "--member", "operator@"))
        self.assertIn_("true", run)

    def test_scope_limits_the_calls(self):
        run = self.assertOk(self.cli("iam", "audit", "--scope", "project"))
        self.assertIn_("scopes: project", run)
        self.assertFalse(
            any("buckets" in " ".join(call) for call in run.calls), run.describe()
        )

    def test_bad_scope_is_a_usage_error(self):
        run = self.assertExit(self.cli("iam", "audit", "--scope", "galaxy"), 2)
        self.assertErrorShape(run, "INVALID_VALUE")

    def test_no_match_is_definitive(self):
        run = self.assertOk(self.cli("iam", "audit", "--member", "nobody@nowhere"))
        self.assertIn_("members: []", run)
        self.assertIn_("note: 0 members matched", run)


class BuildsTest(CliTestCase):
    def test_summary_is_precomputed(self):
        run = self.assertOk(self.cli("builds"))
        self.assertIn_("summary:", run)
        self.assertIn_("failed: 1", run)
        self.assertIn_("builds[2]{id,status,started,duration,trigger}:", run)

    def test_empty_state(self):
        run = self.assertOk(self.cli("builds", scenario="empty"))
        self.assertIn_("builds: []", run)

    def test_limit_is_passed_through(self):
        run = self.assertOk(self.cli("builds", "--limit", "3"))
        joined = " ".join(" ".join(call) for call in run.calls)
        self.assertIn("--limit=3", joined)

    def test_limit_must_be_numeric(self):
        run = self.assertExit(self.cli("builds", "--limit", "many"), 2)
        self.assertErrorShape(run, "INVALID_VALUE")


class HelpSurfaceTest(CliTestCase):
    COMMANDS = [
        [],
        ["overview"],
        ["run"],
        ["logs"],
        ["jobs"],
        ["sql"],
        ["secrets"],
        ["iam"],
        ["builds"],
        ["grant"],
        ["ledger"],
        ["revoke"],
    ]

    def test_every_command_has_help_with_usage_and_exit_contract(self):
        for command in self.COMMANDS:
            run = self.assertOk(self.cli(*(command + ["--help"])))
            self.assertIn_("usage: gcloud-axi", run)
            self.assertIn_("exit: 0 success, 1 error, 2 usage error", run)

    def test_top_level_help_states_project_resolution_order(self):
        run = self.assertOk(self.cli("--help"))
        self.assertIn_("the --project flag, then PROJECT in the config file", run)
        self.assertIn_("then whatever gcloud itself is configured with", run)


if __name__ == "__main__":
    unittest.main()
