"""Shared document model for ``locus lint`` and ``locus index``.

Both commands walk the same markdown roots and read the same frontmatter, so
the walking, the reserved-name rules, and the title/description extraction all
live here.  The frontmatter parser is the dependency-free one from
:mod:`locus.recall.frontmatter`, which means lint and index see exactly the
frontmatter that recall indexes: a file that lints clean ranks the way the
lint output says it will.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from locus.recall.frontmatter import split_frontmatter

# OKF reserves these two filenames: they are directory apparatus, never
# concept documents, so the "every file needs a type" rule skips them.
OKF_RESERVED = frozenset({"index.md", "log.md"})
# Locus and Claude Code index files. Same exemption, different producer.
LOCUS_RESERVED = frozenset({"INDEX.md", "MEMORY.md"})
RESERVED = OKF_RESERVED | LOCUS_RESERVED

# Mirrors locus.recall.index._SKIP_DIR_NAMES so lint never reports on a file
# recall would not index in the first place.
SKIP_DIR_NAMES = frozenset({"node_modules", "__pycache__", "_metrics", ".venv"})

# Directories whose contents are episodic: append-only session and journal
# records, exempt from the size limits that apply to canonical files.
EPISODIC_DIR_NAMES = frozenset({"sessions", "journal"})

_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_OPENER_RE = re.compile(r"\A---[ \t]*\r?\n")
_ISO_DATE_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")


@dataclass(frozen=True)
class Violation:
    """One rule failure against one path.

    ``fix`` is a human-readable description of what ``--fix`` would write, or
    ``None`` when nothing can be inferred.  Reporting it separately from the
    message keeps "this is wrong" and "this is what I would do about it" apart
    in both the text and the JSON output.
    """

    path: Path
    rule: str
    message: str
    severity: str = "error"
    fix: str | None = None

    @property
    def fixable(self) -> bool:
        return self.fix is not None

    def to_dict(self, base: Path | None = None) -> dict[str, Any]:
        return {
            "path": display_path(self.path, base),
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "fixable": self.fixable,
            "fix": self.fix,
        }


@dataclass
class Doc:
    """One markdown file, read once and shared by every rule."""

    root: Path
    file: Path
    text: str
    frontmatter: dict[str, Any]
    body: str
    frontmatter_state: str  # "none", "ok", or "unterminated"

    @property
    def rel(self) -> str:
        return self.file.relative_to(self.root).as_posix()

    @property
    def name(self) -> str:
        return self.file.name

    @property
    def reserved(self) -> bool:
        return self.name in RESERVED

    @property
    def episodic(self) -> bool:
        parts = self.file.relative_to(self.root).parts[:-1]
        return any(part in EPISODIC_DIR_NAMES for part in parts)

    @property
    def line_count(self) -> int:
        return len(self.text.splitlines())

    @property
    def okf_type(self) -> str:
        return text_of(self.frontmatter.get("type"))

    @property
    def category(self) -> str:
        """The document's grouping label: OKF ``type`` first, then the Claude
        Code category under ``memory_type`` or ``metadata.type``."""
        metadata = self.frontmatter.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        return (
            self.okf_type
            or text_of(self.frontmatter.get("memory_type"))
            or text_of(metadata.get("type"))
        )

    @property
    def title(self) -> str:
        for key in ("title", "name"):
            value = text_of(self.frontmatter.get(key))
            if value:
                return value
        return first_heading(self.body) or humanize(self.file.stem)

    @property
    def description(self) -> str:
        value = text_of(self.frontmatter.get("description"))
        return collapse(value) if value else first_paragraph(self.body)


def load_doc(root: Path, file: Path) -> Doc:
    """Read one file into a :class:`Doc`; unreadable bytes become an empty doc."""
    try:
        text = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return Doc(root, file, "", {}, "", "unreadable")
    stripped = text.lstrip("﻿")
    frontmatter, body = split_frontmatter(text)
    if frontmatter or _closes_frontmatter(stripped):
        state = "ok"
    elif _OPENER_RE.match(stripped):
        state = "unterminated"
    else:
        state = "none"
    return Doc(root, file, text, frontmatter, body, state)


def _closes_frontmatter(text: str) -> bool:
    """True when the text opens a frontmatter block and also closes it.

    An empty but well-formed block (``---\\n---``) parses to ``{}``, which is
    indistinguishable from "no frontmatter at all" by the parsed value alone.
    """
    if not _OPENER_RE.match(text):
        return False
    for line in text.splitlines()[1:]:
        if line.rstrip() == "---":
            return True
    return False


def iter_markdown(root: Path):
    """Yield every ``*.md`` under ``root`` in a stable order.

    Dot-directories and tooling directories are skipped, matching what
    ``locus recall`` indexes.  Symlinked directories are followed with a cycle
    guard, because symlinking a memory directory into a palace is normal.
    """
    seen: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        try:
            real = os.path.realpath(dirpath)
        except OSError:
            dirnames[:] = []
            continue
        if real in seen:
            dirnames[:] = []
            continue
        seen.add(real)
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d not in SKIP_DIR_NAMES
        )
        for name in sorted(filenames):
            if name.endswith(".md"):
                yield Path(dirpath) / name


def iter_dirs(root: Path):
    """Yield ``root`` and every indexable directory beneath it, stable order."""
    seen: set[str] = set()
    for dirpath, dirnames, _ in os.walk(root, followlinks=True):
        try:
            real = os.path.realpath(dirpath)
        except OSError:
            dirnames[:] = []
            continue
        if real in seen:
            dirnames[:] = []
            continue
        seen.add(real)
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d not in SKIP_DIR_NAMES
        )
        yield Path(dirpath)


def has_file(directory: Path, name: str) -> bool:
    """Case-sensitive test for a file named exactly ``name`` in ``directory``.

    macOS and Windows filesystems are case-insensitive, so ``Path.is_file()``
    answers True for ``INDEX.md`` when only an OKF ``index.md`` exists.  That
    made every OKF bundle classify as a palace on a Mac and generate the wrong
    index file, so every check that distinguishes the two goes through here.
    """
    try:
        return name in {entry.name for entry in directory.iterdir() if entry.is_file()}
    except OSError:
        return False


def root_kind(root: Path) -> str:
    """Classify a root as ``palace``, ``memory``, or ``okf``.

    The kind decides which rules apply and which index files are generated.  A
    palace is recognised by its ``INDEX.md`` or by the ``global/`` plus
    ``projects/`` layout; a Claude Code memory directory by topic files
    carrying both ``name`` and ``description``; everything else is treated as
    an OKF bundle.
    """
    if has_file(root, "INDEX.md"):
        return "palace"
    if (root / "global").is_dir() and (root / "projects").is_dir():
        return "palace"
    if has_file(root, "index.md"):
        # An OKF bundle root says so with index.md. Checking it before the
        # memory heuristic keeps a bundle that happens to carry a MEMORY.md
        # from being rewritten as a memory directory.
        return "okf"
    if looks_like_memory(root):
        return "memory"
    return "okf"


def looks_like_memory(root: Path) -> bool:
    """True when the root's topic files are Claude Code auto-memory files.

    Half of the direct children have to carry both ``name`` and ``description``,
    not just one of them.  A single Claude Code file dropped into an OKF bundle
    is a document in a bundle, not a memory directory, and misreading it as one
    would replace the bundle's ``index.md`` files with a ``MEMORY.md``.
    """
    if has_file(root, "MEMORY.md"):
        return True
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return False
    topics = [
        entry
        for entry in entries
        if entry.is_file() and entry.suffix == ".md" and entry.name not in RESERVED
    ]
    if not topics:
        return False
    memory_shaped = sum(
        1
        for entry in topics
        if (doc := load_doc(root, entry)).frontmatter.get("name")
        and doc.frontmatter.get("description")
    )
    return memory_shaped * 2 >= len(topics)


def palace_file_class(root: Path, file: Path) -> str:
    """Classify a palace file for the size limits in ``spec/size-limits.md``."""
    parts = file.relative_to(root).parts
    if any(part in EPISODIC_DIR_NAMES for part in parts[:-1]):
        return "session"
    if file.name in RESERVED:
        return "index"
    if len(parts) == 1:
        return "specialty"
    if file.stem == file.parent.name or file.name == "README.md":
        return "main"
    return "specialty"


def text_of(value: Any) -> str:
    if value is None or isinstance(value, dict):
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value).strip()


def collapse(value: str) -> str:
    """Flatten to a single line.

    Every index this package writes is one entry per line, which is what makes
    a ``MEMORY.md`` safe to merge with git's union driver.  A description with
    an embedded newline would break that, so it is collapsed here rather than
    at each call site.
    """
    return " ".join(value.split())


def first_heading(body: str) -> str:
    match = _HEADING_RE.search(body)
    return match.group(1).strip() if match else ""


def first_paragraph(body: str) -> str:
    """First prose paragraph: no headings, tables, comments, or list items."""
    lines: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            if lines:
                break
            continue
        if line.startswith(("#", "|", "<!--", ">", "```", "---", "* ", "- ")):
            if lines:
                break
            continue
        lines.append(line)
    return collapse(" ".join(lines))


def humanize(stem: str) -> str:
    words = collapse(stem.replace("-", " ").replace("_", " "))
    return words[:1].upper() + words[1:] if words else stem


def is_iso_date(value: str) -> bool:
    return bool(_ISO_DATE_RE.match(value))


def is_iso_timestamp(value: str) -> bool:
    """True for an ISO 8601 date or datetime, the only forms OKF timestamps take."""
    if not value:
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def display_path(path: Path, base: Path | None) -> str:
    if base is not None:
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            pass
    return str(path)
