---
name: locus-consolidate
description: >
  Consolidate a Locus memory palace room. Merges session logs into canonical
  files, supersedes stale entries, archives processed logs, and enforces size
  limits. Use when a room's session directory has more than 5 unprocessed logs,
  the same room has been accessed in 3 or more consecutive sessions, a room
  main file exceeds 150 lines, or the user asks to consolidate or clean up memory.
---

# Locus Consolidate

Merges accumulated session logs into canonical room files, enforces size limits,
and archives processed logs. Run per room — if multiple rooms need consolidation,
run this skill once per room.

**Arguments:** `$ARGUMENTS` — room path relative to palace root (e.g. `projects/my-project`).
If omitted, detect rooms needing consolidation automatically (Step 1b).

---

## Step 1 — Identify the target room

**1a — Argument provided:**
Navigate to `<palace-root>/$ARGUMENTS`. Verify the directory exists and contains
a room main file and a `sessions/` subdirectory.

**1b — No argument (auto-detect):**
Read `INDEX.md` to get all room paths. For each room, check:
- Does `sessions/` contain more than 5 files not in `sessions/archived/`?
- Does the room main file exceed 150 lines?

Process rooms that meet either condition, one at a time. Report which rooms were
found and ask for confirmation before proceeding if running interactively.

---

## Step 2 — Read the room state

Read in this order:
1. Room main file (`<room>/<room-name>.md` or `README.md`)
2. All unprocessed session logs: files in `<room>/sessions/` that are **not**
   in `<room>/sessions/archived/`

Read session logs oldest-first (alphabetical by filename = chronological).

Do not read specialty files unless a Consolidation Note explicitly references them.

---

## Step 3 — Extract consolidation candidates

For each session log, collect entries from the `### Consolidation Notes` section.
Discard sessions with empty or blank Consolidation Notes.

Build a working list:
```
[ { source: "sessions/2026-02-01.md", note: "promote X to Overview section" },
  { source: "sessions/2026-02-15.md", note: "update Y in technical-gotchas.md" },
  ... ]
```

Resolve conflicts: if two notes contradict each other, the newer one wins.
Flag irreconcilable conflicts for user review rather than silently choosing.

---

## Step 4 — Merge into canonical files

For each consolidation candidate:

1. Identify the target canonical file (room main file or a specialty file).
2. Read the target file if not already loaded.
3. Apply the change:
   - **New fact** → add to the appropriate section
   - **Updated fact** → edit the existing entry in place; remove the old version
   - **Superseded entry** → delete the outdated content entirely
4. Do not append alongside old content — canonical files must stay authoritative.

After all changes, verify each edited canonical file:
- Room main file: must be ≤ 200 lines (hard limit)
- Specialty file: must be ≤ 300 lines

If a file exceeds its limit after merging:

→ **Extract:** identify the largest or most self-contained section, move it to
a new specialty file (`<room>/<descriptive-name>.md`), and replace the section
in the main file with a one-line reference:
```markdown
- [`<filename>.md`](./<filename>.md) — <one-line description>
```

---

## Step 5 — Archive processed session logs

Move processed session log files to `<room>/sessions/archived/`:

1. Create `sessions/archived/` if it does not exist.
2. Move each processed log file into it.
3. Do not delete session logs — archival preserves the audit trail.

Only move logs whose Consolidation Notes were fully processed. If a log had
irreconcilable conflicts flagged in Step 3, leave it in place with a comment
added at the top:
```markdown
<!-- CONSOLIDATION PENDING: conflicts flagged, requires review -->
```

---

## Step 6 — Update INDEX.md

Re-read the room main file. Check whether its description in `INDEX.md` still
accurately reflects the room's current contents.

If the description has drifted:
1. Read `INDEX.md`.
2. Update the room's description column: one line, present tense, specific.
3. Verify `INDEX.md` remains under 50 lines.

---

## Step 6b: Verify with the CLI

Consolidation moves content between files, which is exactly what drifts an index
and breaks a size limit. Confirm both before reporting:

```sh
locus index --root <palace-root> --check   # exit 1 if any index drifted
locus lint  --root <palace-root>           # size limits and OKF conformance
```

`locus index --root <palace-root>` (without `--check`) regenerates the index
files that drifted, so Step 6 can be delegated to it rather than done by hand.
It writes index files only, never room content. A `locus.size-limit` error after
consolidation means the merge pushed a canonical file past its hard limit: go
back to Step 4 and extract.

---

## Step 7 — Report

Emit a consolidation summary:

```
Locus Consolidate: <room-path>
  Session logs processed:  <N>
  Canonical files updated: <list>
  Specialty files created: <list or none>
  Logs archived:           <N>
  Conflicts flagged:       <N or none>
  Room main file:          <final line count> lines
  INDEX.md updated:        yes/no
  locus index --check:     clean/drifted
  locus lint:              <N> error(s), <N> warning(s)
```

If multiple rooms were processed (auto-detect mode), emit one summary block per room.

---

## Installation

From a clone of the locus source repository:

```sh
make install-skills          # installs every skill in skills/claude/
```
