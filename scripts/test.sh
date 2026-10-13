#!/usr/bin/env bash
# Run the offline test suite and shell checks.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$repo_root"
exec python3 -B "$repo_root/tests/run.py" "$@"
