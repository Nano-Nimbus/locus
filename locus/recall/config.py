"""Root discovery and index placement for ``locus recall``.

Roots come from, in priority order:

1. repeated ``--root DIR`` flags
2. ``[recall] roots = [...]`` in a ``.locus.toml`` found in the working
   directory or one of its parents (entries are relative to that file)
3. the ``LOCUS_PALACE`` environment variable

The index never lives inside a root.  It goes to
``${XDG_CACHE_HOME:-~/.cache}/locus/<hash-of-roots>.sqlite`` so that the
same set of roots always maps to the same derived, disposable file.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tomllib
from pathlib import Path

CONFIG_FILENAME = ".locus.toml"

log = logging.getLogger("locus.recall.config")


class RecallError(Exception):
    """A user-facing error: the CLI prints it to stderr and exits 1."""


def resolve_roots(
    explicit: list[str] | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    sections: tuple[str, ...] = ("recall",),
) -> list[Path]:
    """Return the resolved, de-duplicated root list or raise :class:`RecallError`.

    ``sections`` names the ``.locus.toml`` tables to read ``roots`` from, in
    order.  ``locus lint`` and ``locus index`` pass ``("lint", "recall")`` so a
    palace configured once for recall does not have to be configured again.
    """
    environ = os.environ if env is None else env
    if explicit:
        roots = [Path(r).expanduser() for r in explicit]
        source = "--root"
    else:
        config_file = find_config(cwd or Path.cwd())
        if config_file is not None:
            roots = load_config_roots(config_file, sections)
            source = str(config_file)
        elif environ.get("LOCUS_PALACE"):
            roots = [Path(environ["LOCUS_PALACE"]).expanduser()]
            source = "LOCUS_PALACE"
        else:
            raise RecallError(
                "No roots configured. Pass --root DIR (repeatable), add "
                f"[{sections[0]}] roots = [...] to a {CONFIG_FILENAME}, or set LOCUS_PALACE."
            )

    resolved: list[Path] = []
    for root in roots:
        root = root.resolve()
        if not root.is_dir():
            raise RecallError(f"Root from {source} is not a directory: {root}")
        if root not in resolved:
            resolved.append(root)
    return dedupe_roots(resolved)


def dedupe_roots(roots: list[Path]) -> list[Path]:
    """Resolve, de-duplicate, and drop any root contained in another.

    A nested root indexes the same physical file twice under two
    ``(root, path)`` keys, so both copies come back as separate hits, each
    consuming one of ``k`` and one slice of ``--budget``.
    """
    resolved: list[Path] = []
    for root in roots:
        resolved_root = Path(root).expanduser().resolve()
        if resolved_root not in resolved:
            resolved.append(resolved_root)
    kept: list[Path] = []
    for root in resolved:
        parent = next((r for r in resolved if r != root and root.is_relative_to(r)), None)
        if parent is not None:
            log.warning("ignoring root %s: already covered by %s", root, parent)
            continue
        kept.append(root)
    return kept


def find_config(start: Path) -> Path | None:
    """Walk up from ``start`` and return the first ``.locus.toml`` found."""
    for directory in (start.resolve(), *start.resolve().parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config_file(config_file: Path) -> dict:
    """Parse ``.locus.toml`` or raise :class:`RecallError`."""
    try:
        return tomllib.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RecallError(f"Cannot read {config_file}: {exc}") from exc


def load_config_roots(config_file: Path, sections: tuple[str, ...] = ("recall",)) -> list[Path]:
    """Return the ``roots`` list from the first of ``sections`` that declares one.

    A section that exists but declares no ``roots`` is skipped, so a
    ``[lint]`` table holding only ``types`` still falls through to ``[recall]``.
    A section that declares an invalid ``roots`` is an error, not a fallthrough.
    """
    data = load_config_file(config_file)
    for section in sections:
        table = data.get(section)
        if not isinstance(table, dict) or "roots" not in table:
            continue
        roots = table["roots"]
        if not isinstance(roots, list) or not roots or not all(isinstance(r, str) for r in roots):
            raise RecallError(
                f"{config_file}: [{section}] roots must be a non-empty list of strings"
            )
        base = config_file.parent
        return [(base / Path(r).expanduser()) for r in roots]
    raise RecallError(f"{config_file} has no [{sections[0]}] table declaring roots")


def default_index_path(roots: list[Path], env: dict[str, str] | None = None) -> Path:
    """Cache location for the index over ``roots`` (order-insensitive)."""
    environ = os.environ if env is None else env
    default_home = Path.home() / ".cache"
    raw = environ.get("XDG_CACHE_HOME") or ""
    cache_home = Path(raw).expanduser() if raw else default_home
    # The XDG spec says a relative XDG_CACHE_HOME must be ignored. Honouring
    # one also made the index path depend on the working directory, so the
    # same roots mapped to different index files from different shells.
    if not cache_home.is_absolute():
        cache_home = default_home
    digest = hashlib.sha256(
        "\n".join(sorted(str(r) for r in roots)).encode("utf-8")
    ).hexdigest()[:16]
    candidate = (cache_home / "locus" / f"{digest}.sqlite").resolve()
    # An XDG_CACHE_HOME pointing into an indexed root would drop a binary file
    # into what is usually a git-tracked memory repo.
    if any(candidate.is_relative_to(Path(r).resolve()) for r in roots):
        candidate = (default_home / "locus" / f"{digest}.sqlite").resolve()
    return candidate
