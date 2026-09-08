# Recall

Defines `locus recall`: a ranked, stale-aware lookup over one or more markdown
roots, and the index it shares with the MCP server's `memory_search`. Recall
is the first Locus component that reads frontmatter; before it, frontmatter was
inert to every tool.

---

## Purpose

An agent that has to *decide* to look something up mostly does not. Recall is
built to be called on every prompt by a hook, so it has three hard constraints:

1. **Fast.** Sub-second on a memory directory plus a few hundred docs. The
   index is SQLite FTS5 from the standard library; the `locus` console script
   dispatches `recall` before importing anything heavy.
2. **One injection.** Text output is a short ranked list with a summary and a
   path per hit, capped by a byte budget. Never a menu to navigate.
3. **Honest.** Every hit says how much to trust it (trust tier) and whether it
   has expired (`STALE`).

---

## Roots

A root is any directory tree of `*.md` files. Three shapes are expected:

| Root | Frontmatter it carries |
|---|---|
| Locus palace | usually none; title from the first `#` heading |
| OKF bundle | `type`, `title`, `description`, `tags`, `generated`, `verified`, `status`, `stale_after` |
| Claude Code memory directory | `name`, `description`, `metadata.type`, `metadata.tags` |

Roots are resolved in this order; the first source that yields anything wins:

1. repeated `--root DIR` flags
2. `[recall] roots = [...]` in a `.locus.toml` found in the working directory
   or any parent (entries are relative to that file; `~` is expanded)
3. the `LOCUS_PALACE` environment variable

```toml
# .locus.toml
[recall]
roots = ["docs", "~/memory/shared"]
```

Dot-directories, `node_modules`, `__pycache__`, `.venv`, and `_metrics` are
skipped inside a root. Files over 1 MB are skipped. Symlinked sub-directories
are followed, with a cycle guard, so a directory reachable two ways is indexed
once. A root contained in another root is dropped with a warning, because a
file inside two roots would otherwise be indexed twice and returned twice.

---

## Index

**Location.** `${XDG_CACHE_HOME:-~/.cache}/locus/<hash-of-roots>.sqlite`, where
the hash is over the sorted, resolved root paths. The index is never written
inside a root: it is derived, disposable data. That is enforced, not assumed.
A relative `XDG_CACHE_HOME` is ignored per the XDG spec (honouring one made the
index path depend on the working directory), one pointing inside a root falls
back to `~/.cache`, and an `--index PATH` inside a root is an error. If the
cache location cannot be opened (read-only home, for example) the index is
built in memory for that call instead. An index file that is corrupt or not a
database is deleted and rebuilt once, rather than failing every run forever.
`--index PATH` overrides the location.

**Refresh.** Every call walks the roots. A file whose mtime and size match the
stored row is skipped. Otherwise it is read and hashed; if the content hash
matches, only mtime and size are updated. Only files whose content changed are
parsed again. Files that disappeared are removed. `--refresh` drops the index
and rebuilds it.

**Columns per document.**

| Column | Source, in order |
|---|---|
| `title` | `title`, `name`, first `# ` heading, file stem |
| `type` | `type`, `metadata.type` |
| `description` | `description` |
| `tags` | `tags`, `metadata.tags` (flow list, block list, or comma string) |
| `modified` | `modified`, `generated.at`, file mtime (UTC ISO) |
| `status` | `status`, lower-cased; absent means stable |
| `stale_after` | `stale_after` |
| `tier` | derived from `verified` (see below) |
| `excerpt` | first prose paragraph of the body, at most 280 characters |
| body (FTS only) | everything after the frontmatter |

**Frontmatter parser.** Dependency-free, covering `key: value` scalars,
`[a, b]` flow lists, block lists of scalars and of mappings, and nested
mappings. Unknown keys are preserved; unparseable values are kept as raw
strings. A file without a valid opening and closing `---` has no frontmatter.

---

## Ranking

FTS5 `bm25` with per-column weights: title 10, description 5, tags 3, type 2,
body 1. The query is a bag of words: each token is quoted (so FTS5 operators
in a prompt are inert), stopwords and one-character tokens are dropped, and
the tokens are joined with `OR`. When the query has two or more tokens the
whole sequence is added as one phrase, so a document that contains the literal
`10.0.0.201` or `pg_basebackup` outranks one that merely mentions `10` or
`pg`. Tokens are Porter-stemmed on both sides.

Exact score ties (to three decimals) are broken by trust tier, human-reviewed
first, then by `modified`, newest first.

`type: Journal` documents are excluded unless `--include journal` is given.
`--type TYPE` (repeatable) restricts results to those types.

---

## Trust tier and staleness

Trust tier follows OKF and is derived from the `verified` list:

| `verified` | Tier |
|---|---|
| absent or empty | `unverified` |
| only non-human actors (`process:`, `producer/version`) | `machine-confirmed` |
| any entry whose `by` starts with `human:` | `human-reviewed` |

A hit is `STALE` when `status` is `deprecated` or `stale_after` is an ISO
date or datetime that has passed (naive values are read as UTC). Staleness is
evaluated at query time, so the index does not need a rebuild when a date
passes.

---

## Output

**Text** (default) is for hooks. It begins `Recalled memory:` and stays within
`--budget` bytes (default 4096): hits that do not fit are dropped, and the
last hit that partially fits has its summary truncated. The summary is the
`description`, else the body line that matches the most query terms, else the
excerpt. With no hits the output is empty and the exit status is still 0.

```
Recalled memory:
1. Irrigation schedule (human-reviewed, 2026-05-01)
   /path/to/bundle/irrigation-schedule.md
   Zone timings for the drip lines and the fix for zone seven valve chatter.
2. [STALE] Frost watch (unverified, 2026-09-07)
   /path/to/bundle/frost-watch.md
   Cover the seedlings when the forecast dips below two degrees.
```

**JSON** (`--json`) is for tools: a list of objects with `title`, `root`,
`path`, `abs_path`, `type`, `description`, `snippet`, `excerpt`, `tags`,
`modified`, `status`, `stale_after`, `tier`, `stale`, `score`, and `summary`.
An empty result is `[]`.

Exit status 1 with a message on stderr means a configuration or environment
problem: no roots, a root that is not a directory, a bad `.locus.toml`, or a
Python whose `sqlite3` lacks FTS5.

---

## CLI

```
locus recall [--root DIR ...] [-k N] [--budget BYTES] [--include journal]
             [--type TYPE ...] [--json] [--refresh] [--index PATH] QUERY...
```

`python -m locus.recall` is equivalent. The importable API is
`locus.recall.recall(query, roots, k=3, include_journal=False, types=None,
refresh=False, index_path=None, scope=None)` returning a list of `Hit`, and
`locus.recall.RecallIndex` for callers that want to hold the index open.

---

## Relationship to `memory_search`

The MCP tool `memory_search` opens the same index over the palace root
(cache-keyed by that one root), refreshes it, and queries it with `scope` set
to the tool's `path` argument. Differences from the CLI: journals are
included, up to 20 hits are returned, and paths are palace-relative. See
`spec/mcp-server.md`.

---

## Implementation

- **Package**: `locus/recall/`
- `frontmatter.py`: parser
- `config.py`: root resolution, `.locus.toml`, cache path
- `index.py`: `RecallIndex`, extraction, ranking, staleness, trust tier
- `output.py`: text and JSON renderers, byte budget
- `main.py`: CLI; `locus/cli.py` dispatches `locus recall` to it
- **Tests**: `tests/unit/test_recall.py` over `tests/fixtures/okf-bundle/` and
  `tests/fixtures/palace/`
