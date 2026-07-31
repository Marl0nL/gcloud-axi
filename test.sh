#!/usr/bin/env bash
#
# The single entry point for the test suite.
#
# Everything here runs offline against the fake-gcloud shim in tests/shim. No
# test may reach a real gcloud, a real credential, or the network - the shim is
# placed first on PATH and fails loudly on any call it has no fixture for.
#
#   ./test.sh                 run everything
#   ./test.sh test_tiering    run one module
#   ./test.sh -v              verbose
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"

# A real gcloud on PATH must not be reachable from the suite; unset anything
# that could point the tool at one.
unset GCLOUD_AXI_GCLOUD || true
unset CLOUDSDK_CONFIG || true

# The provider-status lookup is the tool's only network read. Off by default for
# anything the suite runs in-process; tests/harness.py strips this for the
# subprocess CLI runs and points them at a `file://` fixture instead, so both
# halves of the suite stay offline without either relying on the other.
export GCLOUD_AXI_PROVIDER_STATUS=off

echo "== syntax =="
"$PYTHON" -m compileall -q src tests gcloud-axi >/dev/null
echo "ok"

echo
echo "== unit and end-to-end tests (offline, fake-gcloud shim) =="
if [ "$#" -gt 0 ] && [[ "$1" != -* ]]; then
  exec "$PYTHON" -m unittest "tests.$1" -v
fi
exec "$PYTHON" -m unittest discover -s tests -p 'test_*.py' -t . "$@"
