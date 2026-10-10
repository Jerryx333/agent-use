#!/usr/bin/env bash
# Install the dispatcher skill idempotently without overwriting conflicts.
# Run: bash scripts/install.sh; requires Bash, ln, and a local checkout.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd -P)"
skills_dir="${SKILLS_DIR:-$HOME/.agents/skills}"
for skill in agent-use; do
  target="$skills_dir/$skill"
  source_path="$repo_root/skills/$skill"
  if [[ -e "$target" || -L "$target" ]]; then
    if [[ ! -L "$target" || "$(readlink "$target")" != "$source_path" ]]; then
      printf 'Installation failed: conflicting content at %s; left unchanged\n' "$target" >&2
      exit 1
    fi
  fi
done
mkdir -p "$skills_dir"
for skill in agent-use; do
  target="$skills_dir/$skill"
  if [[ ! -L "$target" ]]; then
    ln -s "$repo_root/skills/$skill" "$target"
  fi
  printf 'Installed: %s\n' "$target"
done
