"""``locus index``: generate the three index shapes Locus and OKF define.

``index.md`` (OKF section 8)
    One per directory, listing that directory's documents and sub-directories
    as ``* [Title](path) - description``.  The bundle-root copy is the only
    index file in OKF that carries frontmatter, and the only key allowed there
    is ``okf_version``.

``INDEX.md`` (``spec/index-format.md``)
    The palace routing table, one row per room, inside the 50-line budget from
    ``spec/size-limits.md``.  Over budget is an error rather than a truncated
    file: the spec's answer is sub-indices, and silently dropping rooms would
    make the palace unnavigable in exactly the case where navigation matters.

``MEMORY.md``
    The Claude Code auto-memory index, one line per topic file.  Claude Code
    requires that filename, so it is not an OKF ``index.md``: no frontmatter,
    and ``- [Title](file.md) - description`` entries.

Every renderer is a pure function of the files on disk, so ``--check`` is a
byte comparison.  Three things would otherwise make output vary between runs
on unchanged input, and each is pinned: entries are sorted rather than left in
directory order, the palace ``_Last consolidated:`` date is preserved from the
existing INDEX.md rather than recomputed, and the prose an author wrote around
the generated tables (the palace title, its scope sentence, and any trailing
HTML comment) is carried across instead of being regenerated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .model import (
    LOCUS_RESERVED,
    RESERVED,
    Doc,
    collapse,
    first_heading,
    has_file,
    humanize,
    iter_dirs,
    iter_markdown,
    load_doc,
    root_kind,
)

OKF_VERSION = "0.2"
INDEX_LINE_LIMIT = 50
# Room descriptions share a table row and MEMORY.md descriptions share a line;
# both stay readable only if a stray essay in a description is cut short.
ROOM_DESCRIPTION_LIMIT = 120
MEMORY_DESCRIPTION_LIMIT = 200


class IndexError_(Exception):
    """A generated index cannot be written as specified."""


@dataclass(frozen=True)
class Generated:
    """One index file the generator wants on disk."""

    path: Path
    content: str
    status: str  # "ok", "drift", or "missing"

    @property
    def needs_write(self) -> bool:
        return self.status != "ok"


def generate_root(root: Path, kind: str | None = None) -> list[Generated]:
    """Every index file ``root`` should carry, with its current drift status."""
    resolved = kind or root_kind(root)
    if resolved == "palace":
        planned = _planned_palace_index(root)
    elif resolved == "memory":
        planned = _planned_memory_index(root)
    else:
        planned = render_okf_indexes(root)
    return [_status(path, content) for path, content in sorted(planned.items())]


def _planned_palace_index(root: Path) -> dict[Path, str]:
    """Nothing at all when the palace has no rooms.

    An empty palace's INDEX.md is whatever a human or the MCP bootstrap wrote.
    Regenerating it from zero rooms would replace that with a placeholder, and
    a generator that empties the file it is meant to maintain is worse than one
    that declines.
    """
    containers = (root / "global", root / "projects", root)
    if not any(_rooms(root, container) for container in containers):
        return {}
    return {root / "INDEX.md": render_palace_index(root)}


def _planned_memory_index(root: Path) -> dict[Path, str]:
    """Nothing at all when the root has no topic files to index."""
    topics = [f for f in iter_markdown(root) if f.name not in RESERVED]
    return {root / "MEMORY.md": render_memory_index(root)} if topics else {}


def _status(path: Path, content: str) -> Generated:
    if not has_file(path.parent, path.name):
        return Generated(path, content, "missing")
    try:
        current = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return Generated(path, content, "drift")
    return Generated(path, content, "ok" if current == content else "drift")


def write(generated: list[Generated]) -> list[Generated]:
    """Write every index that drifted; return the ones actually written."""
    written: list[Generated] = []
    for item in generated:
        if not item.needs_write:
            continue
        item.path.parent.mkdir(parents=True, exist_ok=True)
        item.path.write_text(item.content, encoding="utf-8")
        written.append(item)
    return written


# ---------------------------------------------------------------------------
# OKF index.md
# ---------------------------------------------------------------------------

def render_okf_indexes(root: Path) -> dict[Path, str]:
    """An ``index.md`` for every directory that has something to list."""
    result: dict[Path, str] = {}
    for directory in iter_dirs(root):
        entries = _okf_entries(root, directory)
        if not entries:
            continue
        result[directory / "index.md"] = _render_okf_index(
            root, directory, entries, directory == root
        )
    return result


def _okf_entries(root: Path, directory: Path) -> list[tuple[str, str, str]]:
    """``(link, title, description)`` for the directory's files, then sub-dirs."""
    try:
        children = sorted(directory.iterdir(), key=lambda p: p.name)
    except OSError:
        return []

    files: list[tuple[str, str, str]] = []
    subdirs: list[tuple[str, str, str]] = []
    for child in children:
        if child.is_file() and child.suffix == ".md" and child.name not in RESERVED:
            doc = load_doc(root, child)
            files.append((child.name, doc.title, doc.description))
        elif child.is_dir() and not child.name.startswith("."):
            if not _has_content(child):
                continue
            subdirs.append((f"{child.name}/", humanize(child.name), _dir_description(root, child)))
    files.sort(key=lambda entry: (entry[1].casefold(), entry[0]))
    return files + subdirs


def _has_content(directory: Path) -> bool:
    """True when any non-reserved markdown lives anywhere beneath ``directory``."""
    from .model import SKIP_DIR_NAMES

    if directory.name in SKIP_DIR_NAMES:
        return False
    for file in iter_markdown(directory):
        if file.name not in RESERVED:
            return True
    return False


def _dir_description(root: Path, directory: Path) -> str:
    """A sub-directory's description: from its main file, if it has one."""
    for name in (f"{directory.name}.md", "README.md"):
        if has_file(directory, name):
            return load_doc(root, directory / name).description
    return ""


def _render_okf_index(
    root: Path, directory: Path, entries: list[tuple[str, str, str]], bundle_root: bool
) -> str:
    lines: list[str] = []
    if bundle_root:
        lines += ["---", f'okf_version: "{OKF_VERSION}"', "---", ""]
    title = humanize(directory.name) if directory != root else humanize(root.name)
    lines += [f"# {title}", ""]
    for link, entry_title, description in entries:
        suffix = f" - {description}" if description else ""
        lines.append(f"* [{entry_title}]({link}){suffix}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Palace INDEX.md
# ---------------------------------------------------------------------------

def render_palace_index(root: Path) -> str:
    """The palace routing table, preserving the prose around it."""
    existing = _read(root / "INDEX.md")
    title = first_heading(existing) or humanize(root.name)
    scope = _palace_scope(existing) or f"Memory palace for {humanize(root.name)}."
    consolidated = _palace_consolidated(existing)
    trailer = _palace_trailer(existing)

    lines = [f"# {title}", "", scope, ""]
    sections = [
        ("Global Rooms", root / "global", "global", "Rooms shared across all projects."),
        ("Project Rooms", root / "projects", "projects", ""),
    ]
    rendered_any = False
    for heading, container, prefix, blurb in sections:
        rooms = _rooms(root, container)
        if not rooms:
            continue
        rendered_any = True
        lines.append(f"## {heading}")
        lines.append("")
        if blurb:
            lines += [blurb, ""]
        lines += _room_table(rooms, prefix)
        lines.append("")
    if not rendered_any:
        rooms = _rooms(root, root)
        lines += ["## Rooms", ""]
        lines += _room_table(rooms, "") if rooms else ["_No rooms yet._"]
        lines.append("")

    lines += ["---", f"_Last consolidated: {consolidated}_"]
    if trailer:
        lines += ["", trailer]
    content = "\n".join(lines) + "\n"

    length = len(content.splitlines())
    if length > INDEX_LINE_LIMIT:
        raise IndexError_(
            f"{root / 'INDEX.md'} would be {length} lines, over the "
            f"{INDEX_LINE_LIMIT}-line limit. Split the palace into sub-indices "
            "(global/INDEX.md, projects/<name>/INDEX.md) per spec/index-format.md."
        )
    return content


def _rooms(root: Path, container: Path) -> list[tuple[str, str]]:
    """``(name, description)`` for each level-one room under ``container``."""
    if not container.is_dir():
        return []
    rooms: list[tuple[str, str]] = []
    for child in sorted(container.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith(".") or child.name in {"global", "projects"}:
            continue
        if not _has_content(child):
            continue
        description = _first_sentence(_dir_description(root, child))
        rooms.append((child.name, _truncate(description, ROOM_DESCRIPTION_LIMIT)))
    return rooms


def _room_table(rooms: list[tuple[str, str]], prefix: str) -> list[str]:
    lines = ["| Room | Description | Path |", "|---|---|---|"]
    for name, description in rooms:
        path = f"{prefix}/{name}/" if prefix else f"{name}/"
        lines.append(f"| `{name}` | {_escape_cell(description)} | `{path}` |")
    return lines


def _palace_scope(existing: str) -> str:
    """The author's one-sentence scope line, from under the title heading."""
    lines: list[str] = []
    seen_title = False
    for raw in existing.splitlines():
        line = raw.strip()
        if not seen_title:
            seen_title = line.startswith("# ")
            continue
        if line.startswith(("#", "|", "<!--", "---")):
            break
        if not line:
            if lines:
                break
            continue
        lines.append(line)
    return collapse(" ".join(lines))


def _palace_consolidated(existing: str) -> str:
    """Preserve the existing consolidation date; a new palace gets today's."""
    for raw in existing.splitlines():
        line = raw.strip()
        if line.startswith("_Last consolidated:") and line.endswith("_"):
            return line[len("_Last consolidated:") : -1].strip()
    return datetime.now(tz=UTC).date().isoformat()


def _palace_trailer(existing: str) -> str:
    """Preserve a trailing HTML comment, which is where palaces keep navigation notes."""
    start = existing.rfind("<!--")
    if start < 0:
        return ""
    end = existing.find("-->", start)
    return existing[start : end + 3] if end >= 0 else ""


# ---------------------------------------------------------------------------
# Claude Code MEMORY.md
# ---------------------------------------------------------------------------

def render_memory_index(root: Path) -> str:
    """One line per topic file, grouped by category when the files declare one."""
    docs = [
        load_doc(root, file)
        for file in iter_markdown(root)
        if file.name not in RESERVED
    ]
    groups: dict[str, list[Doc]] = {}
    for doc in docs:
        groups.setdefault(doc.category or "", []).append(doc)

    lines = ["# Memory Index", ""]
    named = sorted(key for key in groups if key)
    order = named + ([""] if "" in groups else [])
    flat = order == [""]
    for key in order:
        if not flat:
            lines += [f"## {humanize(key) if key else 'Other'}", ""]
        for doc in sorted(groups[key], key=lambda d: (d.title.casefold(), d.rel)):
            description = _truncate(doc.description, MEMORY_DESCRIPTION_LIMIT)
            suffix = f" - {description}" if description else ""
            lines.append(f"- [{doc.title}]({doc.rel}){suffix}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _first_sentence(value: str) -> str:
    """The first sentence of a description.

    A room row is one table cell, and a paragraph cut at a character count
    ends mid-word.  ``spec/index-format.md`` asks for one line describing what
    the room contains, which is what the first sentence of a main file's
    opening paragraph almost always is.
    """
    value = collapse(value)
    for position, character in enumerate(value):
        if character != "." or position + 1 == len(value):
            continue
        if value[position + 1] == " " and not value[max(0, position - 2) : position].endswith(" "):
            return value[: position + 1]
    return value


def _truncate(value: str, limit: int) -> str:
    value = collapse(value)
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _escape_cell(value: str) -> str:
    """A pipe inside a description would open a new table column."""
    return value.replace("|", "\\|")


__all__ = [
    "INDEX_LINE_LIMIT",
    "LOCUS_RESERVED",
    "OKF_VERSION",
    "Generated",
    "IndexError_",
    "generate_root",
    "render_memory_index",
    "render_okf_indexes",
    "render_palace_index",
    "write",
]
