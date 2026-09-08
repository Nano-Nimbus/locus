"""Configuration for ``locus lint`` and ``locus index``.

Roots come from the same place ``locus recall`` gets them, with ``[lint]``
consulted before ``[recall]`` so one ``.locus.toml`` can serve all three
commands.  Everything else lint needs, the directory-to-type map and the
archive globs, lives in ``[lint]``:

.. code-block:: toml

    [lint]
    roots = ["docs"]
    archive_globs = ["archive/*", "*/superseded/*"]

    [lint.types]
    "." = "Reference"
    runbooks = "Runbook"
    analysis = "Analysis"
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

from locus.recall.config import (
    CONFIG_FILENAME,
    RecallError,
    find_config,
    load_config_file,
)

SECTIONS = ("lint", "recall")


class ConformError(RecallError):
    """A user-facing error: the CLI prints it to stderr and exits 1."""


@dataclass
class ConformConfig:
    """Resolved lint settings, after merging the config file and the CLI flags."""

    types: dict[str, str] = field(default_factory=dict)
    archive_globs: list[str] = field(default_factory=list)
    strict: bool = False

    def type_for(self, root: Path, file: Path) -> str:
        """The ``type`` to infer for ``file``, or ``""`` when nothing matches.

        Directories are tried deepest first, each by its root-relative path and
        then by its bare name, so ``runbooks = "Runbook"`` works at any depth
        while ``"docs/runbooks" = "Runbook"`` pins one directory.  ``"."`` is
        the whole-root default.
        """
        if not self.types:
            return ""
        relative = file.relative_to(root).parent
        candidates: list[str] = []
        for directory in (relative, *relative.parents):
            as_posix = directory.as_posix()
            candidates.append(as_posix)
            if directory.name:
                candidates.append(directory.name)
        for candidate in candidates:
            if candidate in self.types:
                return self.types[candidate]
        return ""

    def is_archived(self, root: Path, file: Path) -> bool:
        """True when the root-relative path matches any archive glob.

        ``fnmatch`` wildcards cross ``/``, so ``archive/*`` covers the whole
        subtree and no ``**`` spelling is needed.
        """
        relative = file.relative_to(root).as_posix()
        return any(fnmatch.fnmatchcase(relative, glob) for glob in self.archive_globs)


def parse_type_map(entries: list[str] | None) -> dict[str, str]:
    """Parse repeated ``--type-map DIR=TYPE`` flags."""
    result: dict[str, str] = {}
    for entry in entries or []:
        key, separator, value = entry.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not key or not value:
            raise ConformError(f"--type-map expects DIR=TYPE, got: {entry}")
        result[key.rstrip("/") or "."] = value
    return result


def load_config(
    cwd: Path | None = None,
    type_map: list[str] | None = None,
    archive_globs: list[str] | None = None,
    strict: bool = False,
) -> ConformConfig:
    """Merge ``[lint]`` from the nearest ``.locus.toml`` with the CLI flags.

    CLI flags win over the file for a given directory key, and archive globs
    from both sources are combined rather than replaced.
    """
    config = ConformConfig(strict=strict)
    config_file = find_config(cwd or Path.cwd())
    if config_file is not None:
        data = load_config_file(config_file)
        lint = data.get("lint")
        if isinstance(lint, dict):
            types = lint.get("types", {})
            if not isinstance(types, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in types.items()
            ):
                raise ConformError(
                    f"{config_file}: [lint.types] must map directory names to type strings"
                )
            config.types.update({k.rstrip("/") or ".": v for k, v in types.items()})
            globs = lint.get("archive_globs", [])
            if not isinstance(globs, list) or not all(isinstance(g, str) for g in globs):
                raise ConformError(
                    f"{config_file}: [lint] archive_globs must be a list of strings"
                )
            config.archive_globs.extend(globs)
    config.types.update(parse_type_map(type_map))
    config.archive_globs.extend(archive_globs or [])
    return config


__all__ = [
    "CONFIG_FILENAME",
    "SECTIONS",
    "ConformConfig",
    "ConformError",
    "load_config",
    "parse_type_map",
]
