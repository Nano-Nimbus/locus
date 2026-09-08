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
# `${VAR-default}` and not `${VAR:-default}` on purpose: an explicitly empty
# CLAUDE_SKILLS_DIR is a caller mistake worth failing on, not a request for
# the default. With `:-` an empty value resolved silently to the real
# ~/.claude/skills, which is how a test run once overwrote live user config.
SKILLS_DST="${CLAUDE_SKILLS_DIR-$HOME/.claude/skills}"

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

# The per-skill `rm -rf` below makes a wrong destination destructive, so the
# destination is validated in two stages.
#
# First, cheaply, on the string: a blank, whitespace-only, or relative path is
# never what's intended (this syncs a global skills directory, not something
# scoped to wherever the script was invoked from) and a relative one would
# otherwise create stray content under the current working directory instead
# of failing.
#
# Then, on the resolved path, because a string comparison catches none of the
# aliases: "//", "/tmp/..", or a symlink can all name "/" or the source tree.
# Canonicalize both sides with cd + pwd -P and compare those. --dry-run skips
# this stage: it writes nothing, so it needs no destination to exist.
if [[ -z "$SKILLS_DST" || "$SKILLS_DST" != /* ]]; then
  echo "ERROR: refusing to install to '$SKILLS_DST' (must be a non-empty absolute path)" >&2
  exit 1
fi
if ! $DRY_RUN; then
  # Canonicalizing needs the directory to exist, so note whether we are the
  # ones creating it: a refusal below should leave the filesystem as it
  # found it rather than leaving a stray empty directory behind. The cleanup
  # uses `rmdir -p` because the `mkdir -p` here may have created several
  # levels; `rmdir` refuses a non-empty directory, so it walks up only
  # through the ones this run created and stops at the first real one.
  dst_preexisted=yes
  [[ -d "$SKILLS_DST" ]] || dst_preexisted=no
  mkdir -p "$SKILLS_DST"
  SKILLS_DST_REAL="$(cd "$SKILLS_DST" && pwd -P)"
  SKILLS_SRC_REAL="$(cd "$SKILLS_SRC" && pwd -P)"

  refuse_destination() {
    echo "ERROR: refusing to install to '$SKILLS_DST' ($1)" >&2
    [[ "$dst_preexisted" == yes ]] || rmdir -p "$SKILLS_DST" 2>/dev/null || true
    exit 1
  }

  # bash's `cd`/`pwd -P` leave exactly two leading slashes ("//") verbatim
  # instead of collapsing them to "/" (a POSIX-sanctioned quirk), so match
  # any all-slash path rather than comparing against the literal string "/".
  if [[ "$SKILLS_DST_REAL" =~ ^/+$ ]]; then
    refuse_destination "resolves to /"
  fi
  # Containment is checked in BOTH directions, because the per-skill `rm -rf`
  # below is destructive either way. A destination inside the source tree
  # deletes the skills being installed. A destination that *contains* the
  # source tree deletes whatever sibling shares a skill's name: pointing this
  # at the repository root removes the `locus/` Python package, because
  # `skills/claude/locus/` makes `locus` a skill name too.
  case "$SKILLS_DST_REAL" in
    "$SKILLS_SRC_REAL" | "$SKILLS_SRC_REAL"/*)
      refuse_destination "inside the source tree: $SKILLS_DST_REAL"
      ;;
  esac
  case "$SKILLS_SRC_REAL" in
    "$SKILLS_DST_REAL"/*)
      refuse_destination "contains the source tree: $SKILLS_DST_REAL"
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
