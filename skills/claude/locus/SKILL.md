---
name: locus
description: >
  Locus memory palace navigation and management. Use when you need to read from
  or write to the agent's memory palace: querying what is known about a topic,
  recording findings from the current session, updating canonical facts,
  navigating to a specific room, regenerating an index, or checking conformance.
  Also use at the end of any session that produced new knowledge worth retaining.
---

# Locus, Memory Palace Navigation

Locus is a hierarchical markdown memory system. Directories are rooms, files are
knowledge. The goal is always: minimal context loaded, maximum precision retrieved.

Three commands from the `locus-mcp` package do the work here:

| Command | What it does |
|---|---|
| `locus recall QUERY` | Ranked lookup over one or more markdown roots. The fast read path. |
| `locus lint` | Checks roots for OKF v0.2 conformance and the Locus size conventions. |
| `locus index` | Regenerates `INDEX.md`, `index.md`, or `MEMORY.md` from frontmatter. |

`locus --palace DIR --task ...` runs the Agent SDK entrypoint and is not part of
this skill. Run `locus recall --help`, `locus lint --help`, or `locus index --help`
when you need a flag this file does not name.

**Compatibility note:** do not rely on `allowed-tools` frontmatter. Use the tools
available in the current environment.

---

## Step 1, recall before you read

Start every task here. `locus recall` is cheap enough to run before you have
decided whether the palace is relevant.

```sh
locus recall -k 3 --budget 4096 "why does the flux kustomization stall"
```

It prints `Recalled memory:` followed by up to `-k` hits (title, trust tier,
modified date, absolute path, one-line summary) and nothing at all when there is
no hit. Exit status is 0 either way, so an empty result is an answer, not a
failure. Exit status 1 means a configuration problem, reported on stderr.

Useful flags:

| Flag | Default | Meaning |
|---|---|---|
| `--root DIR` | `.locus.toml`, then `LOCUS_PALACE` | Root to search; repeatable |
| `-k N` | 3 | Number of hits |
| `--budget BYTES` | 4096 | Hard cap on the text output |
| `--type TYPE` | all | Only this frontmatter `type`; repeatable |
| `--include journal` | off | Include `type: Journal` files, excluded by default |
| `--json` | off | A JSON list instead of the text block |
| `--refresh` | off | Rebuild the index from scratch |

Ranking is bm25 over a SQLite FTS5 index, with `title` and `description` weighted
above the body. Exact ties go to human-reviewed files first, then to the newest
`modified`. A hit prefixed `[STALE]` has passed its `stale_after` or carries
`status: deprecated`: read it, but verify before repeating it.

Read the files recall names. Do not re-read the whole palace to confirm them.

If recall reports that no roots are configured, either pass `--root` explicitly or
set the roots once in a `.locus.toml` at the project root or any parent:

```toml
[recall]
roots = ["docs", "~/memory/shared"]
```

The index is derived and disposable. It lives under
`${XDG_CACHE_HOME:-~/.cache}/locus/`, never inside a root, and refreshes
incrementally on every call. Never commit it, and never hand-search around a stale
index: `--refresh` rebuilds it.

---

## Step 2, locate the palace

Needed when recall found nothing, when you are about to write, or when the task
names a room directly.

The MCP server and the Agent SDK resolve the palace root in this order:

1. an explicit `--palace DIR` argument
2. the `LOCUS_PALACE` environment variable
3. `.locus/` in the current working directory
4. `~/.claude/projects/<project-slug>/memory/`, the Claude Code auto-memory bridge
5. `~/.locus/`, the global palace

Cases 1, 2, 3, and 5 each name a directory that is meant to be a palace, so each is
bootstrapped when it has no `INDEX.md`: a 50-line `INDEX.md` skeleton is written, and
`global/` and `projects/` are created when the directory is empty. Case 4 is left
alone. A Claude Code memory directory already has `MEMORY.md` as its entry point and
belongs to Claude Code, not to Locus.

Three things worth knowing:

- Bootstrap follows the root you name. It used to run only for the `~/.locus`
  fallback, so a container that always passed `--palace` started with no index at
  all and `memory_list` answered "No INDEX.md found" until somebody wrote one.
- Bootstrap never modifies an existing file, and a read-only root is logged and
  skipped rather than failing the run.
- A directory whose entry point is `MEMORY.md` rather than `INDEX.md` is a memory
  directory, not a palace. `locus index` classifies it correctly on its own.

Once you have the root, read `INDEX.md` (or `MEMORY.md`) and nothing else yet.
Identify the relevant rooms from its tables. If no room matches, go to Step 6.

---

## Step 3, navigate to a room

For each relevant room:

1. Read the room's main file, `<room-name>/<room-name>.md` or `README.md`.
2. Check its References section. Read a specialty file only if it is directly
   relevant to the task.
3. Stop when you have enough. Do not speculatively load files.

Track internally, and report only if asked: which files were read, and how many
lines that came to.

---

## Step 4, write a canonical fact

Use this for a durable, verified fact that should persist across sessions.

1. Identify the file that owns the fact: the room main file, or a specialty file.
2. Read it if it is not already loaded.
3. Edit it explicitly. Update the section in place. Remove or supersede outdated
   entries rather than appending alongside them.
4. Keep the file inside its limit: 200 lines for a room main file, 300 for a
   specialty file. If the edit would break the limit, extract the growing section
   into a specialty file and leave a one-line reference behind.
5. Confirm `INDEX.md` still describes the room accurately.

Frontmatter is what recall ranks on, so a new file earns its retrieval by carrying
at least `title` (or `name`), `description`, and `type`. Add `stale_after` when the
fact has a known shelf life, and `status: deprecated` when you supersede a file
instead of deleting it.

---

## Step 5, write a session log

Use this for ephemeral findings, unverified information, or anything that may not
be durable. Write one at the end of any session that touched a room.

1. Determine the target room.
2. Open or create `<room-name>/sessions/YYYY-MM-DD.md` for today. Append if it
   exists. For a second session on the same day use `YYYY-MM-DD-2.md`.
3. Append this structure:

```markdown
## <HH:MM> <brief task description>

### Findings
<Specific facts learned. "Checked networking" has no consolidation value.>

### Actions Taken
<What was written or changed, with file references.>

### Consolidation Notes
<What should be promoted to a canonical file. Be explicit: "promote X to the
Overview section of <room>.md". Leave blank if nothing warrants promotion.>
```

4. Never edit a session log entry after writing it.

`sessions/` is write-blocked through the MCP server, deliberately: consolidation is
an explicit act, not a side effect of a write.

Recall excludes files whose frontmatter `type` is `Journal`, and `--include journal`
brings them back. The filter is on that type, not on the `sessions/` path, so give a
session log `type: Journal` if you want it out of the default results.

---

## Step 6, create a new room

Only when no existing room fits.

1. Confirm with the user, or proceed if operating autonomously with clear scope.
2. Create `<palace-root>/<layer>/<room-name>/`, where `<layer>` is `global/` for
   cross-project knowledge or `projects/<project-name>/` for project knowledge.
3. Write `<room-name>/<room-name>.md` from the skeleton below. It is reproduced
   here on purpose: an installed `locus-mcp` ships no `templates/` directory, so
   never copy from a repo-relative path. A git clone of the source repository
   carries the same starting point at `templates/room/room-name.md`.

```markdown
---
title: <Room Name>
description: <One line. Recall weights this above the body, so make it specific.>
type: Reference
---

# <Room Name>

<What this room covers and which queries it answers.>

## Overview

- <Key fact>
- <Key fact>

## <Topic Section>

<Domain-specific content. Rename the heading. Add or remove sections as needed.>

## Key Files

| Description | Path |
|---|---|
| <What it is> | `<absolute or repo-relative path>` |

## References

- [`<specialty-file>.md`](./<specialty-file>.md), <one-line description>

<!--
SIZE LIMIT: keep this file under 200 lines. Extract a growing section into a
specialty file and reference it above.
WRITE MODE: canonical. Edit explicitly. Supersede rather than append.
-->
```

4. Add the room to `INDEX.md`, or run `locus index` (Step 7) and let it do that.
5. Verify `INDEX.md` is still under 50 lines.

---

## Step 7, regenerate indexes and check conformance

Run these after any write. They are the cheapest way to catch a palace that has
drifted from its own index.

```sh
locus index --root <palace-root>            # rewrite the index files that drifted
locus index --root <palace-root> --check    # write nothing, exit 1 on drift
locus lint  --root <palace-root>            # report conformance and size violations
locus lint  --root <palace-root> --fix      # add inferable frontmatter only
```

`locus index` picks the output shape from the root itself: a palace (an `INDEX.md`
at the root, or `global/` plus `projects/`) gets the 50-line routing table, an OKF
bundle gets an `index.md` per directory, and a Claude Code memory directory gets a
`MEMORY.md` of one `- [Title](file.md) - description` line per topic file. Pass
`--kind palace|okf|memory` to override the detection. Output is deterministic and
only index files are ever written, so `--check` is a byte comparison and is safe in
CI.

`locus lint` reports and exits 0 by default. `--check` exits 1 on any error, and
`--strict` promotes warnings to errors as well. `okf.*` rules are what the
specification requires; `locus.*` rules are the palace size conventions. On a
palace root the missing-frontmatter rules are warnings rather than errors, because
a palace legitimately carries no frontmatter.

`--fix` adds exactly three things: `type` (from `--type-map DIR=TYPE` or
`[lint.types]`), `generated.at` (from the file's first git commit), and
`status: deprecated` for `--archive-glob` paths. It never rewrites or deletes an
existing key. Everything else a lint run reports is yours to fix by hand.

Before reporting a clean palace, `locus index --check` and `locus lint --check`
should both exit 0.

---

## Step 8, check consolidation triggers

After any write:

| Condition | Action |
|---|---|
| Room main file over 150 lines (soft limit) | Extract a section into a specialty file |
| Room main file over 200 lines (hard limit) | Must extract before any further writes |
| `sessions/` has more than 5 unprocessed logs | Invoke the `locus-consolidate` skill |
| Same room accessed 3 or more consecutive sessions | Invoke the `locus-consolidate` skill |
| `INDEX.md` over 40 lines | Consider splitting into sub-indices |
| `locus lint` reports a `locus.size-limit` error | Extract now, the hard limit is already breached |

---

## Step 9, inferred feedback (experimental)

After giving a Locus-sourced answer, check whether the user's **immediate
follow-up message** carries a disagreement signal. Check only the next message.
Do not scan earlier context.

Skip inference when:

- the follow-up is longer than 300 characters (likely a new task)
- it contains a URL, a code block, or a file path
- the most recent `_metrics/*.json` already has a non-null `feedback` field

| User says | Infer | Examples |
|---|---|---|
| Strong disagreement | `fail` | "that's wrong", "incorrect", "no, that's not right" |
| Mild correction | `partial` | "actually...", "not quite", "you missed...", "try again" |
| Anything else | no action | |

On a signal, record feedback with the same logic as `/locus-feedback`, with the
note `inferred (confidence: <N>): "<message>"`. Do not announce it unless asked.
An explicit `/locus-feedback` always overwrites an inferred entry.

---

## Output

End every Locus operation with a one-line summary:

```
Locus: recalled <N> hits, read <N> files (<M> lines). Wrote to <file(s)>. [Consolidation needed: yes/no]
```

The Agent SDK metrics collector and the benchmark runner both read this line.
