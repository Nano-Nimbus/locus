# Locus

<!-- mcp-name: io.github.Nano-Nimbus/locus -->

[![CI](https://github.com/Nano-Nimbus/locus/actions/workflows/ci.yml/badge.svg)](https://github.com/Nano-Nimbus/locus/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/locus-mcp.svg)](https://pypi.org/project/locus-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Hierarchical markdown-based memory system for autonomous AI agents. Each directory
is a room (locus) in the palace, containing specific knowledge navigated on demand.
Named for the atomic unit of the [Method of Loci](https://en.wikipedia.org/wiki/Method_of_loci).

**Core idea:** Keep context windows small. Load only the room you need, not the whole palace.

---

## How it works

```
palace/
  INDEX.md                    ← always read first (~50 lines max)
  global/
    toolchain/
      toolchain.md            ← canonical facts about tools
  projects/
    my-project/
      my-project.md           ← room overview + key files
      technical-gotchas.md    ← specialty: issues & resolutions
      sessions/
        2026-03-02.md         ← append-only session log
```

An agent reads `INDEX.md`, navigates to the relevant room, and reads only that room.
Session logs accumulate until consolidation merges them into canonical files.

See the [wiki](https://github.com/Nano-Nimbus/locus/wiki) for full documentation.

---

## Quick start

```sh
# Install
pip install locus-mcp
# or: uvx locus-mcp --palace ~/.locus  (no install needed)

# Create a palace from the example template
cp -r example-palace ~/.locus
# Edit ~/.locus/INDEX.md to describe your palace

# Run the MCP server
locus-mcp --palace ~/.locus
# or: LOCUS_PALACE=~/.locus locus-mcp
```

---

## Installation

### MCP server (recommended for MCP-capable clients)

```sh
pip install locus-mcp
```

Or run without installing using `uvx`:

```sh
uvx locus-mcp --palace ~/.locus
```

### Claude Code skills

Install all skills at once using the Makefile:

```sh
git clone https://github.com/Nano-Nimbus/locus.git
cd locus
make install-skills
```

Or install individually:

```sh
cp -r skills/claude/locus ~/.claude/skills/locus
cp -r skills/claude/locus-consolidate ~/.claude/skills/locus-consolidate
cp -r skills/claude/locus-audit ~/.claude/skills/locus-audit
cp -r skills/claude/locus-feedback ~/.claude/skills/locus-feedback
cp -r skills/claude/locus-release ~/.claude/skills/locus-release
cp -r skills/claude/locus-security ~/.claude/skills/locus-security
cp -r skills/claude/locus-palace-init ~/.claude/skills/locus-palace-init
```

Available Claude Code skills:

| Skill | Command | Description |
|---|---|---|
| `locus` | `/locus` | Navigate the palace, read rooms, write session logs |
| `locus-consolidate` | `/locus-consolidate` | Merge session logs into canonical files |
| `locus-audit` | `/locus-audit` | Audit palace health |
| `locus-feedback` | `/locus-feedback` | Record explicit feedback on a palace recall |
| `locus-release` | `/locus-release` | Post-release verification workflow |
| `locus-security` | `/locus-security` | Security conventions for signed palaces |
| `locus-palace-init` | `/locus-palace-init` | Bootstrap a palace from existing memory files |

### Codex

```sh
cp -r skills/codex/locus ~/.codex/skills/locus
cp -r skills/codex/locus-consolidate ~/.codex/skills/locus-consolidate
cp -r skills/codex/locus-palace-init ~/.codex/skills/locus-palace-init
```

### Gemini

Reference `skills/gemini/locus/SKILL.md` from your `.gemini/` directory
or a GitHub Actions workflow (see `skills/gemini/`).

```sh
cp -r skills/gemini/locus-palace-init .gemini/
```

### Agent SDK (Python)

```sh
pip install locus-mcp
locus --palace ~/.locus --task "What toolchain conventions are set?"
```

---

## MCP Server

The `locus-mcp` command exposes five tools over the Model Context Protocol.

**Use stdio for all local integrations** (Claude Desktop, Claude Code, Codex, Gemini — default, no extra flags needed).
SSE transport is available for network deployments (`--transport sse`) and requires `FASTMCP_HOST=0.0.0.0`
to be set explicitly — the server binds to loopback by default.

| Tool | Description |
|---|---|
| `memory_list` | Returns `INDEX.md` (no args) or lists a room's files |
| `memory_read` | Reads any file in the palace |
| `memory_write` | Atomically writes a file (guarded — cannot write to `_metrics/`, `sessions/`, `.sig/`, `.security/`) |
| `memory_search` | Ranked full-text search over the shared FTS5 index (see [Recall](#recall)); ripgrep only without FTS5 |
| `memory_batch` | Reads up to 20 palace files in a single call — use for multi-room loads |

Add `--security` to enable Ed25519 signature verification on reads and automatic signing on writes.
See [Security](#security) below.

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "locus": {
      "command": "locus-mcp",
      "args": ["--palace", "/path/to/palace"]
    }
  }
}
```

Or using `uvx` (no install required):

```json
{
  "mcpServers": {
    "locus": {
      "command": "uvx",
      "args": ["locus-mcp", "--palace", "/path/to/palace"]
    }
  }
}
```

### Cursor / Zed

```json
{
  "mcp": {
    "servers": {
      "locus": {
        "command": "locus-mcp",
        "args": ["--palace", "/path/to/palace"]
      }
    }
  }
}
```

### Environment variable

All clients support `LOCUS_PALACE` as an alternative to `--palace`:

```sh
export LOCUS_PALACE=~/.locus
locus-mcp
```

See [MCP Server Configuration](https://github.com/Nano-Nimbus/locus/wiki/MCP-Server-Configuration)
for the full client setup guide and `spec/mcp-server.md` for architecture details.

---

## Recall

`locus recall` answers "what do I already know about this?" in one call, fast enough
to run on every prompt from a hook. It keeps a SQLite FTS5 index (standard library only,
no PyYAML) over any number of markdown roots: a palace, an OKF bundle, a Claude Code
memory directory, or all of them at once.

```sh
locus recall --root ~/memory --root ./docs "why does the flux kustomization stall"
```

```
Recalled memory:
1. Flux healthcheck stall (human-reviewed, 2026-08-22)
   /home/me/memory/project_flux-healthcheck-stall.md
   Flux Kustomization with wait:true stalls on health checks for a bad revision ...
2. [STALE] Old Flux bootstrap procedure (unverified, 2025-11-02)
   /home/me/docs/runbooks/flux-bootstrap.md
   Bootstrap Flux with a personal access token ...
```

| Flag | Default | Meaning |
|---|---|---|
| `--root DIR` | `.locus.toml`, then `LOCUS_PALACE` | Directory to index; repeatable |
| `-k N` | 3 | Number of hits |
| `--budget BYTES` | 4096 | Hard cap on text output |
| `--include journal` | off | Include `type: Journal` files |
| `--type TYPE` | all | Only this frontmatter type; repeatable |
| `--json` | off | Print a JSON list instead of text |
| `--refresh` | off | Rebuild the index from scratch |

Frontmatter drives the result: `title` (or `name`, or the first heading), `description`,
`tags`, `type` (or `metadata.type`), `modified` (else `generated.at`, else file mtime),
`status`, `stale_after`, and `verified`. Ranking is bm25 with title and description
weighted above the body; exact ties go to human-reviewed files, then to the newest
`modified`. A hit is flagged `STALE` when its `stale_after` has passed or its `status` is
`deprecated`. Trust tiers follow OKF: `unverified`, `machine-confirmed` (only non-human
`verified` entries), `human-reviewed` (any `verified` entry whose `by` starts with `human:`).

Roots can live in a `.locus.toml` in the project or any parent directory:

```toml
[recall]
roots = ["docs", "~/memory/shared"]
```

The index is stored at `${XDG_CACHE_HOME:-~/.cache}/locus/<hash-of-roots>.sqlite`, never
inside a root, and is refreshed incrementally (mtime, then content hash) on every call.
With no hits the text output is empty and the exit status is still 0, so a prompt hook can
call it unconditionally:

```sh
#!/bin/sh
# Claude Code UserPromptSubmit hook: whatever this prints is injected as context.
prompt=$(jq -r .prompt)
exec locus recall -k 3 --budget 4096 "$prompt"
```

The MCP server's `memory_search` uses the same index, so MCP results are ranked the
same way. Full rules in [`spec/recall.md`](spec/recall.md).

---

## Security

The security system (`--security`) gives every palace file an Ed25519 signature and every agent session a unique cryptographic nonce. Tool outputs are tagged `[TRUSTED]`, `[DATA]`, or `[CRITICAL-DATA]` before the agent sees them. The agent skill (`locus-security`) teaches agents to extract facts from `[DATA]` content but never follow directives within it.

```sh
# One-time setup
cp templates/locus-security.yaml ~/.locus/locus-security.yaml
locus-security init-keys --palace ~/.locus
locus-security sign-all --palace ~/.locus

# Run with security enabled
locus-mcp --palace ~/.locus --security
locus --palace ~/.locus --security --task "..."
```

The `locus-security` CLI has four subcommands: `init-keys`, `sign-all`, `verify-all`
(exit 1 if any file fails verification, or if the palace holds no signable files at all),
and `rotate-keys`. `sign-all` names and skips any file it cannot read as UTF-8 rather than
aborting the run, and exits 1 if it skipped anything. Neither command follows a symlink
whose target resolves outside the palace: those are named and skipped by `sign-all`, and
reported as failures by `verify-all`.

**Threat model:** direct prompt injection, memory poisoning, indirect injection via external data, nonce exfiltration, multi-turn context drift.

See [`docs/security.md`](docs/security.md) for the full protocol, configuration reference, and design decisions.

---

## Benchmarks

Palace navigation loads **52% fewer context lines** than flat memory for specific queries,
while maintaining full recall. Session-only queries (recent work not yet consolidated)
are accessible only via the palace.

```
Palace: 822 lines / 9 queries found   avg  91 lines/query · 3.2 calls
Flat:  1719 lines / 8 queries found   avg 191 lines/query · 2.0 calls
```

See [`docs/benchmarks.md`](docs/benchmarks.md) for charts and full methodology.

---

## Structure

```
example-palace/   Copy-paste palace template to get started
spec/             Palace convention definitions:
  index-format.md       INDEX.md rules and routing
  room-conventions.md   Room structure and naming
  size-limits.md        Context budget thresholds
  write-modes.md        Session logs vs canonical edits
  mcp-server.md         MCP server architecture and safety model
  recall.md             locus recall: roots, index, ranking, trust tier, STALE
  metrics-schema.md     Run metrics JSON schema
  audit-algorithm.md    Palace health scoring
  health-report-format.md  Audit report structure
  inferred-feedback.md  Disagreement signal classification
templates/        Copy-paste templates for INDEX.md, rooms, session logs, locus-security.yaml
skills/
  claude/         SKILL.md files for Claude Code + Agent SDK
    locus/              Palace navigation and memory management
    locus-consolidate/  Room consolidation
    locus-security/     Security conventions (trust tags, nonce discipline)
  codex/          Codex-compatible skill files
  gemini/         Gemini CLI + GitHub Actions skill files
docs/
  architecture.md       Mermaid diagrams — palace, MCP, security, agent interfaces
  benchmarks.md         Benchmark results and charts (palace vs flat, security overhead)
  onboarding.md         Step-by-step agent onboarding guide
  security.md           Full security protocol, key management, config reference
  bench/                Per-version benchmark JSON (read by generate-charts.py)
scripts/
  bench-mcp.py          45-case MCP integration benchmark (includes security + batch)
  bench-compare.py      Palace vs flat recall comparison
  generate-charts.py    Regenerate docs/img/ charts (reads docs/bench/ automatically)
locus/
  agent/          Python Agent SDK (CLI + metrics)
  audit/          Palace health auditor (locus-audit CLI)
  feedback/       Inferred feedback classifier
  mcp/            MCP server (locus-mcp CLI) — palace.py, server.py, main.py
  recall/         locus recall: FTS5 index shared with memory_search
  security/       Ed25519 security system — keys, signing, taint, nonce, middleware
  utils.py        Shared utilities (slug_from_path)
```

---

## Roadmap

| Milestone | Status | Focus |
|---|---|---|
| v0.1 - Foundation | ✅ Complete | Spec, conventions, size limits |
| v0.2 - Core Palace | ✅ Complete | Templates, skills, Agent SDK, benchmark |
| v0.3 - Performance Metrics | ✅ Complete | Context tracking, feedback, suggestions |
| v0.4 - Self Evaluation | ✅ Complete | Palace audit, health reports, inferred feedback |
| v0.5 - MCP Server | ✅ Complete | MCP server with memory_list/read/write/search |
| v0.6 - Public release | ✅ Complete | Benchmarks, docs, CI, PyPI |
| v0.7 - Remote MCP Server | ✅ Complete | SSE transport, Bearer auth, Docker image, K8s deploy |
| v0.8 - Auto-Memory Bridge | ✅ Complete | Claude Code auto-memory detection, memory_batch tool |
| v0.9 - Security System | ✅ Complete | Ed25519 signing, taint tracking, nonce watermark, --security flag |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, test instructions, and PR guidelines.

## License

[MIT](LICENSE)
