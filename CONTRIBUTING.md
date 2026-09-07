# Contributing to Locus

Locus is a focused project with a clear design principle:

> **Load only what you need. Never the whole palace.**

This guide covers everything needed to develop, test, benchmark, document, and ship changes
— whether you are a human contributor or an AI agent working autonomously.

---

## Table of contents

1. [Project intent](#1-project-intent)
2. [Getting started](#2-getting-started)
3. [Repository structure](#3-repository-structure)
4. [Architecture overview](#4-architecture-overview)
5. [Development workflow](#5-development-workflow)
6. [Test suite](#6-test-suite)
7. [Benchmarks](#7-benchmarks)
8. [Version numbering](#8-version-numbering)
9. [Documenting changes](#9-documenting-changes)
10. [Design constraints](#10-design-constraints)
11. [Security module](#11-security-module)
12. [What to contribute](#12-what-to-contribute)

---

## 1. Project intent

Locus solves a specific problem: **autonomous AI agents have large context windows but most
knowledge is irrelevant to any given task.** Loading an entire memory dump on every invocation
is slow and burns tokens. Locus structures memory hierarchically so agents navigate on demand
— reading the index, finding the right room, reading only that room.

**What Locus is:**
- A filesystem convention (directories as rooms, markdown as knowledge)
- A set of agent skills (SKILL.md files for Claude, Codex, Gemini)
- An MCP server (protocol-native memory access with safety guards)
- A Python Agent SDK entrypoint (for benchmarking and autonomous runs)
- An optional security layer (Ed25519 signatures, taint tracking, nonce watermarking)

**What Locus is not:**
- A vector database or embedding store
- A general-purpose file sync tool
- A replacement for project-specific documentation
- Tied to any single agent runtime (it is agent-agnostic by design)

The security system (v0.9+) extends this into runtime trust enforcement: making injected
content cryptographically distinguishable from operator-authorized content.

---

## 2. Getting started

```sh
git clone https://github.com/Nano-Nimbus/locus
cd locus
uv sync --extra dev
```

Verify the environment:

```sh
# Unit tests — all 256 must pass
uv run pytest tests/unit/ -v

# MCP integration benchmark — smoke-test the live server
uv run scripts/bench-mcp.py
# Expected: 45/45 pass, avg ~5ms

# Palace vs flat recall comparison
uv run scripts/bench-compare.py
# Expected: palace −52% lines, flat misses Type C (session-only) queries
```

---

## 3. Repository structure

```
locus/
  agent/          Python Agent SDK — CLI entrypoint, metrics collector
  audit/          Palace health auditor (locus-audit CLI)
  feedback/       Inferred feedback signal classifier
  mcp/            MCP server (locus-mcp CLI)
    palace.py       Path safety guards, palace resolution, auto-memory bridge
    server.py       FastMCP tool handlers (memory_list/read/write/search/batch)
    main.py         CLI — palace resolution, logging, --security flag
  security/       Ed25519 security system (v0.9+)
    config.py       locus-security.yaml loader, boundary criticality model
    keys.py         Keypair generation, PKCS8 storage, rotation
    signing.py      sign_file() / verify_file(), .sig/ sidecar protocol
    nonce.py        Per-session HMAC-SHA256 nonce, system prompt injection
    taint.py        TaintLevel, TaintTracker (session_tainted latch), classify_content()
    middleware.py   SecurityContext, SecurityMiddleware (Pre/PostToolUse hooks)
    main.py         CLI (locus-security): init-keys, sign-all, verify-all, rotate-keys
    __init__.py     Public API: build_security_context()
  utils.py        Shared utilities — slug_from_path()
spec/             Palace convention definitions (the shared contract between agents)
templates/        Copy-paste starting points for palace files and locus-security.yaml
skills/
  claude/         SKILL.md files for Claude Code + Agent SDK
  codex/          Codex-compatible skill files
  gemini/         Gemini CLI + GitHub Actions skill files
docs/             Reference documentation (architecture, security, benchmarks, onboarding)
  bench/          Per-version benchmark JSON — read by generate-charts.py
  img/            Generated SVG charts
scripts/          Developer tools (benchmarks, chart generation)
tests/
  unit/           256 unit tests covering all layers
  fixtures/       palace/ and flat-palace/ for benchmark comparison
example-palace/   Copy-paste template to create a new palace
```

---

## 4. Architecture overview

Read [`docs/architecture.md`](docs/architecture.md) for Mermaid diagrams of all subsystems.
The key layers:

```
Palace filesystem  ←  MCP server (locus-mcp)  ←  MCP clients (Claude, Codex, Gemini)
                   ←  Agent SDK (locus)        ←  direct Python invocation
                   ←  Security layer            ←  when --security is active
```

**Critical files to understand before making changes:**

| File | Why it matters |
|---|---|
| `locus/mcp/palace.py` | Every filesystem operation flows through here. Path safety, write guards, palace resolution. Do not bypass. |
| `spec/size-limits.md` | The context budget thresholds. Changes here affect every agent using Locus. |
| `spec/mcp-server.md` | MCP tool contracts. Changes to tool signatures are breaking changes. |
| `locus/security/middleware.py` | The trust enforcement boundary. Changes here affect the security guarantee. |
| `locus/security/taint.py` | The `session_tainted` latch is a one-way flag — it cannot and must not be clearable within a session. |

---

## 5. Development workflow

### Branch policy

`main` is branch-protected — direct pushes are rejected. All changes go through a PR.

```sh
git checkout main && git pull
git checkout -b your-feature-branch
# ... make changes ...
git push -u origin your-feature-branch
gh pr create
```

### After a squash-merge

Squash merges leave local history diverged. Always reset after your PR lands:

```sh
git checkout main
git reset --hard origin/main
```

Never `git push --force` to `main`. If you see "your branch is ahead of origin/main" after a
squash merge, the reset above is the correct fix.

### CI

Two checks run on every PR:
- `Unit tests (Python 3.11)` — `uv run pytest tests/unit/`
- `Unit tests (Python 3.12)` — same, different runtime

Both must pass before merge. The CI workflow is in `.github/workflows/ci.yml`.

### Merge policy

PRs are **squash-merged**. Write a meaningful PR title — it becomes the squash commit message
on `main`. The PR description is preserved in the merge commit body.

---

## 6. Test suite

```sh
# Run all unit tests
uv run pytest tests/unit/ -v

# Run only security tests
uv run pytest tests/unit/security/ -v

# Run a specific module
uv run pytest tests/unit/test_mcp.py -v
```

**Test layout:**

| Module | What it covers |
|---|---|
| `tests/unit/test_mcp.py` | MCP tools, path safety, write guards, search |
| `tests/unit/test_metrics.py` | Agent run metrics schema and aggregation |
| `tests/unit/test_audit.py` | Palace health auditor (scanner, scorer, reporter) |
| `tests/unit/test_signals.py` | Inferred feedback signal classifier |
| `tests/unit/test_auto_memory.py` | Claude Code auto-memory bridge |
| `tests/unit/security/test_config.py` | Security config loading and defaults |
| `tests/unit/security/test_keys.py` | Keypair generation, save, load, rotation |
| `tests/unit/security/test_signing.py` | File signing, verification, sidecar format |
| `tests/unit/security/test_nonce.py` | Nonce generation, injection, uniqueness |
| `tests/unit/security/test_taint.py` | Taint classification, nonce detection, tracker |
| `tests/unit/security/test_review_fixes.py` | Regression tests for all P1/P2 review findings |
| `tests/unit/security/test_cli.py` | `locus-security` CLI: key init, bulk sign/verify, rotation |

**Coverage requirements:**

- Every new MCP tool requires tests in `tests/unit/test_mcp.py`
- Every new security behaviour requires tests in `tests/unit/security/`
- Regression tests are required for any bug fix that had a corresponding review finding
- The `session_tainted` latch must remain one-way — test that it cannot be cleared

---

## 7. Benchmarks

Three benchmark scripts serve different purposes. Run them before and after any change that
could affect performance or recall accuracy.

### MCP integration benchmark

```sh
uv run scripts/bench-mcp.py
# 45 cases: memory_list, memory_read, memory_write, memory_search, memory_batch
# Expected: 45/45 pass, avg ~5ms, p95 ~15ms

# Save results for a new version
uv run scripts/bench-mcp.py --version 0.9.0
# Writes to docs/bench/v0.9.0.json
```

The benchmark spins up a real `locus-mcp` subprocess against `tests/fixtures/palace/`.
FastMCP guard errors come back as `isError=True` tool results, **not** Python exceptions —
check `resp.isError`, never `try/except` around tool calls.

### Palace vs flat recall comparison

```sh
uv run scripts/bench-compare.py
# 9 scenarios: Type A (specific), Type B (broad), Type C (session-only)
# Palace should load ~52% fewer lines and maintain full recall
# Flat baseline has no session logs structurally — Type C always fails
```

Fixtures:
- `tests/fixtures/palace/` — hierarchical palace with sessions
- `tests/fixtures/flat-palace/` — flat 184-line MEMORY.md baseline

### Chart generation

```sh
uv run scripts/generate-charts.py
# Reads docs/bench/*.json automatically
# Writes docs/img/latency-by-category.svg, docs/img/latency-comparison.svg,
#         docs/img/latency-trend.svg
```

Run this after saving new benchmark results. The generated SVGs are committed and
referenced from `docs/benchmarks.md`.

### Benchmark data format

Each `docs/bench/vX.Y.Z.json` follows this schema:

```json
{
  "version": "0.9.0",
  "timestamp": "2026-03-11T...",
  "results": [
    {
      "name": "memory_read_index",
      "category": "read",
      "passed": true,
      "latency_ms": 4.2
    }
  ],
  "summary": {
    "total": 45,
    "passed": 45,
    "avg_ms": 4.9,
    "p95_ms": 10.3
  }
}
```

When adding a new benchmark case, add it to `scripts/bench-mcp.py` and document
the expected result range in `docs/benchmarks.md`.

---

## 8. Version numbering

Locus uses **semver** (`MAJOR.MINOR.PATCH`). In practice:

| Increment | When |
|---|---|
| `MINOR` | New feature, new MCP tool, new CLI flag, new module |
| `PATCH` | Bug fix, documentation update, dependency bump |
| `MAJOR` | Breaking change to MCP tool signatures, palace convention, or public Python API |

### Bumping the version

Version is set in one place — `pyproject.toml`:

```toml
[project]
version = "0.9.0"
```

`uv.lock` reflects the version automatically (the `locus-mcp` package entry updates).
Do not manually edit `uv.lock` — run `uv sync` after changing `pyproject.toml` and
commit the updated lockfile.

### Release checklist

Before tagging a release:

1. `pyproject.toml` — update `version`
2. `uv sync` — regenerate `uv.lock`
3. `CHANGELOG.md` — add a section for the new version (see [Documenting changes](#9-documenting-changes))
4. `README.md` — update roadmap table
5. `docs/benchmarks.md` — add benchmark results if performance-relevant
6. Run `uv run pytest tests/unit/` — all tests pass
7. Run `uv run scripts/bench-mcp.py --version X.Y.Z` — save benchmark JSON
8. Run `uv run scripts/generate-charts.py` — regenerate SVGs if benchmark changed
9. Open a PR — do not push directly to `main`
10. After merge: `git checkout main && git reset --hard origin/main`
11. Tag: `git tag vX.Y.Z && git push origin vX.Y.Z`

PyPI publish and Docker build are triggered automatically by the tag via GitHub Actions.

---

## 9. Documenting changes

### CHANGELOG.md

Every PR that merges to `main` should have a CHANGELOG entry. Format:

```markdown
## vX.Y.Z — YYYY-MM-DD

### Short title describing the theme

One or two sentences explaining what changed and why.

**Subsection (new feature / fix / refactor):**

- `fix(module)`: what was broken and what was fixed
- `feat(module)`: what was added and what it enables
- `refactor(module)`: what moved and why

**Tests:** what coverage was added.
```

Match the detail level to the change size. A bug fix needs one bullet.
A new subsystem (like `locus/security/`) needs subsections.

### docs/ format

Follow the style of existing docs files — all use:
- H2 (`##`) section headers
- Mermaid diagrams for architecture and data flow (fenced ` ```mermaid ` blocks)
- Tables for reference data (config options, tool descriptions, constraint rationale)
- Code blocks with language tags for all commands and file content
- `<br/>` not `\n` for line breaks inside Mermaid node labels

**Mermaid node label line breaks:** use `<br/>`, not `\n`. The renderer in GitHub
does not support `\n` inside quoted labels.

When adding a new doc:
1. Link it from `README.md` (Structure section and/or a relevant section)
2. Link it from `docs/architecture.md` if it describes a subsystem
3. Reference it from `CONTRIBUTING.md` if it affects the development workflow

### Scanning for personal paths before committing

Before committing any new file (especially skills, docs, spec files):

```sh
grep -r "/home/" . --include="*.md" --include="*.yaml" --include="*.py"
grep -r "dank" . --include="*.md"
```

Use generic references: "the locus source repository", "the palace root", "your home directory".

---

## 10. Design constraints

These are load-bearing — do not work around them:

| Constraint | Reason | Where enforced |
|---|---|---|
| `INDEX.md` ≤ 50 lines | Always loaded — must stay small | `spec/size-limits.md`, `locus-consolidate` skill |
| Room main files ≤ 200 lines | Context budget per room | `spec/size-limits.md`, audit scorer |
| `sessions/` write-blocked via MCP | Consolidation is explicit, not automatic | `palace.py` `_WRITE_BLOCKED_DIRS` |
| `.sig/` and `.security/` write-blocked via MCP | Agents cannot forge signatures or overwrite keys | `palace.py` `_WRITE_BLOCKED_DIRS` |
| Path traversal strictly rejected | All palace access must be scoped to palace root | `palace.py` `safe_resolve()` |
| stdio is the default MCP transport | All local integrations use stdio | `main.py` default; SSE is opt-in |
| `session_tainted` is a one-way latch | A compromised session cannot un-taint itself | `taint.py` — no `clear_tainted()` method exists by design |
| `auto_sign_writes` defaults to `False` | Prevents taint laundering by default | `config.py` `SigningConfig` |
| Security is opt-in (`--security` flag) | Existing palace users are unaffected | `main.py` and `locus/agent/main.py` |
| `_slug_from_path` lives in `locus/utils.py` | `security/` must not depend on `mcp/` | `locus/utils.py` `slug_from_path()` |

---

## 11. Security module

The security module (`locus/security/`) is the most sensitive part of the codebase.
Changes here require extra care.

### Module layout and dependency order

```
locus/utils.py          ← no locus dependencies
locus/security/config.py   ← imports yaml only
locus/security/keys.py     ← imports cryptography
locus/security/signing.py  ← imports keys, yaml
locus/security/nonce.py    ← imports hashlib, secrets
locus/security/taint.py    ← imports config, signing
locus/security/middleware.py ← imports all of the above
locus/security/__init__.py   ← public API
locus/mcp/palace.py      ← imports locus.utils
locus/mcp/server.py      ← imports locus.security (optional)
```

`security/` must not import from `mcp/`. If a utility is needed by both, it belongs in
`locus/utils.py`. This was corrected in v0.9.0 (the `_slug_from_path` extraction).

### The taint laundering threat

The most significant design risk in the security system is **taint laundering**: an agent
that has processed compromised content writes it to a palace file, which the system then
auto-signs as `[TRUSTED]`. Future sessions read that file as operator-authorized.

The defense is two-layered:
1. `auto_sign_writes` defaults to `False` — operators must explicitly opt in
2. `TaintTracker.session_tainted` is a one-way latch — once any TAINTED content is
   processed, `post_write_hook` suppresses auto-signing for the rest of the session

**Never add a mechanism to clear or reset `session_tainted`** within a session.
The only safe reset is starting a fresh session.

### Key management

The signing protocol uses PKCS8 PEM format with optional AES-256-CBC passphrase encryption:

```
.security/keys/
  active.pem          Ed25519 private key (PKCS8, optionally encrypted)
  active.pub          Ed25519 public key (DER, unencrypted)
  active.json         Key metadata (key_id, created_at, expires_at)
  retired/
    <key_id>.pub      Retired public key (only — private key never retained after rotation)
    <key_id>.json     Retired key metadata
```

Set `LOCUS_SIGNING_PASSPHRASE` to encrypt the private key at rest. Rotation archives the
current public key to `retired/` and generates a new active pair. The private key is
**never** retained after rotation — only the public key is archived for verifying
files signed by the previous key.

### Sidecar signature format

Signatures are stored as YAML in `.sig/<palace-relative-path>.sig`:

```yaml
protocol: locus-sig-v1
key_id: locus-2026-03-11
signed_at: 2026-03-11T12:00:00+00:00
signature: <base64-encoded Ed25519 signature>
```

The canonical payload signed is:
```
locus-sig-v1\n<palace_slug>\n<rel_path>\n<signed_at>\n<sha256_hex_of_content>
```

Content is normalized (LF line endings, BOM stripped) before hashing. Do not change
this normalization without versioning the protocol (`locus-sig-v2`).

### Adding a new security feature

1. Identify which layer it belongs to (config model, signing protocol, taint classification, middleware enforcement)
2. Add tests in `tests/unit/security/` — both positive and negative cases
3. If the feature changes the signing protocol, bump the protocol version string
4. If the feature changes config schema, update `templates/locus-security.yaml` and `docs/security.md`
5. If the feature adds a new trust tag, update `skills/claude/locus-security/SKILL.md`

---

## 12. What to contribute

**Good fits — open a PR:**
- Bug fixes in `locus/mcp/`, `locus/audit/`, `locus/agent/`, `locus/security/`
- New MCP tool behaviours (with tests)
- Skill files for new agent runtimes (`skills/<runtime>/`)
- Benchmark improvements and new test cases
- Documentation fixes and improvements
- Performance improvements that don't change observable behaviour

**Discuss first — open an issue:**
- Changes to the palace convention specs (`spec/`) — these are the shared contract
  between agents and shouldn't change without deliberate versioning
- New CLI entry points or package restructuring
- Changes to `palace.py` safety model (write guards, path resolution)
- Changes to the signing protocol (sidecar format, canonical payload)
- Changes to the taint model (especially any mechanism that clears taint)
- New mandatory dependencies (Locus has intentionally minimal runtime deps)

**Not accepted:**
- Features that load the full palace automatically
- Auto-expiry or TTL on memory files
- Mechanisms to bypass path traversal checks
- Any mechanism to clear `session_tainted` within a session
- Inline signatures (signatures embedded in the markdown files themselves)
