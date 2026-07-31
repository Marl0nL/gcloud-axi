# Contributing

Issues and pull requests are welcome.

Ground rules:

- Keep the tool generic. No organisation-specific project ids, service-account
  emails, bucket names, or policy baked into code or fixtures - configuration
  is declarative, examples use obvious placeholders (`my-project`,
  `inspect@my-project.iam.gserviceaccount.com`).
- No interactive prompts anywhere; every command must be scriptable.
- Every command keeps the output contract: minimal default schema, total
  counts, definitive empty states, structured errors, exit 0/1/2, `help[]`
  next-step hints, consistent `--help`.
- Tests run offline against the fake-gcloud fixture shim; nothing in the test
  suite may call the real `gcloud` or touch a network.
- Never print a token value to the terminal, a log, or the ledger.

## Getting set up

Python 3.8+ is the only requirement; the package uses the standard library
only. Adding a third-party dependency needs a strong reason.

```
./test.sh                    # or: make test
./test.sh test_tiering -v    # one module
```

The suite is end-to-end: it runs the real CLI as a subprocess against a fake
`gcloud` placed first on `PATH`. To cover a new gcloud call, add a fixture
under `tests/fixtures/<scenario>/` rather than loosening the isolation - the
shim exits loudly on any call it has no fixture for.

[AGENTS.md](AGENTS.md) documents the module layout, the fixture naming rules,
and the invariants that must not regress. Read it before a first change.

[VERIFY.md](VERIFY.md) is the manual live checklist a maintainer runs before a
release, covering the things an offline suite cannot prove.
