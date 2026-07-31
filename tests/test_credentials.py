"""The credential subsystem: dual probe, ADC fallback, and diagnosis.

These cover the four states a machine is actually found in - both credentials
live, either one lapsed, both lapsed - plus the two things that must remain true
in every one of them: no token value is ever printed, and nothing mutating is
ever re-issued under a substituted credential.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import CliTestCase, ROOT  # noqa: E402

# The values the shim hands back. Nothing this tool prints may contain either.
CLI_TOKEN = "ya29.FAKE-TOKEN-FOR-TESTS-ONLY"
ADC_TOKEN = "ya29.FAKE-ADC-TOKEN-FOR-TESTS-ONLY"


class DualCredentialProbeTest(CliTestCase):
    """`gcloud-axi auth` - finding 1a."""

    def test_reports_both_credentials_not_one(self):
        run = self.assertOk(self.cli("auth"))
        self.assertIn_("cli: live", run)
        self.assertIn_("adc: live", run)
        self.assertIn_("bothLive: true", run)
        self.assertHasHelp(run)

    def test_a_lapsed_cli_credential_with_live_adc_is_named_precisely(self):
        run = self.assertOk(self.cli("auth", scenario="adcfallback"))
        self.assertIn_("cli: lapsed", run)
        self.assertIn_("adc: live", run)
        self.assertIn_("inStep: false", run)
        # The half that is broken must carry its own fix, not a generic pointer.
        self.assertIn_("gcloud auth login", run)

    def test_a_lapsed_adc_with_live_cli_is_named_precisely(self):
        run = self.assertOk(self.cli("auth", scenario="adclapsed"))
        self.assertIn_("cli: live", run)
        self.assertIn_("adc: lapsed", run)
        self.assertIn_("gcloud auth application-default login", run)

    def test_both_lapsed_reports_both_fixes(self):
        run = self.assertOk(self.cli("auth", scenario="bothlapsed"))
        self.assertIn_("cli: lapsed", run)
        self.assertIn_("adc: lapsed", run)
        self.assertIn_("neither credential is live", run)
        self.assertIn_("gcloud auth login", run)
        self.assertIn_("gcloud auth application-default login", run)

    def test_liveness_is_proved_by_a_mint_not_by_a_listing(self):
        """An account can be listed as active long after it stopped working."""
        run = self.assertOk(self.cli("auth", scenario="adcfallback"))
        minted = [c for c in run.calls if "print-access-token" in c]
        self.assertEqual(2, len(minted), run.describe())
        self.assertTrue(
            any("application-default" in c for c in minted), run.describe()
        )

    def test_no_probe_mints_nothing(self):
        run = self.assertOk(self.cli("auth", "--no-probe"))
        self.assertIn_("probed: false", run)
        self.assertFalse(
            [c for c in run.calls if "print-access-token" in c], run.describe()
        )

    def test_identity_is_read_from_an_adc_file_without_reading_its_secrets(self):
        adc = os.path.join(self.home, "adc.json")
        with open(adc, "w") as handle:
            json.dump(
                {
                    "type": "service_account",
                    "client_email": "inspect@my-project.iam.gserviceaccount.com",
                    "private_key": "-----BEGIN PRIVATE KEY-----FAKE-----END PRIVATE KEY-----",
                    "refresh_token": "FAKE-REFRESH-TOKEN-VALUE",
                },
                handle,
            )
        run = self.assertOk(
            self.cli("auth", env={"GOOGLE_APPLICATION_CREDENTIALS": adc})
        )
        self.assertIn_("inspect@my-project.iam.gserviceaccount.com", run)
        self.assertIn_("type: service_account", run)
        self.assertNotIn_("FAKE-REFRESH-TOKEN-VALUE", run)
        self.assertNotIn_("BEGIN PRIVATE KEY", run)

    def test_no_token_value_is_ever_printed(self):
        for scenario in ("happy", "adcfallback", "adclapsed", "bothlapsed"):
            run = self.cli("auth", scenario=scenario)
            self.assertNotIn(CLI_TOKEN, run.stdout, run.describe())
            self.assertNotIn(ADC_TOKEN, run.stdout, run.describe())
            self.assertNotIn(CLI_TOKEN, run.stderr, run.describe())
            self.assertNotIn(ADC_TOKEN, run.stderr, run.describe())

    def test_ambient_status_names_the_other_credential_too(self):
        run = self.assertOk(self.cli())
        self.assertIn_("adc:", run)
        self.assertIn_("gcloud-axi auth", run)

    def test_ambient_status_does_not_pay_for_a_liveness_probe(self):
        run = self.assertOk(self.cli())
        self.assertFalse(
            [c for c in run.calls if "print-access-token" in c], run.describe()
        )


class AdcFallbackTest(CliTestCase):
    """Finding 1b: a read must survive a lapsed CLI credential."""

    def test_a_read_falls_back_to_adc_and_says_so(self):
        run = self.assertOk(self.cli("run", "status", scenario="adcfallback"))
        self.assertIn_("credentialFallback:", run)
        self.assertIn_("used: adc", run)
        self.assertIn_("my-service", run)

    def test_the_fallback_is_declared_above_the_help_hints(self):
        run = self.assertOk(self.cli("run", "status", scenario="adcfallback"))
        self.assertLess(
            run.stdout.index("credentialFallback:"),
            run.stdout.index("help["),
            run.describe(),
        )

    def test_the_fallback_carries_the_token_as_a_file_never_as_an_argument(self):
        run = self.assertOk(self.cli("run", "status", scenario="adcfallback"))
        retried = [c for c in run.calls if any("--access-token-file" in a for a in c)]
        self.assertTrue(retried, run.describe())
        for call in retried:
            for argument in call:
                self.assertNotIn(ADC_TOKEN, argument, " ".join(call))
                self.assertNotIn(CLI_TOKEN, argument, " ".join(call))

    def test_the_fallback_prints_no_token_value(self):
        run = self.cli("run", "status", scenario="adcfallback")
        self.assertNotIn(ADC_TOKEN, run.stdout, run.describe())
        self.assertNotIn(ADC_TOKEN, run.stderr, run.describe())

    def test_the_scratch_token_file_does_not_outlive_the_process(self):
        run = self.assertOk(self.cli("run", "status", scenario="adcfallback"))
        paths = [
            argument.split("=", 1)[1]
            for call in run.calls
            for argument in call
            if argument.startswith("--access-token-file=")
        ]
        self.assertTrue(paths, run.describe())
        for path in paths:
            self.assertFalse(os.path.exists(path), "%s survived the process" % path)

    def test_a_failure_under_both_credentials_says_the_fallback_was_tried(self):
        run = self.assertExit(self.cli("run", "status", scenario="expired"), 1)
        self.assertErrorShape(run, "CREDENTIAL_EXPIRED")
        self.assertIn_("adcFallback:", run)

    def test_no_fallback_is_attempted_when_adc_cannot_mint_either(self):
        run = self.assertExit(self.cli("run", "status", scenario="bothlapsed"), 1)
        self.assertErrorShape(run, "CREDENTIAL_EXPIRED")
        self.assertIn_("ADC could not mint a token either", run)
        self.assertFalse(
            [c for c in run.calls if any("--access-token-file" in a for a in c)],
            run.describe(),
        )


class FallbackIsReadOnlyTest(unittest.TestCase):
    """The allow-list is the guarantee; it is checked directly, not inferred."""

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from gcloud_axi import gcloudcmd

        self.gcloudcmd = gcloudcmd

    def test_read_vectors_are_eligible(self):
        for args in (
            ["run", "services", "list"],
            ["run", "revisions", "list", "--limit=10"],
            ["logging", "read", "severity>=ERROR"],
            ["projects", "get-iam-policy", "my-project"],
            ["secrets", "versions", "list", "--secret=x"],
            ["sql", "instances", "describe", "my-instance"],
        ):
            self.assertTrue(self.gcloudcmd.is_read_only(args), args)

    def test_mutating_vectors_are_not(self):
        for args in (
            ["run", "jobs", "execute", "my-job"],
            ["run", "deploy", "my-service"],
            ["secrets", "create", "my-secret"],
            ["projects", "add-iam-policy-binding", "my-project"],
            ["iam", "service-accounts", "keys", "create", "key.json"],
            [],
        ):
            self.assertFalse(self.gcloudcmd.is_read_only(args), args)

    def test_credential_plumbing_is_never_re_issued_under_another_credential(self):
        """Asking `auth list` under a borrowed token answers a different question."""
        for args in (
            ["auth", "list"],
            ["auth", "print-access-token"],
            ["config", "get-value", "core/project"],
        ):
            self.assertFalse(self.gcloudcmd.is_read_only(args), args)


class DiagnoseTest(CliTestCase):
    """Finding 2: make the disproof cheap."""

    def test_with_no_command_it_answers_credentials_and_provider(self):
        run = self.assertOk(self.cli("diagnose"))
        self.assertIn_("verdict: credentials-healthy", run)
        self.assertIn_("openIncidents: 0", run)
        self.assertHasHelp(run)

    def test_both_credentials_lapsed_is_the_verdict_when_they_are(self):
        run = self.assertOk(self.cli("diagnose", scenario="bothlapsed"))
        self.assertIn_("verdict: both-credentials-lapsed", run)

    def test_a_5xx_under_every_identity_with_an_open_incident_is_the_provider(self):
        run = self.assertOk(self.cli("diagnose", "run", "status", scenario="outage"))
        self.assertIn_("verdict: provider-outage", run)
        self.assertIn_("attempts[", run)
        self.assertIn_("openIncidents: 1", run)

    def test_a_resolved_incident_is_not_reported_as_open(self):
        run = self.assertOk(self.cli("diagnose", "run", "status", scenario="outage"))
        self.assertIn_("openIncidents: 1", run)
        self.assertNotIn_("must not be reported as open", run)

    def test_a_failure_that_survives_every_identity_is_not_identity(self):
        run = self.assertOk(self.cli("diagnose", "run", "status", scenario="denied"))
        self.assertIn_("verdict: denied-for-every-identity-tried", run)

    def test_a_failure_one_identity_escapes_is_identity_specific(self):
        run = self.assertOk(
            self.cli(
                "diagnose",
                "--as",
                "inspect@my-project.iam.gserviceaccount.com",
                "run",
                "status",
                scenario="identity",
            )
        )
        self.assertIn_("verdict: identity-specific", run)
        self.assertIn_("sa:inspect@my-project.iam.gserviceaccount.com,succeeded", run)

    def test_the_impersonated_identity_comes_from_a_declared_tier_too(self):
        self.write_config(
            "PROJECT=my-project\nREGION=us-central1\nTIERS=inspect\n"
            "TIER_INSPECT_SERVICE_ACCOUNT=inspect@my-project.iam.gserviceaccount.com\n"
            "TIER_INSPECT_PROJECTS=my-project\nTIER_INSPECT_TTL=3600\n"
        )
        run = self.assertOk(
            self.cli("diagnose", "--tier", "inspect", "run", "status",
                     scenario="identity")
        )
        self.assertIn_("sa:inspect@my-project.iam.gserviceaccount.com", run)

    def test_a_mutating_command_cannot_be_diagnosed(self):
        run = self.assertExit(self.cli("diagnose", "jobs", "run", "my-job"), 2)
        self.assertErrorShape(run, "NOT_DIAGNOSABLE")
        self.assertIn_("starts a job execution", run)
        self.assertFalse(
            [c for c in run.calls if "execute" in c], run.describe()
        )

    def test_grant_and_revoke_cannot_be_diagnosed(self):
        for command in (["grant"], ["revoke"]):
            run = self.assertExit(self.cli("diagnose", *command), 2)
            self.assertErrorShape(run, "NOT_DIAGNOSABLE")

    def test_an_unknown_command_is_refused_by_name(self):
        run = self.assertExit(self.cli("diagnose", "nosuchthing"), 2)
        self.assertErrorShape(run, "NOT_DIAGNOSABLE")

    def test_the_commands_own_flags_are_forwarded_verbatim(self):
        run = self.assertOk(
            self.cli("diagnose", "run", "revisions", "my-service", "--limit", "5")
        )
        self.assertIn_("attempts[", run)
        self.assertTrue(
            any("--limit=5" in a for call in run.calls for a in call),
            run.describe(),
        )

    def test_a_positional_after_a_read_verb_is_still_read_only(self):
        """`logging read <filter>` must remain eligible for the fallback."""
        run = self.assertOk(self.cli("diagnose", "logs", "my-service", "--since", "3h"))
        self.assertIn_("attempts[", run)
        self.assertTrue(
            any("logging" in call for call in run.calls), run.describe()
        )

    def test_an_unknown_flag_after_the_command_still_fails_loud(self):
        """The fail-loud guarantee has to survive being called through a wrapper."""
        run = self.assertExit(
            self.cli("diagnose", "run", "status", "--definitely-not-a-flag"), 2
        )
        self.assertIn_("code: UNKNOWN_FLAG", run)

    def test_a_flag_value_is_not_mistaken_for_the_command(self):
        run = self.assertOk(
            self.cli("diagnose", "--as", "inspect@my-project.iam.gserviceaccount.com",
                     scenario="happy")
        )
        self.assertIn_("(no command - credentials and provider only)", run)

    def test_no_network_lookup_when_it_is_declined(self):
        run = self.assertOk(
            self.cli("diagnose", "--no-provider-status",
                     env={"GCLOUD_AXI_STATUS_URL": "http://127.0.0.1:9/never"})
        )
        self.assertIn_("skipped (--no-provider-status)", run)

    def test_an_unreadable_status_feed_is_reported_not_raised(self):
        run = self.assertOk(
            self.cli("diagnose",
                     env={"GCLOUD_AXI_STATUS_URL":
                          "file://" + os.path.join(self.home, "nothing-here.json")})
        )
        self.assertIn_("could not reach the status feed", run)
        self.assertIn_("verdict:", run)

    def test_no_token_value_is_ever_printed(self):
        for scenario in ("happy", "identity", "outage", "bothlapsed"):
            run = self.cli("diagnose", "--as",
                           "inspect@my-project.iam.gserviceaccount.com",
                           "run", "status", scenario=scenario)
            self.assertNotIn(CLI_TOKEN, run.stdout, run.describe())
            self.assertNotIn(ADC_TOKEN, run.stdout, run.describe())
            self.assertNotIn(CLI_TOKEN, run.stderr, run.describe())
            self.assertNotIn(ADC_TOKEN, run.stderr, run.describe())

    def test_the_impersonated_token_file_does_not_outlive_the_process(self):
        run = self.assertOk(
            self.cli("diagnose", "--as", "inspect@my-project.iam.gserviceaccount.com",
                     "run", "status", scenario="identity")
        )
        paths = [
            argument.split("=", 1)[1]
            for call in run.calls
            for argument in call
            if argument.startswith("--access-token-file=")
        ]
        self.assertTrue(paths, run.describe())
        for path in paths:
            self.assertFalse(os.path.exists(path), "%s survived the process" % path)


class ProviderStatusOnErrorTest(CliTestCase):
    """Finding 2a: a 5xx must point at the provider without being asked to."""

    def test_a_5xx_is_classified_as_the_providers_failure(self):
        run = self.assertExit(self.cli("run", "status", scenario="outage"), 1)
        self.assertErrorShape(run, "PROVIDER_ERROR")
        self.assertIn_("httpStatus: 501", run)

    def test_an_open_incident_is_reported_in_the_same_read_as_the_failure(self):
        run = self.assertExit(self.cli("run", "status", scenario="outage"), 1)
        self.assertIn_("providerOpenIncidents: 1", run)
        self.assertIn_("status.cloud.google.com/incidents/", run)
        self.assertIn_("treat this failure as the provider's", run)

    def test_the_absence_of_an_incident_is_stated_definitively(self):
        run = self.assertExit(
            self.cli("run", "status", scenario="outage",
                     env={"GCLOUD_AXI_STATUS_URL":
                          "file://" + os.path.join(
                              ROOT, "tests", "fixtures", "happy", "incidents.json")}),
            1,
        )
        self.assertIn_("Google publishes no open incident", run)
        self.assertIn_("transient", run)

    def test_the_lookup_can_be_switched_off_entirely(self):
        run = self.assertExit(
            self.cli("run", "status", scenario="outage",
                     env={"GCLOUD_AXI_PROVIDER_STATUS": "off",
                          "GCLOUD_AXI_STATUS_URL": "http://127.0.0.1:9/never"}),
            1,
        )
        self.assertErrorShape(run, "PROVIDER_ERROR")
        self.assertIn_("GCLOUD_AXI_PROVIDER_STATUS is off", run)

    def test_an_unreachable_feed_does_not_lose_the_original_error(self):
        run = self.assertExit(
            self.cli("run", "status", scenario="outage",
                     env={"GCLOUD_AXI_STATUS_URL":
                          "file://" + os.path.join(self.home, "absent.json")}),
            1,
        )
        self.assertErrorShape(run, "PROVIDER_ERROR")
        self.assertIn_("could not reach the status feed", run)

    def test_a_permission_denial_is_not_read_as_a_provider_failure(self):
        run = self.assertExit(self.cli("run", "status", scenario="denied"), 1)
        self.assertErrorShape(run, "PERMISSION_DENIED")
        self.assertNotIn_("providerOpenIncidents", run)


class ServerErrorClassificationTest(unittest.TestCase):
    """A 5xx has to be recognised in gcloud's prose, not just in a clean field."""

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from gcloud_axi import gcloudcmd

        self.gcloudcmd = gcloudcmd
        # This class calls `classify` in-process, and a PROVIDER_ERROR there
        # would otherwise reach the real incident feed. Switched off for the
        # whole class, not per-helper, so a test added later cannot miss it.
        os.environ["GCLOUD_AXI_PROVIDER_STATUS"] = "off"
        self.addCleanup(os.environ.pop, "GCLOUD_AXI_PROVIDER_STATUS", None)

    def _code(self, stderr):
        return self.gcloudcmd.classify(["run", "services", "list"], stderr, 1).code

    def test_server_side_failures_are_recognised(self):
        for stderr in (
            "ERROR: (gcloud.run.services.list) HTTPError 501: Not Implemented",
            "ERROR: ResponseError: status=[503], code=[Unavailable]",
            "ERROR: (gcloud.run.services.list) INTERNAL: internal error encountered",
            "ERROR: UNAVAILABLE: The service is currently unavailable.",
            "ERROR: Backend Error",
        ):
            self.assertEqual("PROVIDER_ERROR", self._code(stderr), stderr)

    def test_client_side_failures_are_not(self):
        cases = {
            "ERROR: PERMISSION_DENIED: caller does not have permission": "PERMISSION_DENIED",
            "ERROR: NOT_FOUND: resource my-503-service does not exist": "NOT_FOUND",
            "ERROR: invalid_grant: Token has been expired or revoked.": "CREDENTIAL_EXPIRED",
            "ERROR: Cloud Run Admin API has not been used in project my-project": "API_DISABLED",
            # Ordinary English, not a gRPC status name. Blaming Google for these
            # is the exact mistake this classification exists to prevent.
            "ERROR: PERMISSION_DENIED: the internal load balancer is not visible":
                "PERMISSION_DENIED",
            "ERROR: NOT_FOUND: instance unavailable-replica does not exist": "NOT_FOUND",
        }
        for stderr, expected in cases.items():
            self.assertEqual(expected, self._code(stderr), stderr)

    def test_the_status_code_is_found_even_when_named_after_the_condition(self):
        error = self.gcloudcmd.classify(
            ["run", "services", "list"],
            "ERROR: UNAVAILABLE: backend failed; HTTPError 503: Service Unavailable",
            1,
        )
        self.assertEqual("PROVIDER_ERROR", error.code)
        self.assertIn(("httpStatus", "503"), error.fields)


if __name__ == "__main__":
    unittest.main()
