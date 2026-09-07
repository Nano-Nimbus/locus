# MCP Server

Defines the Locus MCP server: transport, tool surface, safety model, and
configuration. This is a thin file-system adapter over the existing palace
structure — no new concepts, just a standardised interface that any MCP-capable
client (Claude Desktop, Cursor, Zed) can consume without needing the skill files.

---

## Transport

**stdio** (default) — the standard for locally-run MCP servers. The server is launched
as a subprocess; the client communicates over stdin/stdout. No network port is opened.

**SSE** (`--transport sse`, added in v0.7.0) — HTTP server-sent events transport for
network deployments (homelab K8s, n8n, Windmill, multi-client scenarios). Runs via
uvicorn. Auth via `LOCUS_API_KEY` Bearer token (recommended).

```
# Local (default)
locus-mcp --palace ~/.locus

# Network service
FASTMCP_HOST=0.0.0.0 FASTMCP_PORT=8000 LOCUS_API_KEY=<token> \
  locus-mcp --transport sse --palace /palace
```

**DNS rebinding protection**: FastMCP 1.26.0+ restricts allowed `Host` headers to
loopback by default. For reverse-proxy deployments (Tailscale, K8s ingress), set
`LOCUS_ALLOWED_HOSTS` to a comma-separated list of extra hostnames:

```
LOCUS_ALLOWED_HOSTS=myhost.ts.net,myservice.svc.cluster.local:*
```

The `:*` suffix is FastMCP's wildcard for any port on that hostname.

---

## Tool Surface

Five tools cover the full palace lifecycle:

| Tool | Arguments | Returns | Notes |
|---|---|---|---|
| `memory_list` | `path?: str` | Markdown or file listing | Omit `path` → returns `INDEX.md` content. Pass a room path → lists files in that room. |
| `memory_read` | `path: str` | File contents as string | Reads any file within the palace. Path is relative to the palace root. |
| `memory_write` | `path: str`, `content: str` | Confirmation message | Atomic write. Refuses writes outside palace root or into `_metrics/`. |
| `memory_search` | `query: str`, `path?: str` | Ranked result list | Full-text search across the palace (or a sub-path). Uses ripgrep if available, falls back to Python `re`. |
| `memory_batch` | `paths: list[str]` | Multi-file contents | Reads up to 20 files in one call. Sections separated by `---`. Errors noted inline; never raises for missing/traversal. |

### Design rationale

- **`memory_list` without a path returns `INDEX.md`** — this mirrors the skill's
  Step 1 (always enter via the index) and gives clients the routing table they
  need to navigate further.
- **Writes are blocked to `_metrics/`** — that directory is owned by the
  `locus-audit` / metrics pipeline. MCP clients should not write metrics files.
- **`memory_search` scope defaults to the full palace** — the optional `path`
  argument narrows the search to a specific room or subdirectory, which is
  useful when the client already knows which room to search.
- **`memory_batch` for research startup** — agents beginning a task often need
  5–10 rooms at once. Batching them into a single MCP call reduces round-trips
  and keeps task startup fast. Errors are returned inline so a partial result
  is always available.

---

## Safety Model

All path arguments are validated before any file I/O:

1. **Resolve to absolute path** within the palace root.
2. **Reject path traversal** — any resolved path that does not start with the
   palace root prefix raises an error.
3. **Reject writes to system directories** — `_metrics/`, `sessions/`,
   `archived/`, `.sig/`, and `.security/` are read-only via MCP. The first three
   are managed by the pipeline; `.sig/` and `.security/` contain signature sidecars
   and key material that agents must never forge or overwrite directly.
4. **Reject writes to binary files** — only `.md`, `.txt`, `.json`, `.yaml`,
   and `.yml` extensions are permitted for writes.

---

## Configuration

The palace root is supplied at server startup:

```
locus-mcp --palace /path/to/palace
```

Or via environment variable:

```
LOCUS_PALACE=/path/to/palace locus-mcp
```

If neither is provided, the server resolves the palace using this priority order:

1. `--palace` CLI argument
2. `LOCUS_PALACE` environment variable
3. `.locus/` in the current working directory
4. **Auto-memory bridge** — `~/.claude/projects/<slug>/memory/` (see below)
5. `~/.locus/` global palace (bootstrapped if absent)

Whichever directory steps 1, 2, 3, or 5 resolve to is given a skeleton
`INDEX.md` when it has none (an empty directory also gets `global/` and
`projects/`). Existing files are never modified, and a read-only root is
logged and skipped. Step 4 is the exception: a Claude Code memory directory
already has `MEMORY.md` as its entry point and is owned by Claude Code, so
Locus never writes into it during resolution.

---

## Auto-Memory Bridge

When `locus-mcp` is started from a project directory without an explicit
`--palace` argument, it automatically checks whether Claude Code's auto-memory
system has a memory directory for that project.

**Slug derivation**: Claude Code slugifies a project's absolute path by replacing
every `/` with `-`. For example:

```
/home/user/git/myproject  →  -home-user-git-myproject
```

The server checks for `~/.claude/projects/<slug>/memory/` and uses it as the
palace root if it exists. This makes Locus the canonical memory layer for any
project that already uses Claude Code's auto-memory, with zero configuration.

**Log signal**: When auto-memory is selected, the server logs at INFO level:

```
palace resolved from auto-memory: /home/user/.claude/projects/-home-user-git-myproject/memory
```

---

## Simplify Integration Pattern

A `code-patterns` palace room enables persistent project-specific context across
agent sessions. Any code-analysis agent (e.g. a `simplify` pass) writes
discovered conventions into this room after each session. Future runs read the
room at startup to avoid re-deriving the same patterns.

**Suggested room structure**:

```
projects/<project-slug>/
  code-patterns/
    code-patterns.md    # Canonical conventions (naming, idioms, anti-patterns)
    sessions/           # Per-session logs (raw findings before consolidation)
```

**Workflow**:

1. Agent starts → `memory_batch(["INDEX.md", "projects/<slug>/code-patterns/code-patterns.md"])`
2. Agent analyses code, discovers conventions
3. Agent writes updated conventions → `memory_write("projects/<slug>/code-patterns/code-patterns.md", ...)`
4. After 3–5 sessions, `locus-consolidate` merges session logs into the canonical file

This pattern gives any stateless agent a persistent project memory without
requiring external databases or vector stores.

---

## Implementation

- **Package**: `locus/mcp/`
- **Server**: `locus/mcp/server.py` — `FastMCP` instance, all four tools
- **Entry point**: `locus/mcp/main.py` — CLI wrapper (`locus-mcp`)
- **Framework**: `mcp.server.fastmcp.FastMCP` (stdio transport)
- **Search backend**: `subprocess` call to `rg` (ripgrep); Python `re` fallback

---

## MCP Client Configuration Examples

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "locus": {
      "command": "uvx",
      "args": ["locus-agent", "locus-mcp", "--palace", "/path/to/palace"]
    }
  }
}
```

### Cursor / Zed (`.cursor/mcp.json` or `.zed/settings.json`)

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

---

## Relationship to v1 Skills

The MCP server and the skill files (`skills/claude/locus/`) are complementary,
not mutually exclusive:

| Capability | Skill | MCP |
|---|---|---|
| Navigate palace | Step 1–2 (read INDEX, follow rooms) | `memory_list` + `memory_read` |
| Write canonical facts | Step 3 | `memory_write` |
| Write session logs | Step 4 | `memory_write` (append) |
| Full-text search | Not available | `memory_search` |
| Works without tool approval | No (Bash/Read/Write required) | Yes (single MCP permission) |

The MCP layer is the recommended interface for MCP-capable clients. The skills
remain the interface for Claude Code, which does not use MCP for local tools.
