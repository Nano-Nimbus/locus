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

# An empty, root, or source-tree destination would make the per-skill
# `rm -rf` below dangerous. A plain string comparison against "/" does not
# catch a root alias ("//", "/tmp/..", a symlink to "/") or a destination
# that resolves inside SKILLS_SRC, so canonicalize both sides first: mkdir
# it (a no-op if it already exists, and --dry-run never reaches here since
# it writes nothing) and resolve the physical path with cd + pwd -P.
if [[ -z "$SKILLS_DST" ]]; then
  echo "ERROR: refusing to install to '$SKILLS_DST'" >&2
  exit 1
fi
if ! $DRY_RUN; then
  mkdir -p "$SKILLS_DST"
  SKILLS_DST_REAL="$(cd "$SKILLS_DST" && pwd -P)"
  SKILLS_SRC_REAL="$(cd "$SKILLS_SRC" && pwd -P)"
  # bash's `cd`/`pwd -P` leave exactly two leading slashes ("//") verbatim
  # instead of collapsing them to "/" (a POSIX-sanctioned quirk), so match
  # any all-slash path rather than comparing against the literal string "/".
  if [[ "$SKILLS_DST_REAL" =~ ^/+$ ]]; then
    echo "ERROR: refusing to install to '$SKILLS_DST' (resolves to /)" >&2
    exit 1
  fi
  case "$SKILLS_DST_REAL" in
    "$SKILLS_SRC_REAL" | "$SKILLS_SRC_REAL"/*)
      echo "ERROR: refusing to install into the source tree: $SKILLS_DST_REAL" >&2
      exit 1
      ;;
  esac
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
