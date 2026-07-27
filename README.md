# gcloud-axi

An agent-ergonomic command-line wrapper around the `gcloud` CLI.

`gcloud` is built for humans: verbose output, interactive prompts, and answers
that take several round trips to assemble. `gcloud-axi` re-presents the common
inspection workflows for AI agents and automation:

- **Token-efficient output** - compact tabular schemas with minimal default
  fields and a `--full` escape hatch on every command that truncates.
- **Pre-computed aggregates** - one `overview` call answers what would
  otherwise take half a dozen `gcloud describe`/`list` invocations.
- **Definitive empty states** - "0 results" is printed, never silence.
- **Structured errors and clean exit codes** - 0 success, 1 error, 2 unknown
  flag; every error names the next step.
- **No interactive prompts, ever.**
- **Contextual next-step hints** - every result ends with `help[]` command
  templates.

It is a single-file-per-module Python 3 package with **no dependencies beyond
the standard library**, and it shells out only to `gcloud` itself.

Built to the [axi conventions](https://axi.md/).

## Install

Requires Python 3.8+ and the Google Cloud SDK on `PATH`.

```
git clone https://github.com/Marl0nL/gcloud-axi.git
cd gcloud-axi
make install                 # symlinks into /usr/local/bin
# or: ln -s "$PWD/gcloud-axi" ~/.local/bin/gcloud-axi
```

The entry point resolves its own real path, so symlinking it anywhere works.

Verify:

```
gcloud-axi --version
gcloud-axi --help
```

## Quick start

Everything below works against your ambient `gcloud` credentials with no
configuration file at all.

```
$ gcloud-axi
tool: gcloud-axi 1.0.0
description: token-efficient, structured gcloud output for agents and automation

credential:
  account: you@example.com
  type: user
  scopedConfigDir: null
  tier: (not a tiered credential)

context:
  project: my-project
  projectSource: gcloud configuration
  region: us-central1
  configPath: /home/you/.config/gcloud-axi/config
  configExists: false
  tiering: not configured (optional)

health: 1/2 Cloud Run services READY; not ready: worker-service

help[4]:
  Run `gcloud-axi overview` for the whole-project picture
  Run `gcloud-axi run status` for Cloud Run service detail
  Run `gcloud-axi grant --help` if you want the optional credential-tiering layer
  Run `gcloud-axi --help` for the full command list
```

Running with no arguments prints live state, not help text.

## Commands

| Command | Answers |
| --- | --- |
| `gcloud-axi` | Which credential, which project, is anything on fire |
| `overview` | Services, jobs, databases and error volume in one call |
| `run status [service]` | Traffic split, serving revision, image digest, env/secret **names** |
| `run revisions [service]` | Revision history with traffic share and status |
| `logs <service\|job>` | Bounded, truncated log reads with an explicit window |
| `jobs` | Cloud Run jobs with schedule and last execution result |
| `jobs run <job>` | Start one execution of an existing job |
| `sql status [instance]` | Instance state, version, tier, backups, flags |
| `sql proxy [instance]` | **Prints** the `cloud-sql-proxy` invocation; runs nothing |
| `secrets` | Secret Manager **metadata only** - never a payload |
| `iam audit` | Bindings pre-joined by member across project, SA and bucket policies |
| `builds` | Recent Cloud Build runs with a pass/fail summary |
| `grant` / `ledger` / `revoke` | The optional credential-tiering layer (below) |

Every subcommand has its own `--help`.

### The aggregate

```
$ gcloud-axi overview
context:
  project: my-project
  projectSource: gcloud configuration
  region: us-central1
  generated: 2025-03-04T11:20:08Z

services[2]{name,status,servingRevision,digest,lastDeploy}:
  my-service,READY,my-service-00007-abc,sha256:a1b2c3d4e5f6,3h ago
  worker-service,RevisionFailed,worker-service-00007-abc,sha256:0f1e2d3c4b5a,2d ago

jobs[2]{name,schedule,lastRun,result}:
  my-job,null,9h ago,SUCCEEDED
  nightly-job,0 2 * * *,8h ago,FAILED

sql[1]{name,state,databaseVersion,tier,availability}:
  my-instance,RUNNABLE,POSTGRES_15,db-custom-2-7680,ZONAL

errors:
  window: 1h
  count: 3
  capped: false
  sources: 2
  errorsBySource[2]{source,count}:
    my-service,2
    nightly-job,1
```

Sections degrade independently: an API you cannot reach becomes a `warnings[]`
line rather than a failed command.

### Bounded log reads

```
$ gcloud-axi logs my-service --since 6h --severity error --limit 10
query:
  target: my-service
  type: any
  project: my-project
  since: 6h
  window: 2025-03-04T05:20:31Z .. now
  severity: ERROR
  match: null
  limit: 10
entries[2]{time,severity,revision,message}:
  2025-03-04T11:02Z,ERROR,my-service-00007-abc,upstream request timed out after 30s while calling ...
  2025-03-04T09:48Z,ERROR,my-service-00007-abc,connection pool exhausted
note: 1 of 2 messages truncated at 200 chars (longest 267) - use --full for complete text
```

An empty window says so explicitly, with the window restated, so you can tell
"nothing happened" apart from "the command did not work":

```
entries: []
count: 0
note: 0 entries for my-service in the last 6h at severity >= ERROR - the window is correct, there is nothing in it
```

### Errors

Errors are structured, go to **stdout**, and carry the next step:

```
$ gcloud-axi run status; echo "exit=$?"
error: the active credential is not permitted to run `gcloud run services list`
code: PERMISSION_DENIED
detail: "ERROR: (gcloud.run.services.list) PERMISSION_DENIED: Permission 'run.services.list' denied ..."
help[3]:
  Run `gcloud-axi` to confirm which credential and project are active
  Run `gcloud-axi iam audit --member <member>` to see what that identity holds
  If you hold a scoped short-lived token, request a higher tier from whoever issued it
exit=1
```

Unknown flags fail loud rather than being ignored:

```
$ gcloud-axi builds --recent; echo "exit=$?"
error: unknown flag "--recent"
code: UNKNOWN_FLAG
help[1]:
  Run `gcloud-axi builds --help` to see supported flags
exit=2
```

## Configuration

Configuration is **optional**. Without it, `gcloud-axi` uses your ambient
credentials and whatever project `gcloud` itself is configured with.

Path: `$GCLOUD_AXI_CONFIG`, else `~/.config/gcloud-axi/config`
(`$XDG_CONFIG_HOME` is honoured). The file is plain `KEY=VALUE`; it is parsed,
never executed. A fully commented template ships as
[`config.example`](config.example).

**Project resolution**, in order, and stated in `--help`:

1. the `--project` flag
2. `PROJECT` in the config file
3. whatever `gcloud` itself is configured with

There is no built-in default. If all three come up empty, the command fails
with a structured error instead of guessing. Region resolution has the same
shape (`--region`, then `REGION`, then gcloud's `run/region`); with none set,
region-scoped listings span every region.

## Optional: credential tiers via impersonation

For multi-agent setups that share one owner credential, `gcloud-axi` ships an
optional issuance layer: `grant` mints a short-lived downscoped access token by
impersonating a service account you designate per tier, writes it into an
isolated gcloud config directory, and records the issuance in an append-only
local ledger. An agent holding such a token cannot exceed the target service
account's IAM roles - even calling raw `gcloud` directly.

The tier layout is entirely declarative (`~/.config/gcloud-axi/config`): you
define the tiers, the target service accounts, and the projects they may be
issued for. The wrapper works fully without this layer configured.

Be aware of what this is and is not: the tier is enforced by IAM **on the
issued token**. It does not by itself stop a process on the same machine from
using other credentials it can read - that separation is an OS-level concern.

### Declaring tiers

Nothing about the layout below is built into the tool. Names, count, targets
and allowed projects are all yours; adopting a completely different layout
needs zero code changes.

```
TIERS=inspect,operate

TIER_INSPECT_SERVICE_ACCOUNT=inspect@my-project.iam.gserviceaccount.com
TIER_INSPECT_PROJECTS=my-project
TIER_INSPECT_TTL=3600
TIER_INSPECT_DESCRIPTION="read-only inspection"

TIER_OPERATE_SERVICE_ACCOUNT=operate@my-project.iam.gserviceaccount.com
TIER_OPERATE_PROJECTS=my-project
TIER_OPERATE_TTL=1800
```

The tier name maps to its keys by upper-casing and turning hyphens into
underscores: tier `read-only` reads `TIER_READ_ONLY_*`. A tier absent from
`TIERS` does not exist, and `TIER_<NAME>_PROJECTS` is a strict allow-list -
there is no implicit allow.

The issuing identity needs
`roles/iam.serviceAccountTokenCreator` on each target service account. The
target's own roles *are* the tier; grant the target exactly what that tier
should be able to do.

### Issuing

```
$ gcloud-axi grant --tier inspect --task audit-staging --ttl 1800
granted:
  tier: inspect
  tierDescription: read-only inspection
  task: audit-staging
  project: my-project
  serviceAccount: inspect@my-project.iam.gserviceaccount.com
  ttlSeconds: 1800
  issuedAt: 2025-03-04T11:31:02Z
  expiresAt: 2025-03-04T12:01:02Z
  configDir: /home/you/work/.gcloud-agent
  replacedPreviousGrant: false
  tokenPrinted: false
env[2]:
  export CLOUDSDK_CONFIG="/home/you/work/.gcloud-agent"
  export GOOGLE_OAUTH_ACCESS_TOKEN="$(cat "/home/you/work/.gcloud-agent/access_token")"
files[4]{path,mode,holds}:
  /home/you/work/.gcloud-agent/access_token,0600,the access token
  /home/you/work/.gcloud-agent/configurations/config_default,0600,gcloud settings pointing at that token
  /home/you/work/.gcloud-agent/env.sh,0600,the env lines above; no token value
  /home/you/work/.gcloud-agent/grant.json,0600,issuance metadata; no token value
```

The recipient runs `source .gcloud-agent/env.sh`. Inside that environment:

- `gcloud-axi` is scoped to the tier;
- **raw `gcloud` is scoped too** - the config directory sets
  `auth/access_token_file`, so a direct `gcloud` call uses the same token. That
  is the property that makes the arrangement resistant to accidents rather than
  merely advisory;
- Google client libraries that honour `GOOGLE_OAUTH_ACCESS_TOKEN` pick up the
  same credential.

**The token value is never printed.** It goes to one file, mode 0600, inside a
directory created 0700. It is absent from stdout, from the ledger, from
`env.sh` and from `grant.json` - the printed environment line reads the file
rather than carrying the value. This is covered by tests.

`gcloud-axi` run inside such an environment reports the tier and its expiry:

```
credential:
  account: inspect@my-project.iam.gserviceaccount.com
  type: service_account
  scopedConfigDir: /home/you/work/.gcloud-agent
  tier: inspect
  issuedFor: audit-staging
  expiresAt: 2025-03-04T12:01:02Z
  expiresIn: 24m
  expired: false
```

### Refusals

`grant` refuses anything the config does not declare, with the fix in the
error:

```
$ gcloud-axi grant --tier superuser --task x; echo "exit=$?"
error: tier "superuser" is not declared in this configuration
code: UNKNOWN_TIER
configPath: /home/you/.config/gcloud-axi/config
help[2]:
  Declared tiers: inspect, operate
  Add it to TIERS in /home/you/.config/gcloud-axi/config together with its TIER_<NAME>_* keys
exit=1

$ gcloud-axi grant --tier operate --task x --project my-other-project; echo "exit=$?"
error: tier "operate" is not allowed to be issued for project "my-other-project"
code: PROJECT_NOT_ALLOWED
tier: operate
requestedProject: my-other-project
help[3]:
  Tier operate allows: my-project
  Run the command with --project <one of those> to issue against an allowed project
  To widen the tier, edit TIER_OPERATE_PROJECTS in /home/you/.config/gcloud-axi/config - this tool has no built-in policy
exit=1
```

With no tiers declared at all, the three tiering subcommands explain how to
configure and change nothing; every other command is unaffected.

### Ledger and revocation

`ledger` reads the append-only issuance log. `grant` is its only writer and
only ever appends; there is deliberately no subcommand that edits or deletes a
line.

```
$ gcloud-axi ledger --active
ledger:
  path: /home/you/.config/gcloud-axi/ledger.log
  exists: true
  totalRecords: 12
  matched: 2
  shown: 2
  appendOnly: true
  tokensRecorded: never
issuances[2]{task,tier,project,issued,expires,state}:
  audit-staging,inspect,my-project,2025-03-04T11:31Z,2025-03-04T12:01Z,active
  rerun-nightly,operate,my-project,2025-03-04T11:05Z,2025-03-04T11:35Z,active
```

Ledger path: `$GCLOUD_AXI_LEDGER`, else `LEDGER` in the config, else
`~/.config/gcloud-axi/ledger.log`.

`revoke --tier <name>` prints the three ways to end a tier's outstanding
credentials - natural expiry, disabling the target service account, removing
the token-creator binding - along with who is currently affected. It runs
**nothing**; you copy the rung you want.

## What this tool will not do

- **It never reads a secret payload.** `secrets` is metadata only, and
  `gcloud secrets versions access` is refused at the process boundary rather
  than merely left unimplemented. Agent transcripts, terminal scrollback, shell
  history and CI logs are all durable copies; a tool that can print a payload
  will eventually print one somewhere nobody meant to write it.
- **It never prints a token value** - not to the terminal, a log, or the
  ledger.
- **It never prompts.** Every command is scriptable; `--quiet` is passed to
  gcloud on every call.
- **It does not cover gcloud's surface.** It covers the dozen questions that
  actually get asked, well. For the long tail, use `gcloud` directly - inside a
  scoped environment that is safe, because the credential floor is already set.

## Testing

```
./test.sh          # or: make test
./test.sh test_tiering -v
```

The suite is fully offline. A fake `gcloud` shim (`tests/shim/gcloud`) is
placed first on `PATH` and replays recorded JSON fixtures, including failure
shapes - permission denied, expired credential, disabled API, empty project. No
test can reach a real `gcloud`, a real credential, or the network, and the shim
exits loudly on any call it has no fixture for rather than returning silence.

[`VERIFY.md`](VERIFY.md) lists the live smoke checks a maintainer runs by hand
against real infrastructure before a release; those are deliberately not part
of the automated suite.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: keep it generic, keep the
output contract, keep the tests offline, and never print a token or a payload.

## Licence

MIT - see [LICENSE](LICENSE).
