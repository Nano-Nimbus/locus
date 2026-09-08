#!/usr/bin/env bash
# install-skills.sh: sync the Claude skills from skills/claude/ to ~/.claude/skills/
#
# Usage:
#   ./scripts/install-skills.sh           # install every skill
#   ./scripts/install-skills.sh --dry-run # show what would be copied, write nothing
#
# skills/claude/ is the only maintained skill set. The per-runtime variants
# (skills/codex/, skills/gemini/) were removed; another runtime adapts these
# files rather than getting a vendored copy.
#
# CLAUDE_SKILLS_DIR overrides the destination.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_SRC="$REPO_ROOT/skills/claude"
SKILLS_DST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"

DRY_RUN=false
case "${1:-}" in
  --dry-run) DRY_RUN=true ;;
  "") ;;
  *) echo "usage: $(basename "$0") [--dry-run]" >&2; exit 2 ;;
esac

if [[ ! -d "$SKILLS_SRC" ]]; then
  echo "ERROR: skills source directory not found: $SKILLS_SRC" >&2
  exit 1
fi

# An empty or root destination would make the per-skill refresh below dangerous.
if [[ -z "$SKILLS_DST" || "$SKILLS_DST" == "/" ]]; then
  echo "ERROR: refusing to install to '$SKILLS_DST'" >&2
  exit 1
fi

count=0
for skill_dir in "$SKILLS_SRC"/*/; do
  # Only a directory holding a SKILL.md is a skill.
  [[ -f "$skill_dir/SKILL.md" ]] || continue
  skill_name="$(basename "$skill_dir")"
  dst="$SKILLS_DST/$skill_name"
  count=$((count + 1))
  if $DRY_RUN; then
    echo "[dry-run] would install: $skill_name -> $dst"
    continue
  fi
  mkdir -p "$SKILLS_DST"
  # Replace rather than merge: cp -r into an existing directory nests the
  # source inside it, so repeated runs used to build ~/.claude/skills/locus/locus.
  rm -rf -- "$dst"
  cp -r "$skill_dir" "$dst"
  echo "installed: $skill_name"
done

echo "---"
if $DRY_RUN; then
  echo "$count skill(s) would be installed to $SKILLS_DST"
else
  echo "$count skill(s) installed to $SKILLS_DST"
fi
