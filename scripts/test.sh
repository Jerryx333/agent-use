#!/usr/bin/env bash
# Run the offline test suite and shell checks.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$repo_root"
python3 -B -m unittest discover -s tests
bash -n scripts/install.sh scripts/test.sh tests/test_install.sh
bash tests/test_install.sh
