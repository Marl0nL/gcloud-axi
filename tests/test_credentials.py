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

    def test_no_probe_does_not_claim_a_lapse(self):
        """Declining to check liveness must not be reported as both halves dead."""
        run = self.assertOk(self.cli("auth", "--no-probe"))
        self.assertIn_("liveness was not probed (--no-probe)", run)
        self.assertNotIn_("neither credential is live", run)

    def test_a_provider_side_mint_failure_is_unverifiable_not_lapsed(self):
        """A 5xx from the token endpoint proves nothing about the credential."""
        run = self.assertOk(self.cli("auth", scenario="probeoutage"))
        self.assertIn_("cli: unverifiable (provider-side failure)", run)
        self.assertNotIn_("cli: lapsed", run)
        # Re-authenticating is the wrong action during a provider incident, so
        # the login command must not be offered for the unverifiable half.
        self.assertNotIn_("gcloud auth login", run)
        self.assertIn_("inStep: null", run)

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

    def test_the_fallback_notice_renders_once_for_a_multi_read_command(self):
        """Two reads falling back to ADC owe the reader one notice, not two."""
        run = self.assertOk(self.cli("overview", "--no-errors", scenario="adcfallback"))
        self.assertEqual(
            1, run.stdout.count("credentialFallback:"), run.describe()
        )
        retried = [c for c in run.calls if any("--access-token-file" in a for a in c)]
        self.assertGreater(len(retried), 1, run.describe())

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

    def test_a_usage_error_costs_no_subprocess(self):
        """Validation must come before the probes, or a typo pays for two mints."""
        run = self.assertExit(self.cli("diagnose", "grant"), 2)
        self.assertErrorShape(run, "NOT_DIAGNOSABLE")
        self.assertFalse(run.calls, run.describe())

    def test_an_unverifiable_probe_is_not_judged_a_lapse(self):
        run = self.assertOk(self.cli("diagnose", scenario="probeoutage"))
        self.assertIn_("verdict: credential-state-unverifiable", run)
        self.assertNotIn_("credential-lapsed", run)

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


class ProviderLookupScopeTest(unittest.TestCase):
    """The incident feed is read at most once, and never for a discarded answer."""

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from gcloud_axi import gcloudcmd, provider

        self.gcloudcmd = gcloudcmd
        self.provider = provider
        provider._fetch_memo.clear()
        self.addCleanup(provider._fetch_memo.clear)
        os.environ["GCLOUD_AXI_PROVIDER_STATUS"] = "off"
        self.addCleanup(os.environ.pop, "GCLOUD_AXI_PROVIDER_STATUS", None)

    def _classify_5xx(self):
        return self.gcloudcmd.classify(
            ["run", "services", "list"],
            "ERROR: (gcloud.run.services.list) HTTPError 503: Service Unavailable",
            1,
        )

    def test_a_5xx_outside_any_override_is_annotated(self):
        error = self._classify_5xx()
        self.assertTrue(
            [k for k, _ in error.fields if k.startswith("providerStatus")],
            error.fields,
        )

    def test_no_annotation_is_attached_inside_a_credential_override(self):
        """diagnose records only code+message; the annotation would be a discarded
        network read per failing attempt."""
        with self.gcloudcmd.using_credential(None):
            error = self._classify_5xx()
        self.assertEqual("PROVIDER_ERROR", error.code)
        self.assertFalse(
            [k for k, _ in error.fields if k.startswith("provider")], error.fields
        )

    def test_no_annotation_is_attached_under_explicit_suppression(self):
        with self.gcloudcmd.suppress_provider_annotation():
            error = self._classify_5xx()
        self.assertFalse(
            [k for k, _ in error.fields if k.startswith("provider")], error.fields
        )

    def test_the_feed_is_fetched_at_most_once_per_process(self):
        """A command making several failing reads must not pay several lookups."""
        import shutil
        import tempfile

        scratch = tempfile.mkdtemp(prefix="gcloud-axi-feed-")
        self.addCleanup(shutil.rmtree, scratch, True)
        feed = os.path.join(scratch, "incidents.json")
        with open(feed, "w") as handle:
            handle.write("[]")
        os.environ["GCLOUD_AXI_PROVIDER_STATUS"] = "on"
        os.environ["GCLOUD_AXI_STATUS_URL"] = "file://" + feed
        self.addCleanup(os.environ.pop, "GCLOUD_AXI_STATUS_URL", None)

        first = self.provider.fetch()
        self.assertEqual(([], None), first)
        os.remove(feed)
        self.assertEqual(first, self.provider.fetch())


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


class ProbeNeverBlamesTheOperatorTest(unittest.TestCase):
    """The rule, not the instance.

    A probe that could not complete must never be reported as a dead operator
    credential, and must never answer with "re-authenticate". Pinning the one
    status code we happened to capture would pass the whole of the next outage
    while the tool repeats today's wrong answer - so this parametrises over the
    shapes a failure actually arrives in, including one nobody here has seen.

    The mirror assertion matters as much: a genuine rejection must still report
    LAPSED *with* the login command, or "never say lapsed" becomes a passing
    test and a broken tool.
    """

    # Real gcloud stderr, not codes: the classifier is part of what is measured.
    PROBE_DID_NOT_COMPLETE = [
        ("a 500", "ERROR: (gcloud.auth.print-access-token) HTTPError 500: Internal error"),
        # The live capture, 2026-07-31: firebasehosting during a real outage.
        ("the captured 501",
         "ERROR: (gcloud.auth.print-access-token) HTTPError 501: Operation is not "
         "implemented, or supported, or enabled."),
        ("a 502", "ERROR: (gcloud.auth.print-access-token) HTTPError 502: Bad Gateway"),
        ("a 503", "ERROR: (gcloud.auth.print-access-token) HTTPError 503: Service Unavailable"),
        ("a 504", "ERROR: (gcloud.auth.print-access-token) HTTPError 504: Gateway Timeout"),
        ("a gRPC UNAVAILABLE",
         "ERROR: UNAVAILABLE: The service is currently unavailable."),
        # A rate limit is not a credential problem, and is not a 5xx either.
        ("a 429", "ERROR: (gcloud.auth.print-access-token) HTTPError 429: Too Many Requests"),
        # A transport failure never reached an identity check at all.
        ("a transport failure",
         "ERROR: (gcloud.auth.print-access-token) Unable to connect: "
         "[Errno -3] Temporary failure in name resolution"),
        ("an unparseable answer", "ERROR: something gcloud printed that we cannot parse"),
        # The point of the exercise: a shape this file has never met.
        ("an unseen status",
         "ERROR: (gcloud.auth.print-access-token) HTTPError 599: a status nobody here "
         "has seen before"),
        ("an unseen gRPC name", "ERROR: TOTALLY_NEW_CONDITION: something new broke"),
    ]

    PROVES_THE_CREDENTIAL_IS_DEAD = [
        ("an expired grant",
         "ERROR: (gcloud.auth.print-access-token) There was a problem refreshing your "
         "current auth tokens: invalid_grant: Token has been expired or revoked.\n"
         "Please run:\n  $ gcloud auth login"),
        ("a reauth demand",
         "ERROR: (gcloud.auth.print-access-token) reauthentication required"),
        ("a 401", "ERROR: (gcloud.auth.print-access-token) 401 UNAUTHENTICATED"),
    ]

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from gcloud_axi import credentials, gcloudcmd

        self.credentials = credentials
        self.gcloudcmd = gcloudcmd
        # classify() annotates a PROVIDER_ERROR from the incident feed; this
        # class runs in-process, so keep it off the network.
        os.environ["GCLOUD_AXI_PROVIDER_STATUS"] = "off"
        self.addCleanup(os.environ.pop, "GCLOUD_AXI_PROVIDER_STATUS", None)

    def _probe(self, stderr):
        """Run the real classifier over ``stderr``, then the real state mapping."""
        real_invoke = self.gcloudcmd.invoke
        classify = self.gcloudcmd.classify

        def fake_invoke(args, **_kwargs):
            return self.gcloudcmd.Result(False, error=classify(args, stderr, 1))

        self.gcloudcmd.invoke = fake_invoke
        self.addCleanup(setattr, self.gcloudcmd, "invoke", real_invoke)
        return self.credentials._mint_state(["auth", "print-access-token"])

    def test_a_probe_that_did_not_complete_is_never_reported_as_a_lapse(self):
        for label, stderr in self.PROBE_DID_NOT_COMPLETE:
            state, detail, _code = self._probe(stderr)
            self.assertEqual(
                self.credentials.UNVERIFIABLE, state,
                "%s was reported as %r - a probe that did not complete is not "
                "proof the operator's credential is dead" % (label, state),
            )
            self.assertTrue(detail, label)

    def test_a_probe_that_did_not_complete_never_answers_re_authenticate(self):
        for label, stderr in self.PROBE_DID_NOT_COMPLETE:
            state, _detail, _code = self._probe(stderr)
            for kind, login in (("cli", self.credentials.CLI_FIX),
                                ("adc", self.credentials.ADC_FIX)):
                fix = self.credentials._fix_for(state, login)
                self.assertNotIn(
                    "login", (fix or ""),
                    "%s offered %r as the %s fix - re-authenticating cannot fix a "
                    "probe that never completed" % (label, fix, kind),
                )

    def test_an_empty_response_is_not_proof_of_a_lapse(self):
        real_invoke = self.gcloudcmd.invoke

        def fake_invoke(_args, **_kwargs):
            return self.gcloudcmd.Result(True, data="   \n")

        self.gcloudcmd.invoke = fake_invoke
        self.addCleanup(setattr, self.gcloudcmd, "invoke", real_invoke)
        state, _detail, _code = self.credentials._mint_state(["auth", "print-access-token"])
        self.assertEqual(self.credentials.UNVERIFIABLE, state)

    def test_a_real_rejection_is_still_reported_as_a_lapse(self):
        """The mirror: the fix must not over-apply into never reporting a lapse."""
        for label, stderr in self.PROVES_THE_CREDENTIAL_IS_DEAD:
            state, _detail, _code = self._probe(stderr)
            self.assertEqual(
                self.credentials.LAPSED, state,
                "%s was reported as %r - a rejected credential must still read as "
                "lapsed" % (label, state),
            )

    def test_a_real_rejection_still_offers_the_matching_login_command(self):
        for label, stderr in self.PROVES_THE_CREDENTIAL_IS_DEAD:
            state, _detail, _code = self._probe(stderr)
            self.assertEqual(
                self.credentials.CLI_FIX,
                self.credentials._fix_for(state, self.credentials.CLI_FIX), label,
            )
            self.assertEqual(
                self.credentials.ADC_FIX,
                self.credentials._fix_for(state, self.credentials.ADC_FIX), label,
            )

    def test_only_a_rejection_is_treated_as_proof(self):
        """The allow-list is the guarantee; assert it rather than infer it."""
        self.assertEqual(frozenset(["CREDENTIAL_EXPIRED"]), self.credentials.PROVES_LAPSE)


class LiveOutagePairingTest(CliTestCase):
    """Built from a real 501 and the real still-open incident it belonged to.

    See tests/fixtures/liveoutage/SOURCE.md for what is verbatim and what is
    reconstructed.
    """

    def test_the_captured_api_body_classifies_as_the_providers_failure(self):
        """The real bytes, through the real classifier."""
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from gcloud_axi import gcloudcmd

        os.environ["GCLOUD_AXI_PROVIDER_STATUS"] = "off"
        self.addCleanup(os.environ.pop, "GCLOUD_AXI_PROVIDER_STATUS", None)
        with open(os.path.join(ROOT, "tests", "fixtures", "liveoutage",
                               "api-501-response.json")) as handle:
            body = handle.read()
        error = gcloudcmd.classify(["run", "services", "list"], body, 1)
        self.assertEqual("PROVIDER_ERROR", error.code, body)
        self.assertIn(("httpStatus", "501"), error.fields)

    def test_the_open_incident_arrives_in_the_same_output_as_the_failure(self):
        run = self.assertExit(self.cli("run", "status", scenario="liveoutage"), 1)
        self.assertErrorShape(run, "PROVIDER_ERROR")
        self.assertIn_("httpStatus: 501", run)
        self.assertIn_("providerOpenIncidents: 1", run)
        self.assertIn_("Hosting", run)

    def test_the_failure_is_not_blamed_on_the_operators_identity(self):
        run = self.assertExit(self.cli("run", "status", scenario="liveoutage"), 1)
        for wrong in ("CREDENTIAL_EXPIRED", "PERMISSION_DENIED", "gcloud auth login"):
            self.assertNotIn_(wrong, run)

    def test_diagnose_reaches_the_provider_verdict_on_the_real_pairing(self):
        run = self.assertOk(self.cli("diagnose", "run", "status", scenario="liveoutage"))
        self.assertIn_("verdict: provider-outage", run)
        # The verdict and everything after it must not send the reader at their
        # own identity - that is the misdiagnosis this pairing was captured for.
        tail = run.stdout.split("verdict:", 1)[1]
        for wrong in ("credential", "auth login", "re-authenticat"):
            self.assertNotIn(wrong, tail, run.describe())

    def test_an_incident_link_follows_the_feed_it_came_from(self):
        """Cloud and Firebase publish separate feeds on separate hosts."""
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from gcloud_axi import provider

        record = [{"uri": "incidents/abc123"}]
        for url, expected in (
            ("https://status.firebase.google.com/incidents.json",
             "https://status.firebase.google.com/incidents/abc123"),
            ("https://status.cloud.google.com/incidents.json",
             "https://status.cloud.google.com/incidents/abc123"),
        ):
            os.environ["GCLOUD_AXI_STATUS_URL"] = url
            self.addCleanup(os.environ.pop, "GCLOUD_AXI_STATUS_URL", None)
            self.assertEqual([expected], provider.links(record), url)

    def test_a_closed_incident_is_not_reported_as_open(self):
        """The negative case: the same 5xx and the same incident, now resolved."""
        run = self.assertExit(self.cli("run", "status", scenario="staleincident"), 1)
        self.assertErrorShape(run, "PROVIDER_ERROR")
        self.assertIn_("providerOpenIncidents: 0", run)
        self.assertNotIn_("Firebase Hosting custom domains API returning 501", run)

    def test_a_5xx_with_no_open_incident_does_not_invent_one(self):
        run = self.assertOk(
            self.cli("diagnose", "run", "status", scenario="staleincident")
        )
        self.assertIn_("openIncidents: 0", run)
        self.assertIn_("verdict: provider-side-no-published-incident", run)
        self.assertNotIn_("Firebase Hosting custom domains API returning 501", run)


if __name__ == "__main__":
    unittest.main()
