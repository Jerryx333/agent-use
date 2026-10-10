#!/usr/bin/env bash
# Verify installation, idempotence, and conflict rejection.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd -P)"
temporary_root="$(mktemp -d "${TMPDIR:-/tmp}/agent-use-install-test.XXXXXX")"
trap 'rm -rf "$temporary_root"' EXIT
for iteration in 1 2; do
  SKILLS_DIR="$temporary_root/skills" bash "$repo_root/scripts/install.sh"
done
[[ "$(readlink "$temporary_root/skills/agent-use")" == "$repo_root/skills/agent-use" ]]
[[ "$(ls -A "$temporary_root/skills")" == agent-use ]]
mkdir -p "$temporary_root/conflicts/agent-use"
printf 'keep' > "$temporary_root/conflicts/agent-use/marker"
if SKILLS_DIR="$temporary_root/conflicts" bash "$repo_root/scripts/install.sh"; then
  exit 1
fi
[[ "$(cat "$temporary_root/conflicts/agent-use/marker")" == keep ]]
mkdir -p "$temporary_root/links"
ln -s "$temporary_root/missing" "$temporary_root/links/agent-use"
if SKILLS_DIR="$temporary_root/links" bash "$repo_root/scripts/install.sh"; then
  exit 1
fi
[[ "$(readlink "$temporary_root/links/agent-use")" == "$temporary_root/missing" ]]
printf 'Installation checks passed\n'
