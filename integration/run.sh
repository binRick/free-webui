#!/usr/bin/env bash
# One-shot integration run: build the backend image, bring up Ollama + pull the
# model, run the HTTP integration suite against the live backend, then tear down.
#
#   ./run.sh             build, test, tear down (default)
#   ./run.sh --keep      leave the stack running afterwards (re-run with --no-build)
#   ./run.sh --no-build  skip the image build (use the existing image)
#
# Exit code is the pytest exit code, so this works as a local gate.
set -euo pipefail
cd "$(dirname "$0")"

KEEP=0
NO_BUILD=0
for arg in "$@"; do
  case "$arg" in
    --keep)     KEEP=1 ;;
    --no-build) NO_BUILD=1 ;;
    -h|--help)  echo "usage: run.sh [--keep] [--no-build]"; exit 0 ;;
    *)          echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

cleanup() {
  if [ "$KEEP" -eq 1 ]; then
    echo "--keep: stack left running. Tear down with: docker compose down (add -v to drop the model cache)"
  else
    # Drop containers + network but KEEP the ollama-models volume so re-runs
    # don't re-pull the model. The backend DB is in the container layer, so it's
    # fresh every run regardless. Use `docker compose down -v` to purge the cache.
    echo "tearing down (model cache preserved)…"
    docker compose down --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [ "$NO_BUILD" -eq 0 ]; then
  echo "building backend image…"
  docker compose build
fi

echo "starting Ollama + pulling the model (first run downloads it; cached afterwards)…"
echo "then running the integration suite against the live backend."
# `run` starts the dependency chain (ollama -> model pull -> backend healthy),
# then runs pytest. --rm cleans up the one-off tests container.
set +e
docker compose run --rm tests
code=$?
set -e

if [ "$code" -eq 0 ]; then
  echo "integration suite PASSED ✓"
else
  echo "integration suite FAILED (exit $code) ✗"
fi
exit "$code"
