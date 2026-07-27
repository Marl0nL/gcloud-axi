# Live verification checklist

The automated suite (`./test.sh`) is entirely offline: it runs against a fake
`gcloud` shim and can never reach real infrastructure. That is deliberate, and
it leaves exactly one class of claim unverified - that the argument vectors we
build are the ones real `gcloud` accepts, and that the isolated credential
directory really does scope a raw `gcloud` call.

This checklist is the manual pass a maintainer runs against a real project
before tagging a release. It is not part of CI and must not be automated onto
shared credentials.

Placeholders throughout: replace `my-project`, `my-service`, `my-job`,
`my-instance`, `inspect@my-project.iam.gserviceaccount.com` with your own.

## 0. Prerequisites

- A real project you are allowed to read.
- `gcloud` authenticated as a principal that can read it.
- For section 3 only: a service account you may impersonate, and
  `roles/iam.serviceAccountTokenCreator` on it.
- A scratch directory. **Do not** run section 3 with `--dest` pointing anywhere
  you would not `rm -rf`.

```
export PROJECT=my-project
```

## 1. Read commands

Each should exit 0 and print a populated result, or a definitive empty state.
Confirm the empty states are explicit rather than silent.

```
gcloud-axi --project "$PROJECT"
gcloud-axi overview --project "$PROJECT"
gcloud-axi overview --project "$PROJECT" --full
gcloud-axi run status --project "$PROJECT"
gcloud-axi run status my-service --project "$PROJECT"
gcloud-axi run revisions my-service --project "$PROJECT" --limit 5
gcloud-axi logs my-service --project "$PROJECT" --since 1h
gcloud-axi logs my-service --project "$PROJECT" --since 24h --severity error --full
gcloud-axi jobs --project "$PROJECT"
gcloud-axi jobs --project "$PROJECT" --full
gcloud-axi sql status --project "$PROJECT"
gcloud-axi sql status my-instance --project "$PROJECT" --full
gcloud-axi secrets --project "$PROJECT"
gcloud-axi secrets --project "$PROJECT" --versions
gcloud-axi iam audit --project "$PROJECT"
gcloud-axi iam audit --project "$PROJECT" --scope project --full
gcloud-axi builds --project "$PROJECT" --limit 10
```

Check while reading the output:

- [ ] Counts match what the Cloud console shows.
- [ ] `run status` shows env var **names** and secret **names**, never a value.
- [ ] `secrets` shows no payload anywhere.
- [ ] If any Cloud Run job has a Cloud Scheduler trigger in the resolved
      region, `jobs` and `overview` show its `schedule` - not `null` and not a
      `schedules unavailable` warning. Cloud Scheduler is a per-location API,
      so this is the check that the location is actually being passed. With no
      region resolvable (`--region` unset, no `REGION`, no gcloud `run/region`)
      the warning must name *that* as the reason rather than claiming the API
      is unreachable.
- [ ] Every result ends with `help[]` lines whose placeholders are unresolved.
- [ ] Timestamps and ages look right for the region you are in.

## 2. Contract checks

```
gcloud-axi builds --nonsense ; echo "expect 2, got $?"
gcloud-axi nosuchcommand     ; echo "expect 2, got $?"
gcloud-axi run status no-such-service-xyz --project "$PROJECT" ; echo "expect 1, got $?"
gcloud-axi overview --project this-project-does-not-exist-xyz  ; echo "expect 1, got $?"
```

- [ ] Errors printed to **stdout**; stderr stays empty (`2>/dev/null` changes
      nothing about what you see).
- [ ] No command ever prompts, even with stdin attached to a terminal.

`sql proxy` must print and run nothing:

```
gcloud-axi sql proxy my-instance --project "$PROJECT"
```

- [ ] `started: false`, a `command[]` line you can copy, and no proxy process
      started (`pgrep -f cloud-sql-proxy` finds nothing new).

## 3. The tiering layer (only if you use it)

Declare a tier in your config first - see `config.example`.

```
export SCRATCH="$(mktemp -d)"
gcloud-axi grant --tier inspect --task verify-release --ttl 900 --dest "$SCRATCH/scoped"
```

Confirm on the spot:

- [ ] The token value does **not** appear in the output.
- [ ] `ls -ld "$SCRATCH/scoped"` shows `drwx------` (0700).
- [ ] `ls -l "$SCRATCH/scoped"` shows `-rw-------` (0600) on every file.
- [ ] `grep -r . "$SCRATCH/scoped" --exclude=access_token` finds no token.
- [ ] The two files gcloud reads as a *value* carry no trailing newline:
      `wc -c < "$SCRATCH/scoped/active_config"` is exactly the length of the
      configuration name (7 for `default`), and
      `xxd "$SCRATCH/scoped/access_token" | tail -1` does not end in `0a`.
      A newline in `active_config` makes gcloud look for `config_default\n`,
      silently lose the active account, and break every raw `gcloud` call
      below - the offline suite cannot see this.
- [ ] `grep -c . "$GCLOUD_AXI_LEDGER"` grew by exactly one line, and that line
      contains no token.

Then verify the scoping property - the whole point of the exercise:

```
# In a fresh shell:
source "$SCRATCH/scoped/env.sh"

gcloud-axi                                    # reports tier + expiry
gcloud config list                            # shows the isolated config
gcloud run services list --project "$PROJECT" # RAW gcloud, must succeed read-only
gcloud auth list                              # shows the impersonated account
```

- [ ] `gcloud-axi` reports `tier: inspect` and a plausible `expiresIn`.
- [ ] **Raw `gcloud` reads succeed** - proving `auth/access_token_file` took
      effect and the environment is scoped, not just the wrapper.
- [ ] A write the tier should not have is **refused by Google**, not by us,
      e.g. `gcloud run services delete some-service --region <region>` returns
      `PERMISSION_DENIED`. Pick something harmless that is genuinely outside
      the tier.
- [ ] Your own shell, outside this environment, still has its normal
      credential (`gcloud auth list` in the original shell is unchanged).

Client-library path, if you depend on it:

```
python3 -c "import os; assert os.environ['GOOGLE_OAUTH_ACCESS_TOKEN']; print('set')"
```

- [ ] A Google client library in that environment authenticates as the tier's
      service account.

Expiry behaviour:

- [ ] Wait out a short TTL (issue one with `--ttl 60`), then run any read
      command. It must fail with `code: CREDENTIAL_EXPIRED` and a help line
      about obtaining a fresh credential - not an opaque 401.

Ledger and revocation:

```
gcloud-axi ledger --active
gcloud-axi ledger --task verify-release --full
gcloud-axi revoke --tier inspect
```

- [ ] `ledger` lists the issuance with `state: active`.
- [ ] `revoke` prints all three rungs with your real service-account email
      substituted, says `ranAnything: false`, and **changes nothing** - confirm
      the service account is still enabled afterwards.

Clean up:

```
rm -rf "$SCRATCH"
```

## 4. After the release

- [ ] `gcloud-axi --version` reports the tagged version.
- [ ] `make install` then `gcloud-axi` from a different directory works (the
      symlink resolution is correct).
