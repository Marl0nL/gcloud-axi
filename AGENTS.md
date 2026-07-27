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

Three modules carry the invariants and are worth reading before changing
anything:

- `gcloudcmd.py` - the **only** place that spawns a process.
- `toon.py` - the **only** place that formats output.
- `tiering.py` - the **only** place that writes a credential to disk.

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
- `<key>.err` plus optional `<key>.exit` simulate failures;
- scenarios: `happy`, `empty`, `denied`, `expired`, `partial`, `noregion`. A
  scenario other than `happy` falls back to `happy` for keys it does not define,
  so a scenario only needs the files it changes.

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

When adding either kind of thing, encode reality in the fixtures (a flagless
vector that errors, an exact-bytes assertion) rather than assuming, and add the
check to [VERIFY.md](VERIFY.md).

An unmatched key makes the shim exit 70 with a loud message. That is
intentional - a silently empty fixture turns a routing bug into a passing test.

## Two invariants that must not regress

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
argument vectors match real gcloud, and that the isolated config directory
really scopes a raw `gcloud` call. [VERIFY.md](VERIFY.md) is the by-hand
checklist for that, run against real infrastructure before a release. Do not
automate it onto shared credentials.

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
