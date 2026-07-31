# AGENTS.md

Notes for anyone - human or agent - working on `gcloud-axi` itself. For what the
tool *does*, read [README.md](README.md); for the rules a contribution must
meet, read [CONTRIBUTING.md](CONTRIBUTING.md). This file records the things
neither of those makes obvious.

## Shape of the codebase

- `gcloud-axi` - the executable. Resolves its own real path so a symlink onto
  `PATH` works, then hands off to `src/gcloud_axi/cli.py`.
- `src/gcloud_axi/` - stdlib-only Python package. No third-party dependencies,
  by design; adding one needs a strong reason.
- `src/gcloud_axi/commands/` - one module per top-level command. Each exports
  `dispatch(ctx_factory, argv)` returning `(Out, exit_code)` or raising an
  `AxiError`.
- `tests/` - the offline suite. `tests/shim/gcloud` is the fake gcloud.

Five modules carry the invariants and are worth reading before changing
anything. Each is "the only place that can do X", and
`tests/test_invariants.py::SingleChokePointTest` asserts the "only" mechanically
rather than trusting it:

- `gcloudcmd.py` - the **only** place that spawns a process, and the **only**
  place that decides which credential a call runs as.
- `toon.py` - the **only** place that formats output.
- `tiering.py` - the **only** place that writes a credential to disk.
- `provider.py` - the **only** place that opens a network connection.
- `credentials.py` - the **only** place that probes a credential; it mints to
  prove liveness and never returns the minted value.

## Design reference

The output contract below is this project's reading of
[AXI](https://axi.md/) - the ten principles for agent-ergonomic CLIs. Before
adding a command, install the reference guidelines with
`npx skills add kunchenguid/axi` (it lands in `.agents/`, which is gitignored:
it is someone else's skill, not part of this tool) and read `gh-axi` /
`chrome-devtools-axi` output for the shapes the fleet already reads fluently.
Matching them beats inventing.

## The output contract

Every command obeys the same shape, and the tests in
`tests/test_invariants.py::OutputContractTest` enforce it mechanically:

- Exit `0` success, `1` error, `2` usage error. Unknown flags fail loud with
  exit 2 - never ignored, never abbreviated. `flags.py` exists instead of
  `argparse` for exactly this reason.
- Errors are structured and go to **stdout**; stderr is left for debug noise.
- Every list carries a count; every empty result prints `count: 0` and a `note:`
  saying what the zero means. Silence is never an answer.
- Large values truncate with a size hint stating the full length, and `--full`
  turns truncation off.
- Every result ends with `help[]` next-step templates whose placeholders stay
  unresolved (`<service>`, not the service you happened to look at).
- Every subcommand has `--help`, rendered through `helptext.render` so they all
  read alike.
- No interactive prompts anywhere; `--quiet` is appended to every gcloud call.

New commands are expected to follow this without being asked. If a change makes
one of the invariant tests awkward, the change is usually what is wrong.

## Testing rule: the shim, never the real gcloud

`./test.sh` (or `make test`) is the only entry point. The suite puts
`tests/shim/gcloud` first on `PATH` and points `HOME`/`XDG_CONFIG_HOME` at a
temp directory. **No test may call the real `gcloud`, use ambient credentials,
or touch the network.**

Since `provider.py` exists, the network half of that is worth proving rather than
asserting. `unshare -rn ./test.sh` runs the whole suite in a namespace with no
network at all; it must stay green. Run it after touching `provider.py`,
`harness.py` or any fixture named `incidents.json`.

Adding coverage for a new gcloud call means adding a fixture, not relaxing this:

- fixtures live in `tests/fixtures/<scenario>/<key>.json`;
- the key is the invocation's non-flag words joined by `_`
  (`run jobs executions list` → `run_jobs_executions_list`);
- the shim tries the longest key first, so `run_jobs_execute_my-job.json` beats
  `run_jobs_execute.json`;
- a key may be qualified by a long flag present in the invocation:
  `<key>.<flag>` is tried before the bare `<key>`. That is how one scenario
  replays two answers for the same subcommand depending on a flag - the reason
  it exists is that real gcloud refuses `scheduler jobs list` without
  `--location` and answers it with one, and the suite has to reproduce both to
  catch a missing flag at all;
- a flag-qualified key keeps the flag's own spelling, hyphens included -
  `run_services_list.access-token-file.json`, not `..._access_token_file.json`;
- `<key>.err` plus optional `<key>.exit` simulate failures;
- scenarios: `happy`, `empty`, `denied`, `expired`, `partial`, `noregion`,
  `outage` (a 5xx plus an open incident feed), `probeoutage` (the token mint
  itself 5xxes, so liveness is unverifiable rather than lapsed), `liveoutage`
  and `staleincident` (a REAL captured 501 with the real incident that belonged
  to it, open and then closed - see `tests/fixtures/liveoutage/SOURCE.md` for
  what is verbatim and what is reconstructed), and the four
  credential states -
  `adcfallback` (CLI lapsed, ADC live, read succeeds under ADC), `adclapsed`
  (the mirror image), `bothlapsed`, `identity` (ambient denied, another identity
  allowed). A scenario other than `happy` falls back to `happy` for keys it does
  not define, so a scenario only needs the files it changes.

Two fixtures are shared and easy to trip over. `auth print-access-token
--impersonate-service-account=…` is the *same vector* for `grant` and for
`diagnose --as`, so they cannot be given different tokens. And the incident feed
is not a gcloud call at all: `harness.incidents_url` points
`GCLOUD_AXI_STATUS_URL` at a scenario's `incidents.json` over `file://`, which
is what keeps the suite off the network - leaving it unset would send the tests
at status.cloud.google.com.

## What the offline suite structurally cannot catch

Live verification has already found two bugs of a kind no fixture can surface,
so treat this class with suspicion:

- **Argument vectors real gcloud rejects.** A fixture answers whatever key the
  shim routes to, so a call missing a required flag still "works" offline.
  Cloud Scheduler is per-location and needs `--location`; assume other APIs have
  their own such requirements.
- **The exact bytes of files gcloud reads as a value.** `active_config` holds a
  configuration *name*, so a trailing newline made gcloud look for
  `config_default\n`, silently lose the active account, and break every raw
  `gcloud` call in a scoped environment - while every offline test passed.
- **Whether a substituted credential actually takes effect.** The ADC fallback
  and `diagnose`'s retries hinge on real gcloud honouring
  `--access-token-file` on the vector in question. The shim answers whatever
  fixture the flag routes to, so offline this always "works". Verified against
  gcloud 576 as a documented global flag; that it scopes a real read is section
  2b of [VERIFY.md](VERIFY.md) and nothing else.

When adding any of these, encode reality in the fixtures (a flagless
vector that errors, an exact-bytes assertion) rather than assuming, and add the
check to [VERIFY.md](VERIFY.md).

An unmatched key makes the shim exit 70 with a loud message. That is
intentional - a silently empty fixture turns a routing bug into a passing test.

## Invariants that must not regress

**No secret payload path.** `gcloudcmd.FORBIDDEN_SEQUENCES` refuses any argument
vector containing `secrets versions access`, at the process boundary, before
anything is spawned. `secrets` is metadata-only and its `--help` says why.
Do not add a flag, an escape hatch, or a "just this once" path.

**No token value in output.** A minted token is written to exactly one file
(0600) inside a directory created 0700, and nowhere else - not stdout, not
stderr, not the ledger, not `env.sh`, not `grant.json`. The printed environment
line reads the file (`$(cat …/access_token)`) rather than carrying the value.
`tests/test_tiering.py` asserts this against a known fixture token; if you touch
`tiering.py` or `commands/grant.py`, keep those assertions passing rather than
adjusting them.

This now covers three token paths, not one: `grant`'s issued credential, the
ADC token a read falls back to, and the impersonated token `diagnose` re-issues
a call with. All three go through `tiering.write_scratch_token` /
`write_isolated_config`, and `tests/test_credentials.py` asserts against two
further fixture tokens. A token reaches gcloud as `--access-token-file=<path>`
and never as an argument value (visible in any process listing) or an
environment variable (visible in `/proc/<pid>/environ` for the whole subtree).

**Only proof counts as proof.** `credentials.PROVES_LAPSE` is an allow-list of
the outcomes that establish a credential is dead - today just
`CREDENTIAL_EXPIRED`. Everything else (a 5xx, a 429, a transport failure, an
empty response, an unrecognised error) reports `UNVERIFIABLE` and never offers a
login command. Do not invert this into a list of provider-side codes to exclude:
the next outage arrives with a code this file has never met, and the default
must not be to blame the operator's identity for it. The rule is pinned by
`tests/test_credentials.py::ProbeNeverBlamesTheOperatorTest`, which parametrises
over failure shapes including unseen ones and carries the mirror assertion that
a genuine rejection still reports `LAPSED` with its login fix - without that
half, "never say lapsed" is a passing test and a broken tool.

**No fallback on a mutating call.** `gcloudcmd.is_read_only` is the gate for
both the automatic ADC fallback and `diagnose`'s cross-identity retry. It is an
allow-list of read verbs *plus* a mutating-verb deny-list, checked anywhere in
the vector rather than at the end - `logging read <filter>` and `sql instances
describe <name>` both carry a positional after the verb. Anything unrecognised
gets no fallback, so a call added later is excluded by default. `auth` and
`config` vectors are excluded outright: asking them as a borrowed identity
answers a different question from the one asked.

The ledger is append-only by construction: `grant` is the only writer and it
only ever appends. There is deliberately no subcommand that edits or deletes a
line, and adding one would defeat the point.

## Nothing organisation-specific in the tree

This repository is public. No real project id, service-account email, bucket,
org id or IAM binding may appear in code, docs, help text, fixtures, tests or
commit messages - use `my-project`,
`inspect@my-project.iam.gserviceaccount.com` and friends.

Policy is **configuration, not code**: which tiers exist, what they target,
which projects they may be issued for, and for how long all come from the config
file. There is no built-in tier name, no built-in service account, no built-in
project, and no built-in default project. A stranger with a completely different
layout must be able to adopt the tool with zero code edits.
`tests/test_invariants.py::PublicSafeFixturesTest` and
`tests/test_tiering.py::StandsAloneTest` guard both halves of this.

## Live verification is manual

Because the suite is offline, one class of claim is untestable in CI: that our
argument vectors match real gcloud, that the isolated config directory really
scopes a raw `gcloud` call, and that a read really does keep working under ADC
when the CLI credential is lapsed. [VERIFY.md](VERIFY.md) is the by-hand
checklist for that, run against real infrastructure before a release. Do not
automate it onto shared credentials.

Section 2b of that checklist covers the credential subsystem, including how to
simulate a lapsed CLI credential safely - point `CLOUDSDK_CONFIG` at an empty
directory rather than touching your real credential.

## CI

`.github/workflows/ci.yml` runs `./test.sh` on a Python matrix (README's
floor plus latest stable) and a `ruff check` lint job - both credential-free
and hermetic, matching the offline-only rule above. `ruff.toml` pins the
rule selection to pyflakes plus pycodestyle errors (`E4`, `E7`, `E9`, `F`)
rather than ruff's full default set, since the broader set (notably
`UP031`, percent-formatting) would flag the codebase's existing style
wholesale rather than catching real bugs. Widen it deliberately, not as a
side effect of a ruff upgrade.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
