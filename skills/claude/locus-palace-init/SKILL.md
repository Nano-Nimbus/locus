---
name: locus-palace-init
description: >
  Bootstrap a new Locus memory palace from existing session memory files.
  Discovers Claude project auto-memory directories, aggregates and sorts
  their contents, and creates a structured palace with an INDEX.md and one
  room per project. Run once when setting up a palace for the first time, or
  to import a new batch of projects into an existing palace.
---

# Locus Palace Init

Bootstrap a structured memory palace from existing flat session memory files
(e.g. `~/.claude/projects/*/memory/MEMORY.md`).

---

## Step 1 — Resolve arguments

Parse `$ARGUMENTS`:

- `--source <dir>` — root to scan for memory files (default: `~/.claude/projects/`)
- `--palace <dir>` — palace root to write into (default: `~/.locus/`)
- `--dry-run` — print the plan, write nothing
- `--overwrite` — replace existing room files (default: skip rooms that already exist)

Set defaults for any omitted flags.

---

## Step 2 — Discover source memory files

1. Glob `<source>/*/memory/MEMORY.md` to find all candidate files.
2. For each match, record:
   - `slug` — the project directory name (the `*` part of the glob path)
   - `memory_path` — full path to `MEMORY.md`
   - Any additional `.md` files in the same `memory/` directory (specialty files)
3. Report: "Found N source memory files."
4. If none found, stop and tell the user to check the `--source` path.

---

## Step 3 — Parse each memory file

For each source memory file, extract:

| Field | How to find it |
|---|---|
| **title** | First `# Heading` in the file |
| **summary** | First non-blank paragraph after the heading (≤3 sentences) |
| **tech_tags** | Comma-separated keywords inferred from headings and bullet lists (e.g., "kubernetes", "python", "terraform") |
| **status** | Look for "Current phase", "Status", "Milestone", or "v0.x" markers |
| **key_files** | Lines matching a path pattern (`/`, `~/`, `./`) in file-reference context |

Keep each extraction to the first 150 lines of the file — do not load the full file if avoidable.

---

## Step 4 — Sort and group projects

Assign each project to a **group** using these heuristics (first match wins):

| Group | Heuristic |
|---|---|
| `infra` | tech_tags contain: kubernetes, terraform, ansible, k3s, proxmox, flux, docker |
| `tools` | tech_tags contain: cli, mcp, sdk, plugin, extension |
| `data` | tech_tags contain: python, jupyter, pandas, ml, model, dataset |
| `docs` | tech_tags contain: wiki, obsidian, markdown, docs, blog |
| `projects` | everything else |

Sort within each group alphabetically by slug.

Report the grouping plan (slug → group) before writing anything.

---

## Step 5 — Create the palace structure

### 5a — Check for existing palace

1. Check if `<palace>/INDEX.md` already exists.
2. If it does and `--overwrite` was NOT passed:
   - Read the existing `INDEX.md`.
   - Only add rooms for slugs not already listed.
   - Preserve existing rows in the index tables.
3. If it does not exist (or `--overwrite`): start fresh from the template below.

### 5b — Write room files

For each project (in group order, then alphabetical):

1. Determine the room path:
   - `infra`, `tools`, `data`, `docs` groups → `<palace>/global/<slug>/`
   - `projects` group → `<palace>/projects/<slug>/`

2. Check if the room already exists (`<room-path>/<slug>.md`). If it does and
   `--overwrite` is not set, skip it with a note.

3. Create the room directory and write `<slug>.md`:

```markdown
# <title>

<summary>

## Overview

<!-- Imported from auto-memory on YYYY-MM-DD. Review and prune. -->

<paste the first 60 lines of the source MEMORY.md, indented as a blockquote or verbatim section>

## Key Files

<!-- Populated from auto-memory import. Verify paths are still current. -->

<key_files extracted in step 3, one per table row>

## References

<!-- Specialty files imported alongside MEMORY.md, if any. -->

<one line per specialty file, or "(none)" if none>

<!--
SOURCE: <memory_path>
IMPORTED: YYYY-MM-DD
STATUS: imported — review and reformat before treating as canonical
-->
```

4. Copy any specialty `.md` files from `memory/` into the room directory as-is.

### 5c — Write INDEX.md

Once every room file is on disk, generate the index rather than hand-writing it:

```sh
locus index --root <palace> --kind palace
```

`locus index` builds the routing table from each room's frontmatter and enforces
the 50-line budget. `--kind palace` is only needed while the palace is still
half-built and auto-detection could read it as something else; once `INDEX.md`
exists, plain `locus index --root <palace>` is enough. Re-running it is safe:
output is deterministic, existing consolidation prose is preserved, and only
index files are ever written.

Follow it with a conformance pass over the imported rooms:

```sh
locus lint --root <palace> --fix    # add inferable type/generated.at, rewrite nothing
locus lint --root <palace>          # report what --fix could not infer
```

Imported rooms carry no frontmatter, and frontmatter is what `locus recall` ranks
on, so a room with no `title`/`description`/`type` is close to unretrievable.
`--fix` supplies `type` where `--type-map DIR=TYPE` or `[lint.types]` says what it
should be; the descriptions are yours to write in the review pass.

If `locus index` is unavailable, build the index by hand, iterating groups in this
order: `infra`, `tools`, `data`, `docs`, `projects`.

Use the template below, omitting any section whose group has no rooms:

```markdown
# <Palace Name>

Memory palace auto-initialized from <N> project memory files on YYYY-MM-DD.
Review imported rooms and reformat to canonical style before treating as final.

## Global Rooms

| Room | Description | Path |
|---|---|---|
| `<slug>` | <summary, truncated to 80 chars> | `global/<slug>/` |

## Project Rooms

| Room | Description | Path |
|---|---|---|
| `<slug>` | <summary, truncated to 80 chars> | `projects/<slug>/` |

---
_Initialized: YYYY-MM-DD — review imported rooms before first use_

<!--
NAVIGATION: Read this file first. Identify the relevant room(s), then read
only those. Do not load the full palace.
SIZE LIMIT: Keep this file under 50 lines.
-->
```

---

## Step 6 — Report

Emit a summary:

```
locus-palace-init complete:
  Source: <source dir>  (<N> memory files found)
  Palace: <palace dir>
  Rooms created: <N>
  Rooms skipped (already exist): <N>
  Groups: infra=N  tools=N  data=N  docs=N  projects=N

Next steps:
  1. Review each room file. Imported content is verbatim, not canonical.
  2. Reformat to palace conventions (remove the SOURCE/IMPORTED comment block).
  3. Give each room frontmatter: title, description, type. Recall ranks on these.
  4. Record the palace roots once so recall and lint need no flags:
       [recall]
       roots = ["<palace>"]
     in a .locus.toml at the project root or any parent, or export LOCUS_PALACE.
  5. Run /locus-consolidate on busy rooms once you start using the palace.
```

If `--dry-run` was passed, prefix every write action with `[dry-run]` and skip
all file I/O.

---

## Notes

- This skill creates a **first-pass** palace. Imported content is verbatim
  from auto-memory files, which are themselves auto-generated and may contain
  stale facts. Always review imported rooms before relying on them.
- Run `locus-consolidate` on any room immediately if it has `sessions/` logs
  that were also imported.
- `locus recall --root <palace> "<a fact you know is in there>"` is the fastest
  smoke test that the import produced something retrievable. No hits usually
  means missing frontmatter, not a broken index.
- The `--source` and `--palace` defaults assume the standard Claude Code
  auto-memory layout. Adjust if your setup differs.
- This skill is intentionally write-safe by default: it skips existing rooms
  rather than overwriting them. Use `--overwrite` explicitly to re-import.
