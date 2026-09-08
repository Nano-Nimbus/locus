"""Palace root resolution and path-safety utilities."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from locus.utils import slug_from_path

log = logging.getLogger("locus.mcp.palace")

# Directories that MCP clients may not write to.
# ".sig" contains signature sidecars — never written by MCP clients directly.
_WRITE_BLOCKED_DIRS = {"_metrics", "sessions", "archived", ".sig", ".security"}

# Extensions permitted for memory_write.
_WRITABLE_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml"}


# Backward-compatible alias — callers inside this package use the private name.
_slug_from_path = slug_from_path


def find_auto_memory(cwd: Path | None = None) -> Path | None:
    """Return ``~/.claude/projects/<slug>/memory/`` if it exists, else ``None``.

    Derives the Claude Code project slug from ``cwd`` (defaults to
    ``Path.cwd()``).  This bridges the Claude Code auto-memory system into
    the MCP server without any explicit configuration.
    """
    actual_cwd = (cwd or Path.cwd()).resolve()
    slug = _slug_from_path(actual_cwd)
    candidate = Path.home() / ".claude" / "projects" / slug / "memory"
    if candidate.is_dir():
        return candidate.resolve()
    return None


def find_palace(palace_arg: str | None = None) -> Path:
    """Resolve the palace root.

    Priority:
    1. Explicit ``--palace`` argument
    2. ``LOCUS_PALACE`` environment variable
    3. ``.locus/`` in the current working directory
    4. ``~/.claude/projects/<slug>/memory/`` (Claude Code auto-memory bridge)
    5. ``~/.locus/`` global palace

    Steps 1 to 3 and 5 name a directory that is meant to *be* a palace, so
    each of them is bootstrapped when it has no ``INDEX.md`` yet (see
    :func:`_bootstrap_if_needed`).  Step 4 is left untouched: a Claude Code
    memory directory already has ``MEMORY.md`` as its entry point and is
    owned by Claude Code, not by Locus.
    """
    if palace_arg:
        p = Path(palace_arg).expanduser().resolve()
        if not p.is_dir():
            raise ValueError(f"Palace path does not exist: {p}")
        log.debug("palace resolved from --palace arg: %s", p)
        _bootstrap_if_needed(p)
        return p

    env = os.environ.get("LOCUS_PALACE")
    if env:
        p = Path(env).expanduser().resolve()
        if not p.is_dir():
            raise ValueError(f"LOCUS_PALACE does not exist: {p}")
        log.debug("palace resolved from LOCUS_PALACE env: %s", p)
        _bootstrap_if_needed(p)
        return p

    cwd_locus = Path.cwd() / ".locus"
    if cwd_locus.is_dir():
        log.debug("palace resolved from .locus/ in cwd: %s", cwd_locus.resolve())
        _bootstrap_if_needed(cwd_locus.resolve())
        return cwd_locus.resolve()

    auto_mem = find_auto_memory()
    if auto_mem is not None:
        log.info("palace resolved from auto-memory: %s", auto_mem)
        return auto_mem

    home_locus = Path.home() / ".locus"
    if home_locus.is_dir():
        log.debug("palace resolved from ~/.locus: %s", home_locus.resolve())
    else:
        _bootstrap_palace(home_locus)
        log.info("palace bootstrapped at %s", home_locus)
    _ensure_index(home_locus)
    return home_locus.resolve()


_INDEX_TEMPLATE = """\
# Memory Palace

Personal memory palace — cross-project facts, tooling notes, and session knowledge.

## Global Rooms

| Room | Description | Path |
|---|---|---|

## Project Rooms

| Room | Description | Path |
|---|---|---|

---
_Last consolidated: {date}_

<!--
NAVIGATION: Read this file first. Identify the relevant room(s), then read
only those. Do not load the full palace. If no room matches your query,
check whether a new room is warranted before writing to an existing one.

SIZE LIMIT: Keep this file under 50 lines.
-->
"""


def _bootstrap_palace(palace: Path) -> None:
    """Create a minimal palace directory structure."""
    for subdir in ("global", "projects"):
        (palace / subdir).mkdir(parents=True, exist_ok=True)
    log.info("created palace directories at %s", palace)


def _ensure_index(palace: Path) -> None:
    """Write INDEX.md if it doesn't exist yet."""
    index = palace / "INDEX.md"
    if not index.exists():
        from datetime import date
        index.write_text(
            _INDEX_TEMPLATE.format(date=date.today().isoformat()),
            encoding="utf-8",
        )
        log.info("created %s", index)


def _is_signed(palace: Path) -> bool:
    """True if the palace holds any Locus signature sidecar."""
    try:
        return any(palace.glob("**/.sig/*.sig"))
    except OSError:
        return False


def _bootstrap_if_needed(palace: Path) -> None:
    """Give an existing but index-less palace directory a skeleton.

    ``memory_list`` without a path returns ``INDEX.md``, so a palace that
    starts without one answers every first call with "No INDEX.md found".
    Container deployments always pass ``--palace`` explicitly, which used to
    skip bootstrapping entirely; this closes that gap.

    - An empty directory gets the full skeleton (``global/``, ``projects/``,
      ``INDEX.md``), exactly like the ``~/.locus`` fallback.
    - A non-empty directory without ``INDEX.md`` gets only ``INDEX.md``; its
      existing layout is left alone.
    - A directory that already has ``INDEX.md`` is not touched.

    A read-only root (for example a mounted checkout) is logged and skipped
    rather than failing startup; ``memory_list`` then reports the missing
    index as before.

    A signed palace is also left alone.  An unsigned ``INDEX.md`` dropped into
    one turns a clean ``locus-security verify-all`` into a failure, and with
    ``verify_on_read`` enabled the server would refuse to serve the very file
    it just wrote.  A palace that keeps signatures has a deliberate layout, so
    resolving it must not change it.
    """
    if (palace / "INDEX.md").is_file():
        return
    if _is_signed(palace):
        log.info(
            "palace at %s carries signatures; not creating INDEX.md "
            "(an unsigned index would fail verify-all)",
            palace,
        )
        return
    try:
        if not any(palace.iterdir()):
            _bootstrap_palace(palace)
        _ensure_index(palace)
    except OSError as exc:
        log.warning("could not bootstrap palace at %s: %s", palace, exc)


def safe_resolve(palace_root: Path, rel_path: str) -> Path:
    """Resolve ``rel_path`` within ``palace_root``, rejecting traversal.

    Raises ``ValueError`` if the resolved path escapes the palace root.
    """
    # Normalise: strip leading slashes so absolute paths don't escape.
    clean = rel_path.lstrip("/")
    resolved = (palace_root / clean).resolve()
    try:
        resolved.relative_to(palace_root)
    except ValueError:
        raise ValueError(
            f"Path '{rel_path}' escapes the palace root '{palace_root}'"
        )
    return resolved


def assert_writable(palace_root: Path, target: Path) -> None:
    """Raise ``ValueError`` if ``target`` is in a write-blocked directory or
    has a non-writable extension."""
    # Check every path segment — blocked dirs are forbidden at any depth.
    rel = target.relative_to(palace_root)
    for part in rel.parts[:-1]:  # exclude the filename itself
        if part in _WRITE_BLOCKED_DIRS:
            raise ValueError(
                f"Writes to '{part}/' are not permitted via MCP. "
                "Use the locus-feedback or locus-audit tools instead."
            )
    if target.suffix not in _WRITABLE_EXTENSIONS:
        raise ValueError(
            f"Extension '{target.suffix}' is not writable via MCP. "
            f"Allowed: {', '.join(sorted(_WRITABLE_EXTENSIONS))}"
        )
