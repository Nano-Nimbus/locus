"""``locus recall``: ranked, stale-aware lookup over markdown memory roots.

Importable API::

    from locus.recall import recall
    hits = recall("flux kustomization stall", roots=[Path("~/memory").expanduser()])

See :mod:`locus.recall.index` for the index and ranking, and
:mod:`locus.recall.output` for the text/JSON renderers.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

from .config import RecallError, default_index_path, find_config, resolve_roots
from .index import Hit, RecallIndex, RefreshStats, fts5_available, require_fts5
from .output import format_json, format_text

log = logging.getLogger("locus.recall")


def recall(
    query: str,
    roots: list[Path],
    k: int = 3,
    include_journal: bool = False,
    types: list[str] | None = None,
    refresh: bool = False,
    index_path: Path | None = None,
    scope: str | None = None,
) -> list[Hit]:
    """Refresh the index over ``roots`` and return the top ``k`` hits.

    A refresh that cannot get the write lock is a warning, not a failure. Two
    processes share one index by design, a per-prompt hook and the MCP
    server's ``memory_search``, so one of them can lose the race and time out.
    Slightly stale results beat no results and a traceback, so the search runs
    against the index as it stands.
    """
    with RecallIndex(roots, index_path=index_path) as index:
        try:
            index.refresh(force=refresh)
        except sqlite3.OperationalError as exc:
            log.warning("index refresh skipped (%s); searching the existing index", exc)
            print(
                f"locus recall: index busy ({exc}); searching without refreshing",
                file=sys.stderr,
            )
        return index.search(
            query, k=k, include_journal=include_journal, types=types, scope=scope
        )


__all__ = [
    "Hit",
    "RecallError",
    "RecallIndex",
    "RefreshStats",
    "default_index_path",
    "find_config",
    "format_json",
    "format_text",
    "fts5_available",
    "recall",
    "require_fts5",
    "resolve_roots",
]
