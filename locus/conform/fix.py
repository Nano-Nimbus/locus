"""``locus lint --fix``: add inferable frontmatter, never rewrite what is there.

Three fields can be inferred without guessing at meaning:

``type``
    From ``--type-map DIR=TYPE`` or ``[lint.types]``.  The author declared the
    mapping, so the value is theirs, not the linter's.
``generated.at``
    The date of the file's first git commit, when the file is inside a git
    repository.  Only filled into an existing ``generated`` block: a block
    with no ``by`` cannot be invented, because nothing in the file says who
    produced it.
``status: deprecated``
    For paths matching a configured archive glob, and only when ``status`` is
    absent entirely.

Edits are textual, not a YAML round trip.  A parse-and-dump would reorder
keys, restyle flow mappings, requote strings, and drop comments across every
file it touched; inserting lines leaves every byte the author wrote exactly
where they put it.  That also makes the operation idempotent by construction:
a key is only ever inserted when it is absent, so a second ``--fix`` run is a
no-op and produces identical bytes.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .config import ConformConfig
from .model import Doc, load_doc, text_of

_CLOSER_RE = re.compile(r"\A---[ \t]*\r?\Z")
_GENERATED_RE = re.compile(r"\A(generated:)([ \t]*)(.*)\Z")


class GitDates:
    """First-commit dates, resolved lazily and cached per file.

    ``git log`` is one subprocess per file, so the cache matters on a bundle
    with hundreds of documents even though ``--fix`` is not a hot path.
    """

    def __init__(self) -> None:
        self._repo_roots: dict[Path, Path | None] = {}
        self._dates: dict[Path, str | None] = {}

    def first_commit(self, file: Path) -> str | None:
        """ISO 8601 author date of the commit that added ``file``, or ``None``."""
        if file in self._dates:
            return self._dates[file]
        repo = self._repo_root(file.parent)
        date: str | None = None
        if repo is not None:
            output = _git(repo, "log", "--format=%aI", "--", str(file))
            if output:
                # git logs newest first; the file was added by the last one.
                date = output.splitlines()[-1].strip() or None
        self._dates[file] = date
        return date

    def _repo_root(self, directory: Path) -> Path | None:
        if directory in self._repo_roots:
            return self._repo_roots[directory]
        output = _git(directory, "rev-parse", "--show-toplevel")
        root = Path(output.strip()) if output and output.strip() else None
        self._repo_roots[directory] = root
        return root


def _git(cwd: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return None
    return result.stdout if result.returncode == 0 else None


def fix_text(doc: Doc, config: ConformConfig, git_dates: GitDates) -> str | None:
    """Return the repaired text for ``doc``, or ``None`` when nothing changes."""
    if doc.name in {"index.md", "log.md", "INDEX.md", "MEMORY.md"}:
        return None
    if doc.frontmatter_state in {"unterminated", "unreadable"}:
        return None

    text = doc.text
    if not doc.okf_type:
        inferred = config.type_for(doc.root, doc.file)
        if inferred:
            text = insert_key(text, "type", inferred)
    if config.is_archived(doc.root, doc.file) and "status" not in doc.frontmatter:
        text = insert_key(text, "status", "deprecated")

    generated = doc.frontmatter.get("generated")
    if isinstance(generated, dict) and not text_of(generated.get("at")):
        date = git_dates.first_commit(doc.file)
        if date:
            updated = insert_generated_at(text, date)
            if updated is not None:
                text = updated

    return text if text != doc.text else None


def fix_root(root: Path, config: ConformConfig, git_dates: GitDates | None = None) -> list[Path]:
    """Apply every inferable fix under ``root``; return the paths rewritten."""
    from .model import iter_markdown

    dates = git_dates or GitDates()
    changed: list[Path] = []
    for file in iter_markdown(root):
        doc = load_doc(root, file)
        updated = fix_text(doc, config, dates)
        if updated is None:
            continue
        file.write_text(updated, encoding="utf-8")
        changed.append(file)
    return changed


def insert_key(text: str, key: str, value: str) -> str:
    """Add a top-level frontmatter key, creating the block when there is none.

    The key goes immediately before the closing ``---`` so existing keys keep
    their order and any comments between them survive.  The file's own line
    ending, LF or CRLF, is detected once and reused: a hardcoded ``\\n`` would
    leave the inserted line as the one line in a CRLF file that is not.
    """
    crlf = "\r\n" in text
    eol = "\r" if crlf else ""
    lines = text.split("\n")
    if lines and _CLOSER_RE.match(lines[0].lstrip("﻿")):
        for position in range(1, len(lines)):
            if _CLOSER_RE.match(lines[position]):
                lines.insert(position, f"{key}: {value}{eol}")
                return "\n".join(lines)
    newline = "\r\n" if crlf else "\n"
    prefix = f"---{newline}{key}: {value}{newline}---{newline}"
    return prefix + (newline + text if text and not text.startswith(("\n", "\r")) else text)


def insert_generated_at(text: str, value: str) -> str | None:
    """Add ``at`` inside an existing ``generated`` mapping, block or flow style.

    Returns ``None`` when ``generated`` is not a mapping this can extend, in
    which case the violation is reported and left for a human.
    """
    eol = "\r" if "\r\n" in text else ""
    lines = text.split("\n")
    if not lines or not _CLOSER_RE.match(lines[0].lstrip("﻿")):
        return None
    end = next((i for i in range(1, len(lines)) if _CLOSER_RE.match(lines[i])), None)
    if end is None:
        return None

    for position in range(1, end):
        match = _GENERATED_RE.match(lines[position])
        if match is None:
            continue
        remainder = match.group(3).strip()
        if remainder.startswith("{") and remainder.endswith("}"):
            # Keep the author's padding: "{ by: x }" stays spaced, "{by: x}" tight.
            inner = remainder[1:-1]
            padding = " " if inner.endswith(" ") else ""
            body = inner.rstrip()
            separator = ", " if body.strip() else ""
            lines[position] = f"generated: {{{body}{separator}at: {value}{padding}}}{eol}"
            return "\n".join(lines)
        if remainder:
            return None  # a scalar; nothing to extend
        # Block mapping: append after its last child, at the child indent.
        indent = None
        last = position
        for child in range(position + 1, end):
            leading = len(lines[child]) - len(lines[child].lstrip(" "))
            if not lines[child].strip():
                continue
            if leading == 0:
                break
            indent = leading if indent is None else indent
            last = child
        if indent is None:
            return None  # ``generated:`` with no children is not a mapping
        lines.insert(last + 1, f"{' ' * indent}at: {value}{eol}")
        return "\n".join(lines)
    return None


__all__ = ["GitDates", "fix_root", "fix_text", "insert_generated_at", "insert_key"]
