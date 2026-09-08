---
name: locus-audit
description: >
  Audit the health of a Locus memory palace. Scores room sizes, session log
  accumulation, retrieval depth, and feedback rates, then writes a prioritised
  report to _metrics/. Use when a room has accumulated session logs, when a run
  had unexpectedly high retrieval depth, after repeated fail feedback, or when
  no audit has been run in more than 30 days.
---

# Locus Audit (Experimental)

> **Experimental.** Results are advisory only.

Run a health audit on a Locus memory palace. Evaluates room sizes, session
log accumulation, retrieval depth, and feedback rates. Produces a prioritised
list of recommended actions and writes reports to `_metrics/`.

**Usage:** `/locus-audit [room-path]`

**Arguments:**
- `room-path` (optional) — scope to a single room (e.g. `projects/api-platform`).
  Omit to audit the entire palace.

---

## When to run

Run `/locus-audit` when:
- Any room has accumulated 5+ session logs
- A recent run had unexpectedly high retrieval depth
- `/locus-feedback fail` has been used more than once recently
- You haven't run an audit in more than 30 days

The locus skill will suggest running an audit when trigger conditions are met
(see the audit algorithm at
https://github.com/Nano-Nimbus/locus/blob/main/spec/audit-algorithm.md).

---

## Steps

### 1. Identify palace root

Walk up from cwd looking for `INDEX.md`. If not found, report an error.

### 2. Run the audit

The size-limit half of the audit is also available without the CLI:
`locus lint --root <palace_root>` reports every `locus.size-limit` violation in
the palace. It does not score retrieval depth or feedback, so it complements the
audit rather than replacing it.

Use the `locus-audit` CLI if available:

```bash
locus-audit --palace <palace_root> [--room <room-path>]
```

If the CLI is not installed, perform the audit manually:
- Discover rooms (dirs with a matching `<dir-name>.md` main file)
- Collect signals per room (line counts, session log count, mtime of oldest log)
- Load `_metrics/*.json` for retrieval and feedback signals
- Score each room per the audit algorithm linked above
- Build the action items list

### 3. Report

Print the full markdown health report. Highlight:
- Any `critical` rooms at the top
- The prioritised action items list
- The summary table

### 4. Write reports

Write `_metrics/audit-<timestamp>.md` and `_metrics/audit-<timestamp>.json`.
Update `_metrics/_last-audit.txt` with the current timestamp.

### 5. Propose next steps

After the report, list the top 3 action items explicitly and ask:
> "Shall I run `/locus-consolidate` on the rooms that need it?"

---

## Output format

```
# Palace Health Report
...
Reports written:
  _metrics/audit-2026-03-02T143012Z.md
  _metrics/audit-2026-03-02T143012Z.json
```
