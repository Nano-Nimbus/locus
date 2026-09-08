# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

## What Locus is

Locus is a hierarchical markdown memory system for autonomous AI agents (a "palace":
directory = room, file = knowledge, navigated on demand instead of loaded all at once).
It ships as four things: SKILL.md files for Claude/Codex/Gemini, an MCP server
(`locus-mcp`), a Python Agent SDK entrypoint (`locus`), and standalone CLI tools for
retrieval, conformance, and security. Current package version: 0.10.0.

For the full pitch, structure diagram, and contributor workflow see
[`README.md`](README.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md). This file only
covers what a coding agent needs to orient fast.

## Module layout

Seven packages under `locus/`. Six of them have their own `main.py`;
`locus/feedback/` is the exception, it has no CLI and no `main.py`:

| Module | Purpose |
|---|---|
| `locus/agent/` | Agent SDK entrypoint (the `locus` CLI when no subcommand matches), run metrics |
| `locus/audit/` | Palace health auditor (`locus-audit` CLI): scanner, scorer, report |
| `locus/conform/` | OKF conformance: `locus lint` and `locus index` (rules, fixer, generators) |
| `locus/feedback/` | Inferred disagreement-signal classifier consumed by the Locus skill layer (see `spec/inferred-feedback.md`); not imported by the Agent SDK runtime, no `main.py` |
| `locus/mcp/` | MCP server (`locus-mcp` CLI): palace resolution, path safety, tool handlers |
| `locus/recall/` | `locus recall`: SQLite FTS5 index shared with the `memory_search` MCP tool |
| `locus/security/` | Ed25519 signing system (`locus-security` CLI): keys, taint, nonce, middleware |

Plus `locus/cli.py` (console-script dispatch, see below) and `locus/utils.py`
(shared helpers, e.g. `slug_from_path`).

## Console scripts and dispatch

`pyproject.toml` defines four entry points: `locus`, `locus-audit`, `locus-mcp`,
`locus-security`.

`locus` is not one command, it is a router. `locus/cli.py` inspects `sys.argv[1]`:
`recall`, `lint`, and `index` are dispatched straight to `locus.recall.main` and
`locus.conform.main`, without importing the Agent SDK. Everything else falls through
to `locus.agent.main:cli` (the `--palace ... --task ...` agent run), which does
import the SDK. This matters for anything that shells out to `locus` from a hook or
a CI job: `locus lint --check` and `locus recall ...` stay cheap only if nothing adds
an import that drags in `claude_agent_sdk` above the dispatch table.

## Tests and lint

```sh
make test   # uv run pytest tests/unit/ -q
make lint   # uv run ruff check locus/ tests/
```

Equivalent to running `uv sync --extra dev` once, then the two `uv run` commands
directly. CI runs the test suite on Python 3.11 and 3.12 (`.github/workflows/ci.yml`).
There is no repo-wide ruff config file, so `make lint` enforces ruff's own defaults,
not a tuned rule set.

`ruff` is not currently in the `dev` extra in `pyproject.toml`, so on a clean
checkout `make lint` fails to spawn until that is fixed. Until then, run
`uvx ruff check locus/ tests/` instead.

## Conventions easy to get wrong

- **Filesystem case sensitivity.** Root classification in `locus/conform/` tells an
  OKF bundle (`index.md`), a palace (`INDEX.md`), and a Claude Code memory directory
  (`MEMORY.md`) apart by filename. macOS and Windows filesystems are case-insensitive,
  so `Path("index.md").is_file()` also matches an existing `INDEX.md`. Anything that
  needs to distinguish these must use `locus.conform.model.has_file()`, which lists
  the directory and compares names exactly, not `Path.is_file()`.
- **`main` is branch-protected.** Every change goes through a PR from a feature
  branch; direct pushes to `main` are rejected. See `CONTRIBUTING.md` section 5 for
  the squash-merge and post-merge reset workflow.
- **The security module's `session_tainted` latch is one-way.** Once a session
  observes tainted content it stays tainted for the rest of that session
  (`locus/security/taint.py`). Do not add a code path that clears it.
- **`locus/mcp/palace.py` is the safety boundary.** Every MCP filesystem operation
  (path traversal guards, write-blocked directories, palace root resolution) flows
  through it. Changes there affect every tool in `locus/mcp/server.py`.

## Where the normative specs live

`spec/` is the current, authoritative specification set (index format, room
conventions, size limits, recall ranking, lint/index rules, MCP server contract,
audit algorithm, security). `SPECIFICATION.md` at the repo root was the original
v0.1 design brief and is now historical; it has moved to
[`docs/history/SPECIFICATION.md`](docs/history/SPECIFICATION.md).
