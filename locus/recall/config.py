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
import os
import tomllib
from pathlib import Path

CONFIG_FILENAME = ".locus.toml"


class RecallError(Exception):
    """A user-facing error: the CLI prints it to stderr and exits 1."""


def resolve_roots(
    explicit: list[str] | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> list[Path]:
    """Return the resolved, de-duplicated root list or raise :class:`RecallError`."""
    environ = os.environ if env is None else env
    if explicit:
        roots = [Path(r).expanduser() for r in explicit]
        source = "--root"
    else:
        config_file = find_config(cwd or Path.cwd())
        if config_file is not None:
            roots = load_config_roots(config_file)
            source = str(config_file)
        elif environ.get("LOCUS_PALACE"):
            roots = [Path(environ["LOCUS_PALACE"]).expanduser()]
            source = "LOCUS_PALACE"
        else:
            raise RecallError(
                "No roots configured. Pass --root DIR (repeatable), add "
                f"[recall] roots = [...] to a {CONFIG_FILENAME}, or set LOCUS_PALACE."
            )

    resolved: list[Path] = []
    for root in roots:
        root = root.resolve()
        if not root.is_dir():
            raise RecallError(f"Root from {source} is not a directory: {root}")
        if root not in resolved:
            resolved.append(root)
    return resolved


def find_config(start: Path) -> Path | None:
    """Walk up from ``start`` and return the first ``.locus.toml`` found."""
    for directory in (start.resolve(), *start.resolve().parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config_roots(config_file: Path) -> list[Path]:
    try:
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RecallError(f"Cannot read {config_file}: {exc}") from exc
    recall = data.get("recall")
    if not isinstance(recall, dict):
        raise RecallError(f"{config_file} has no [recall] table")
    roots = recall.get("roots")
    if not isinstance(roots, list) or not roots or not all(isinstance(r, str) for r in roots):
        raise RecallError(f"{config_file}: [recall] roots must be a non-empty list of strings")
    base = config_file.parent
    return [(base / Path(r).expanduser()) for r in roots]


def default_index_path(roots: list[Path], env: dict[str, str] | None = None) -> Path:
    """Cache location for the index over ``roots`` (order-insensitive)."""
    environ = os.environ if env is None else env
    cache_home = environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    digest = hashlib.sha256(
        "\n".join(sorted(str(r) for r in roots)).encode("utf-8")
    ).hexdigest()[:16]
    return Path(cache_home) / "locus" / f"{digest}.sqlite"
