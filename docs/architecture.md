# Locus Architecture

## Palace Structure

A palace is a directory tree of markdown files. `INDEX.md` is the only file
loaded automatically — it routes the agent to the right room. Specialty files
are loaded only when relevant.

```mermaid
graph TD
    INDEX["📄 INDEX.md<br/>≤50 lines · always loaded"]

    subgraph ROOM["projects/homelab-iac/"]
        CANON["📄 homelab-iac.md<br/>55 lines · topology + key files"]
        GOTCHAS["📄 technical-gotchas.md<br/>67 lines · confirmed gotchas"]
        SERVICES["📄 platform-services.md<br/>58 lines · versions + IPs"]
        subgraph SESSIONS["sessions/"]
            LOG["📄 2026-02-25.md<br/>24 lines · session log"]
        end
    end

    INDEX -->|"identifies room"| ROOM
    ROOM -.->|"read on demand"| CANON
    ROOM -.->|"read on demand"| GOTCHAS
    ROOM -.->|"read on demand"| SERVICES
    ROOM -.->|"read on demand"| SESSIONS
```

**Size budget:** INDEX ≤ 50 lines · room main files ≤ 200 lines · consolidate when exceeded.

---

## MCP Server

The MCP server exposes five tools over stdio. All path operations go through
`palace.py` safety guards before touching the filesystem.

```mermaid
flowchart LR
    subgraph CLIENT["MCP Client"]
        direction TB
        T1["memory_list"]
        T2["memory_read"]
        T3["memory_write"]
        T4["memory_search"]
        T5["memory_batch"]
    end

    subgraph SRV["locus-mcp · stdio transport"]
        direction TB
        MAIN["main.py<br/>CLI · palace resolution<br/>logging config"]
        SERVER["server.py<br/>FastMCP tool handlers<br/>FTS5 ranked search"]
        PALACE["palace.py<br/>safe_resolve()<br/>assert_writable()<br/>find_palace()"]
    end

    subgraph FS["Palace · filesystem"]
        MD["Markdown files<br/>git-backed"]
    end

    CLIENT <-->|"JSON-RPC / stdio"| MAIN
    MAIN --> SERVER
    SERVER --> PALACE
    PALACE <-->|"read / atomic write"| MD
```

**Safety guards in `palace.py`:**
- Path traversal rejected (`../` escapes palace root)
- Write-blocked dirs: `sessions/`, `_metrics/`, `archived/`, `.sig/`, `.security/` — checked at every depth
- Non-text extensions blocked (`.exe`, `.bin`, …)

When started with `--security`, the MCP server adds a `_SecurityVerifier` layer
between the tool handlers and the filesystem — signatures are checked on every
file read and written on every file write. See [Security Architecture](security.md)
for the full protocol.

---

## Security Layer

The security system sits between tool calls and the agent's context window. It
gives every palace file an Ed25519 signature and every session a unique nonce.

```mermaid
flowchart LR
    subgraph AGENT["Agent context window"]
        SP["System prompt<br/>+ SECURITY CONTEXT block<br/>(signed, nonce-bearing)"]
    end

    subgraph MW["SecurityMiddleware hooks"]
        PRE["PreToolUse<br/>verify signature<br/>block CRITICAL violations"]
        POST["PostToolUse<br/>inject [TRUSTED] / [DATA] tag<br/>detect nonce exfiltration"]
        POSW["post_write_hook<br/>auto-sign new files"]
    end

    subgraph SEC["locus/security/"]
        KEYS["keys.py<br/>Ed25519 keypair"]
        SIGN["signing.py<br/>sign · verify · sidecar"]
        TAINT["taint.py<br/>classify · propagate"]
        NONCE["nonce.py<br/>session nonce"]
    end

    subgraph PALACE["Palace filesystem"]
        MD["memory files<br/>*.md"]
        SIG[".sig/<br/>sidecar signatures"]
        KDIR[".security/keys/<br/>active.pem · active.pub"]
    end

    SP -->|"session nonce embedded"| POST
    PRE -->|"verify_file()"| SIGN
    POSW -->|"sign_file()"| SIGN
    SIGN <--> SIG
    KEYS <--> KDIR
    POST -->|"tag + nonce check"| AGENT
    PRE -->|"allow / deny"| MD
```

**Trust tag flow:** every tool output gets `[TRUSTED]`, `[DATA]`, or
`[CRITICAL-DATA]` injected before the agent sees it. The agent is instructed
to extract facts from `[DATA]` content but never follow directives within it.

**Fail-closed:** `--security` with missing config or keys aborts startup
rather than silently running unsecured.

See [Security Architecture](security.md) for the full protocol, key management,
configuration reference, and design decisions.

---

## Agent Interfaces

Locus is agent-agnostic. The same palace filesystem is shared across all runtimes.
SKILL.md files are the primary interface; MCP is the secondary, protocol-native interface.

```mermaid
flowchart LR
    subgraph RUNTIMES["Agent Runtimes"]
        CC["Claude Code CLI"]
        SDK["Claude Agent SDK"]
        CX["Codex"]
        GM["Gemini"]
    end

    subgraph INTERFACES["Locus Interfaces"]
        SKILL["SKILL.md<br/>~/.claude/skills/locus/"]
        MCP["MCP Server<br/>locus-mcp · stdio"]
        CSKILL[".codex/commands/locus/"]
        GSKILL[".gemini/SKILL.md"]
    end

    PALACE["🏛️  Palace<br/>~/.locus/ or .locus/"]

    CC -->|"skill invocation"| SKILL
    SDK -->|"setting_sources"| SKILL
    CC -->|".mcp.json"| MCP
    CX --> CSKILL
    GM --> GSKILL

    SKILL --> PALACE
    MCP --> PALACE
    CSKILL --> PALACE
    GSKILL --> PALACE
```

> **SDK caveat:** `allowed-tools` frontmatter in SKILL.md is honoured by
> Claude Code CLI only — not by the Agent SDK. Control tool access via `allowedTools`
> in the host config instead.

---

## Memory Lifecycle

```mermaid
flowchart LR
    Q(["Agent query"]) --> IDX["Read INDEX.md<br/>identify room"]
    IDX --> NAV["Read room file<br/>or specialty file"]
    NAV --> ACT["Answer · act<br/>on findings"]
    ACT --> SL["Append to<br/>sessions/YYYY-MM-DD.md"]
    SL -->|"trigger:<br/>≥5 logs or 3 sessions<br/>or room > 150 lines"| CON["locus-consolidate"]
    CON --> UP["Promote findings<br/>to canonical files<br/>Archive session logs"]
    UP -.->|"updated palace"| IDX

    style CON fill:#f0f9ff,stroke:#0284c7
    style UP fill:#f0fdf4,stroke:#16a34a
```

**Two write modes:**
| Mode | When | How |
|---|---|---|
| Session log | Unverified finding, in-progress work | Append-only to `sessions/` |
| Canonical edit | Confirmed, durable fact | Edit room file in place |
