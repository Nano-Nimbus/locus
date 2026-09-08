# Project: Locus

> **Historical document.** This is the v0.1 design brief for Locus, written before any
> code existed. It is superseded by the current specification set under
> [`spec/`](../../spec/); start there for how Locus actually behaves. It is kept here
> because it is the only record of why the project is named Locus (the Method of Loci)
> and the constraints it was founded on.
>
> Two of its forward-looking notes have since shipped: "v2: search capability
> (grep/FTS), likely the point where MCP becomes the right interface" (below, under
> Discovery & Reading) is now `locus recall` plus the `memory_search` MCP tool (see
> [`spec/recall.md`](../../spec/recall.md) and [`spec/mcp-server.md`](../../spec/mcp-server.md)),
> and the "foundation for the v0.5 MCP server" note (under Runtime) is `locus-mcp`,
> which shipped in v0.5.0.

## Pretext

Using markdown-based memory across multiple AI-assisted projects, a recurring pattern emerged:
agents navigate structured directories of markdown files to keep information handy while keeping
context windows small. Locus formalizes and generalizes that pattern into a reusable system for any agent.

## Intent

Build a hierarchical markdown-based memory system for autonomous AI agents. Named for the atomic
unit of the Method of Loci: each directory is a "room" (locus) containing specific knowledge,
navigated on demand. The goal is to keep context windows as small as possible while enabling
precise, deep recall across sessions and projects.

## Name

**Locus** — singular of loci. The foundational unit of the memory palace.
GitHub: https://github.com/Nano-Nimbus/locus

## Guidelines

1. Uses markdown files and directories exclusively — no databases, no external services required.
2. Agent-agnostic: usable by Claude, Codex, Gemini, or any LLM-based agent without agent-specific tooling.
3. Flexible and expandable — no limit to palace depth.
4. Reference existing implementations in:
   - `~/.claude/{skills,commands}`
   - `~/.claude/projects/<project>/memory`
   - Any project with `.codex`, `.gemini`, `.claude` config directories

## Architecture Decisions

### Structure
- **Hierarchy**: directory = room, file = knowledge. Depth is unlimited.
- **Index**: a root `INDEX.md` (~50 lines max) maps the palace — room names, one-line descriptions,
  and paths — without loading any content. Agents always enter here first.
- **Memory layers**: two tiers:
  - **Global palace** — cross-project facts, toolchain preferences, recurring patterns, user conventions
  - **Per-project rooms** — domain-specific knowledge scoped to a single project

### Discovery & Reading
- Agents enter via `INDEX.md` and navigate to specific rooms on demand.
- v1: path-based navigation only (agent knows which room to enter based on the index).
- v2: search capability (grep/FTS), likely the point where MCP becomes the right interface.

### Writing
- Agents write autonomously — no human approval step (this is agent infrastructure, not user-facing).
- Convention: **append-only** for session logs, **explicit edits** for canonical fact files.
- Git is the audit trail. No separate versioning layer needed.

### Lifecycle
- **No automatic memory expiry** in v1. Memories are retained indefinitely.
- **Consolidation** is event-driven: triggered when a room exceeds a size threshold or after N
  sessions touch the same room. Implemented as a Locus skill (similar to `knowledge-capture`).
- Existing memory files in mature projects are the reference implementation to
  learn from — not a migration target. Locus produces a spec those files already mostly satisfy.

### Performance Metrics (v1, optional)
When enabled, Locus tracks:
- **Context size** — lines and estimated tokens generated per retrieval
- **Retrieval depth** — number of files read to satisfy a query (deep traversals signal structural issues)
- **Disagreement signal** — explicit user command (e.g., `/locus feedback`) for v1; inferred from
  conversational cues is a v2 consideration

When disagreement or oversized context is detected, the agent suggests:
- Raising the context size limit, OR
- Splitting the room into smaller files
...as options for the user to choose from.

### Self-Evaluation (v2, experimental)
A "palace audit" capability where the agent reflexively reviews its own rooms for:
- Duplicate or contradictory facts
- Rooms never accessed (candidates for archival)
- Rooms exceeding size thresholds
- Stale entries (timestamps older than a configurable threshold)

Outputs a health report and proposes restructuring. Builds on the `knowledge-capture` skill pattern.

### Runtime
Two complementary interfaces:

**SKILL.md files** (primary) — compatible with Claude Code CLI and the Claude Agent SDK.
Loaded via `settingSources: ["user", "project"]` in SDK apps. Must not rely on
`allowed-tools` SKILL.md frontmatter (only honoured by Claude Code CLI, not the SDK).

**Agent SDK entrypoint** (`locus/agent/`, Python) — runs Locus autonomously against a
palace directory. Serves as the benchmark harness and the foundation for the v0.5 MCP server.

### Portability
- v0.2: SKILL.md files (zero dependencies) + Python Agent SDK entrypoint (`pyproject.toml`)
- v0.5: optional MCP server layer exposing tools (`memory_read`, `memory_write`, `memory_list`,
  `memory_search`) for agents with MCP client support; backed by the Agent SDK entrypoint.

## Process

1. Full project analysis
2. Requirements clarification (questions session — complete)
3. Develop project plan on GitHub (issues + milestones)
4. Break work into phases mapped to GitHub issues
5. Multi-agent code review covering:
   - Unit tests + code coverage
   - Architecture analysis
   - Security analysis and testing
   - Integration testing
6. Leverage Claude, Codex, and Gemini in parallel

## References

- [How to build a memory palace](https://artofmemory.com/blog/how-to-build-a-memory-palace/)
- [Method of Loci](https://en.wikipedia.org/wiki/Method_of_loci)
- [How to build a memory palace to store and revisit information](https://psyche.co/guides/how-to-build-a-memory-palace-to-store-and-revisit-information)
