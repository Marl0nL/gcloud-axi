# gcloud-axi

An agent-ergonomic command-line wrapper around the `gcloud` CLI.

`gcloud` is built for humans: verbose output, interactive prompts, and answers
that take several round trips to assemble. `gcloud-axi` re-presents the common
inspection workflows for AI agents and automation:

- **Token-efficient output** - compact tabular schemas with minimal default
  fields and `--full` / `--fields` escape hatches.
- **Pre-computed aggregates** - one `overview` call answers what would
  otherwise take half a dozen `gcloud describe`/`list` invocations.
- **Definitive empty states** - "0 results" is printed, never silence.
- **Structured errors and clean exit codes** - 0 success, 1 error, 2 unknown
  flag; errors carry a `hint:` line with the next step.
- **No interactive prompts, ever.**
- **Contextual next-step hints** - every result ends with `help[]` command
  templates.

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

## Status

Early development. Command surface and config format may change.
