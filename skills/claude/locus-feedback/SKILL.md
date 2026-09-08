---
name: locus-feedback
description: >
  Record quality feedback on the most recent Locus run by annotating that run's
  _metrics/ JSON with a pass, partial, or fail verdict. Use when the user rates a
  palace answer, or when the locus skill infers a disagreement signal from the
  immediate follow-up message.
---

# Locus Feedback

Record quality feedback on the most recent Locus run. Annotates the run's
metrics file with a user verdict so retrieval quality can be tracked over time.

**Usage:** `/locus-feedback <quality> [note]`

**Arguments:**
- `quality` (required) — `pass`, `partial`, or `fail` (aliases: `good`/`ok`/`bad`)
- `note` (optional) — free-text explanation (quote if it contains spaces)

**Examples:**
```
/locus-feedback pass
/locus-feedback partial "Got the right room but missed the port number"
/locus-feedback fail "Answered about the wrong service entirely"
```

---

## Steps

### 1. Resolve quality value

Map the first argument to a canonical value:
- `pass` or `good` → `"pass"`
- `partial` or `ok` → `"partial"`
- `fail` or `bad` → `"fail"`

If the argument is missing or unrecognized, stop and output:
```
Usage: /locus-feedback <pass|partial|fail> [note]
```

### 2. Find the palace root

Walk up from the current working directory looking for a directory that contains
both `INDEX.md` and `_metrics/`. Stop at the filesystem root if not found.

If no palace root is found, output:
```
Error: no palace found (no _metrics/ directory above current path).
Run from inside a palace directory.
```

### 3. Find the most recent metrics file

List all `.json` files in `<palace_root>/_metrics/` sorted by modification time
(most recent first). Use the first result.

If `_metrics/` is empty or missing:
```
Error: no metrics files found in <palace_root>/_metrics/.
Run a locus query first to generate a metrics file.
```

### 4. Read and validate the metrics file

Read the file. Parse as JSON. Check:
- `schema_version` is `"1"` — if not, warn but continue
- `feedback` field is `null` — if already set, output a warning:
  ```
  Warning: this run already has feedback (<existing quality>).
  Overwrite? If yes, re-run with the same command. (Proceeding to overwrite.)
  ```
  Then overwrite.

### 5. Write feedback

Set the `feedback` field to:
```json
{
  "quality": "<resolved quality>",
  "note": "<note or null>",
  "recorded_at": "<current UTC timestamp in ISO 8601>"
}
```

Write the updated JSON back to the same file (preserve all other fields,
maintain 2-space indentation).

### 6. Report

Output a single confirmation line:
```
Locus feedback recorded: <quality> → <relative path to metrics file>
```

If a note was provided:
```
Locus feedback recorded: <quality> — "<note>" → <relative path to metrics file>
```

---

## Output format

```
Locus feedback recorded: pass — "Got the exact IP and port" → _metrics/2026-03-02T143012Z.json
```

Errors use the prefix `Error:`, warnings use `Warning:`. No other output.
