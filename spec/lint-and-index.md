# Lint and Index

Defines `locus lint` and `locus index`: conformance checking against
[Open Knowledge Format](https://github.com/GoogleCloudPlatform/open-knowledge-format)
v0.2 and the Locus palace conventions, and generation of the three index
shapes those conventions define.

Both commands read the same frontmatter `locus recall` indexes, using the same
dependency-free parser, and both are dispatched from `locus.cli` before the
Agent SDK is imported. A CI job that only checks conformance never installs it.

---

## Roots

A root is any directory tree of `*.md` files, resolved exactly as
[`recall`](recall.md) resolves them, with one addition: the `[lint]` table is
consulted before `[recall]`, so a project already configured for recall needs
no second configuration.

1. repeated `--root DIR` flags
2. `[lint] roots = [...]`, then `[recall] roots = [...]`, in a `.locus.toml`
   found in the working directory or any parent
3. the `LOCUS_PALACE` environment variable

```toml
# .locus.toml
[lint]
roots = ["docs"]
archive_globs = ["archive/*", "*/superseded/*"]

[lint.types]
"." = "Reference"
runbooks = "Runbook"
analysis = "Analysis"
```

### Root kinds

Each root is classified once, and the kind decides which rules apply and which
index file is generated. `--kind` overrides the detection.

| Kind | Detected by | Generates |
|---|---|---|
| `palace` | `INDEX.md` at the root, or `global/` plus `projects/` | `INDEX.md` |
| `okf` | `index.md` at the root, or nothing else matching | `index.md` per directory |
| `memory` | half or more of the root's topic files carry both `name` and `description`, or a bare `MEMORY.md` | `MEMORY.md` |

Detection is case-sensitive. macOS and Windows filesystems are not, so an OKF
`index.md` answers `True` to a naive test for `INDEX.md`; every check that
separates the two lists the directory and compares names exactly.

A single Claude Code style file inside an OKF bundle does not make the bundle a
memory directory, and a bundle that happens to carry a `MEMORY.md` is still a
bundle: `index.md` at the root wins over the memory heuristic.

---

## `locus lint`

```sh
locus lint [--root DIR ...] [--fix] [--check] [--strict] [--json]
           [--type-map DIR=TYPE ...] [--archive-glob GLOB ...]
```

Reports and exits 0 by default. `--check` is the CI gate: it exits 1 when any
**error** was reported, and with `--strict` when any **warning** was too.

### Severity

An error is a violation of something OKF or a Locus spec requires. A warning is
advisory: a soft size limit, or a rule that a legitimate layout can break.

The one severity downgrade is deliberate. A palace stores prose without
frontmatter by design, which [`recall.md`](recall.md) states outright ("Locus
palace | usually none; title from the first `#` heading"). Reporting every
palace file as a CI failure for a layout the palace spec itself describes would
make `--check` useless on a palace, so on a `palace` root the missing
frontmatter and missing type rules are warnings. `--strict` holds a palace to
full OKF conformance.

### OKF rules

| Rule | Severity | What it checks |
|---|---|---|
| `okf.frontmatter-missing` | error (warning on a palace) | A non-reserved document has no frontmatter. OKF requires at least a `type`. |
| `okf.frontmatter-unparseable` | error | Frontmatter opens with `---` and is never closed. |
| `okf.type-missing` | error (warning on a palace) | Frontmatter carries no non-empty `type`. A Claude Code `metadata.type` is named in the message but does not satisfy the rule: it is a category, not an OKF type. |
| `okf.generated-by` | error | A `generated` mapping with no `by`. The spec requires the producing actor. |
| `okf.generated-at` | error | A `generated` mapping with no `at`. |
| `okf.timestamp` | error | `generated.at`, `verified[].at`, or `stale_after` is not ISO 8601. |
| `okf.verified-actor` | error | A `verified` entry with no `by`. |
| `okf.sources-resource` | error | A `sources` entry with no `resource`. |
| `okf.index-frontmatter` | error | `index.md` carries frontmatter; only `okf_version` is allowed, and only at a bundle root. |
| `okf.index-entry` | warning | A list item in `index.md` that is not `* [Title](path) - description`. |
| `okf.log-frontmatter` | error | `log.md` carries frontmatter. |
| `okf.log-heading` | error | A `log.md` heading that is not an ISO 8601 `YYYY-MM-DD` date. |
| `okf.log-order` | error | `log.md` entries run oldest first. Newest first is the spec's order. |
| `okf.unreadable` | error | Not readable as UTF-8. |

**What is never reported.** OKF section 4.1 says a consumer "MUST NOT reject
documents with unrecognized fields" and "MUST tolerate unknown types". Unknown
keys and unknown `type` values therefore pass, always. `verified` is accepted
both as a list of mappings and as one bare mapping, and `index.md` and `log.md`
are exempt from the type rule: they are directory apparatus, not concepts.

### Locus rules

| Rule | Severity | What it checks |
|---|---|---|
| `locus.size-limit` | warning over the soft limit, error over the hard one | Line counts from [`size-limits.md`](size-limits.md). |
| `locus.room-main-file` | error | A palace room directory with no `<room-name>.md` or `README.md`, per [`room-conventions.md`](room-conventions.md). |
| `locus.archive-status` | error (warning when `status` is already set) | A path matching an archive glob with no `status: deprecated`. |

Size limits by file class:

| Class | Soft | Hard | Applies to |
|---|---|---|---|
| `INDEX.md` | 40 | 50 | every root |
| `MEMORY.md` | 150 | 200 | every root |
| Room main file | 150 | 200 | palace roots |
| Specialty file | 200 | 300 | palace roots |
| Session log | none | none | never limited; append-only, consolidated later |

`sessions/` and `journal/` contents are episodic and exempt. Container
directories with no markdown of their own (`global/`, `projects/`) are not
rooms and are exempt from the main-file rule.

### `--fix`

`--fix` adds three fields and nothing else. It never rewrites a key that
already exists, and it never deletes one.

| Field | Inferred from |
|---|---|
| `type` | `--type-map DIR=TYPE` or `[lint.types]`. Directories are tried deepest first, each by its root-relative path and then by its bare name; `"."` is the whole-root default. |
| `generated.at` | The date of the file's first git commit, when the file is inside a git repository. Only filled into an existing `generated` block. |
| `status: deprecated` | A root-relative path matching an archive glob, and only when `status` is absent entirely. |

Three deliberate limits:

- **A `generated` block is never invented.** `generated.by` names who produced
  the text, and nothing in a file says that. Writing `at` alone would trade
  `okf.generated-at` for `okf.generated-by`.
- **Edits are textual, not a YAML round trip.** Parsing and re-dumping would
  reorder keys, restyle flow mappings, requote strings, and drop comments in
  every file it touched. Inserting a line leaves every byte the author wrote
  where they put it, and `{ by: x }` stays spaced while `{by: x}` stays tight.
- **Idempotent by construction.** A key is only ever inserted when it is
  absent, so a second `--fix` is a no-op that produces identical bytes.

---

## `locus index`

```sh
locus index [--root DIR ...] [--check] [--json] [--kind KIND]
```

Writes the index files that drifted and exits 0. `--check` writes nothing and
exits 1 if anything would change. Only index files are ever written: a
concept document is never touched, in either mode.

### `index.md` (OKF section 8)

One per directory that has something to list.

```markdown
---
okf_version: "0.2"
---

# Greenhouse

* [Irrigation schedule](irrigation-schedule.md) - Zone timings for the drip lines.
* [Runbooks](runbooks/) - Symptom to fix procedures.
```

Files come first, sorted by title, then sub-directories, sorted by name. A
sub-directory is listed when any non-reserved markdown lives anywhere beneath
it, and its description comes from its main file if it has one. Reserved names
(`index.md`, `log.md`, `INDEX.md`, `MEMORY.md`) are never listed as entries.
The frontmatter block appears only at the bundle root, and holds only
`okf_version`: that is the sole exception in OKF to index files carrying none.

### `INDEX.md` (palace)

The routing table from [`index-format.md`](index-format.md), one row per
level-one room under `global/` and `projects/`, inside the 50-line budget.

Room descriptions come from the room main file: its frontmatter `description`,
else the first sentence of its opening paragraph. A pipe in a description is
escaped so it cannot open a new table column.

Over budget is an error, not a truncation. The spec's answer to a palace that
outgrows 50 lines is sub-indices, and silently dropping rooms would make the
palace unnavigable in exactly the case where navigation matters. The file is
left as it was and the command exits 1.

A palace with no rooms generates nothing rather than replacing a hand-written
or bootstrapped `INDEX.md` with a placeholder.

### `MEMORY.md` (Claude Code auto-memory)

Claude Code requires that filename, so this is not an OKF `index.md`: it
carries no frontmatter and uses `-` entries.

```markdown
# Memory Index

## Gotcha

- [project_valve-chatter](project_valve-chatter.md) - Zone seven valve chatter is a tired solenoid spring.
```

One line per topic file, grouped under the file's category (`type`, else
`memory_type`, else `metadata.type`) when the files declare one and flat when
they do not. Groups are sorted, uncategorised files come last under `Other`,
and entries within a group sort by title.

Descriptions are collapsed to one line and truncated at 200 characters. One
entry per line is what makes the index safe to merge with git's union driver:
a multi-line entry can be interleaved by a merge, a single line cannot.

### Determinism

`--check` is a byte comparison, so every renderer is a pure function of the
files on disk. Three things would otherwise vary between runs on unchanged
input, and each is pinned:

- entries are sorted, never left in directory order
- the palace `_Last consolidated:` date is preserved from the existing
  `INDEX.md`, never recomputed
- prose an author wrote around the generated tables, the palace title, its
  scope sentence, and any trailing HTML comment, is carried across rather than
  regenerated

---

## CI

```yaml
- run: uv run locus lint --root docs --check
- run: uv run locus index --root docs --check
```

`lint --check` fails on errors; add `--strict` to fail on warnings too.
`index --check` fails when a generated index has drifted from the frontmatter
it is built from, which is the case a human hand-editing a generated file
produces. The fix is to correct the `description` in the concept document and
regenerate, not to edit the index.
