#!/usr/bin/env bash
# Run the offline test suite and shell checks.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$repo_root"
temporary_home="$(mktemp -d "${TMPDIR:-/tmp}/agent-use-test-home.XXXXXX")"
trap 'rm -rf "$temporary_home"' EXIT
export HOME="$temporary_home"
unset AGENT_USE_CONFIG
python3 -B -m unittest discover -s tests
bash -n scripts/install.sh scripts/test.sh tests/test_install.sh
bash tests/test_install.sh
