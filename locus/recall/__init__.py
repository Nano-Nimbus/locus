"""``locus recall``: ranked, stale-aware lookup over markdown memory roots.

Importable API::

    from locus.recall import recall
    hits = recall("flux kustomization stall", roots=[Path("~/memory").expanduser()])

See :mod:`locus.recall.index` for the index and ranking, and
:mod:`locus.recall.output` for the text/JSON renderers.
"""

from __future__ import annotations

from pathlib import Path

from .config import RecallError, default_index_path, find_config, resolve_roots
from .index import Hit, RecallIndex, RefreshStats, fts5_available, require_fts5
from .output import format_json, format_text


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
    """Refresh the index over ``roots`` and return the top ``k`` hits."""
    with RecallIndex(roots, index_path=index_path) as index:
        index.refresh(force=refresh)
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
