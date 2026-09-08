"""Rules for ``locus lint``: OKF v0.2 conformance plus Locus palace conventions.

Two rule families run over the same walk.

**OKF** (rule ids prefixed ``okf.``) checks what the specification actually
requires: every non-reserved document parses and carries a non-empty ``type``;
``index.md`` carries no frontmatter beyond a bundle-root ``okf_version``;
``log.md`` is date-headed and newest first; the timestamps that do exist are
ISO 8601.  The specification is explicit that a consumer "MUST NOT reject
documents with unrecognized fields" and "MUST tolerate unknown types", so
unknown keys and unknown type values are never reported.  ``verified`` is
accepted as a list of mappings or as one bare mapping.

**Locus** (rule ids prefixed ``locus.``) checks the palace conventions: the
size limits from ``spec/size-limits.md`` and the room main-file rule from
``spec/room-conventions.md``.

Severity separates the two audiences.  An ``error`` fails ``--check``; a
``warning`` is advisory and only fails under ``--strict``.  Soft size limits
are warnings and hard limits are errors, and a palace root, which legitimately
carries no frontmatter at all, reports missing ``type`` as a warning rather
than failing CI on a layout the palace spec itself describes.
"""

from __future__ import annotations

import re
from itertools import pairwise
from pathlib import Path
from typing import Any

from .config import ConformConfig
from .model import (
    LOCUS_RESERVED,
    OKF_RESERVED,
    RESERVED,
    Doc,
    Violation,
    is_iso_date,
    is_iso_timestamp,
    iter_dirs,
    iter_markdown,
    load_doc,
    palace_file_class,
    root_kind,
    text_of,
)

# (soft, hard) line counts from spec/size-limits.md. Session logs have no
# limit: they are append-only and consolidated after the fact.
SIZE_LIMITS: dict[str, tuple[int, int]] = {
    "INDEX.md": (40, 50),
    "MEMORY.md": (150, 200),
    "main": (150, 200),
    "specialty": (200, 300),
}

_LOG_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_LIST_ITEM_RE = re.compile(r"^\s*[*+-]\s+(.*)$")
_INDEX_ENTRY_RE = re.compile(r"^\s*\*\s+\[[^\]]*\]\([^)]+\)\s*(?:-\s+\S.*)?$")


def lint_roots(roots: list[Path], config: ConformConfig) -> list[Violation]:
    violations: list[Violation] = []
    for root in roots:
        violations.extend(lint_root(root, config))
    return violations


def lint_root(root: Path, config: ConformConfig) -> list[Violation]:
    """Every violation in one root, in walk order."""
    kind = root_kind(root)
    violations: list[Violation] = []
    for file in iter_markdown(root):
        violations.extend(lint_file(load_doc(root, file), config, kind))
    if kind == "palace":
        violations.extend(_lint_palace_rooms(root))
    return violations


def lint_file(doc: Doc, config: ConformConfig, kind: str) -> list[Violation]:
    """Every violation in one file."""
    violations: list[Violation] = []
    if doc.frontmatter_state == "unreadable":
        return [
            Violation(doc.file, "okf.unreadable", "not readable as UTF-8 text")
        ]
    if doc.name == "index.md":
        violations.extend(_lint_okf_index(doc))
    elif doc.name == "log.md":
        violations.extend(_lint_okf_log(doc))
    elif doc.name not in LOCUS_RESERVED:
        violations.extend(_lint_concept(doc, config, kind))
    violations.extend(_lint_size(doc, kind))
    return violations


# ---------------------------------------------------------------------------
# OKF rules
# ---------------------------------------------------------------------------

def _lint_concept(doc: Doc, config: ConformConfig, kind: str) -> list[Violation]:
    """Rules for a concept document: anything that is not a reserved filename."""
    violations: list[Violation] = []
    # A palace stores prose without frontmatter by design (spec/recall.md), so
    # its missing types are advisory rather than a CI failure.
    severity = "warning" if kind == "palace" else "error"
    inferred = config.type_for(doc.root, doc.file)
    fix = f"add type: {inferred}" if inferred else None

    if doc.frontmatter_state == "unterminated":
        # Not fixable: inserting a closing marker would guess where the
        # frontmatter the author meant to write ends.
        return [
            Violation(
                doc.file,
                "okf.frontmatter-unparseable",
                "frontmatter opens with --- but is never closed",
            )
        ]
    if doc.frontmatter_state == "none":
        violations.append(
            Violation(
                doc.file,
                "okf.frontmatter-missing",
                "no frontmatter; OKF requires at least a type",
                severity,
                fix,
            )
        )
    elif not doc.okf_type:
        metadata = doc.frontmatter.get("metadata")
        nested = text_of(metadata.get("type")) if isinstance(metadata, dict) else ""
        detail = (
            f"; metadata.type is {nested!r}, a Claude Code category, not an OKF type"
            if nested
            else ""
        )
        violations.append(
            Violation(
                doc.file,
                "okf.type-missing",
                f"frontmatter has no non-empty type{detail}",
                severity,
                fix,
            )
        )

    violations.extend(_lint_generated(doc))
    violations.extend(_lint_verified(doc))
    violations.extend(_lint_sources(doc))
    violations.extend(_lint_stale_after(doc))
    violations.extend(_lint_archive_status(doc, config))
    return violations


def _lint_generated(doc: Doc) -> list[Violation]:
    generated = doc.frontmatter.get("generated")
    if not isinstance(generated, dict):
        return []
    violations: list[Violation] = []
    if not text_of(generated.get("by")):
        # No fix: the producer of the text cannot be inferred from the text.
        violations.append(
            Violation(doc.file, "okf.generated-by", "generated has no by (producer actor)")
        )
    at = text_of(generated.get("at"))
    if not at:
        violations.append(
            Violation(
                doc.file,
                "okf.generated-at",
                "generated has no at (production timestamp)",
                "error",
                "add generated.at from the file's first git commit date",
            )
        )
    elif not is_iso_timestamp(at):
        violations.append(
            Violation(doc.file, "okf.timestamp", f"generated.at is not ISO 8601: {at!r}")
        )
    return violations


def _lint_verified(doc: Doc) -> list[Violation]:
    """``verified`` is a list of ``{by, at}``; one bare mapping is tolerated."""
    verified = doc.frontmatter.get("verified")
    if verified is None or verified == "":
        return []
    entries = verified if isinstance(verified, list) else [verified]
    violations: list[Violation] = []
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            violations.append(
                Violation(
                    doc.file,
                    "okf.verified-actor",
                    f"verified entry {position} is not a mapping with a by",
                )
            )
            continue
        if not text_of(entry.get("by")):
            violations.append(
                Violation(doc.file, "okf.verified-actor", f"verified entry {position} has no by")
            )
        at = text_of(entry.get("at"))
        if at and not is_iso_timestamp(at):
            violations.append(
                Violation(
                    doc.file,
                    "okf.timestamp",
                    f"verified entry {position} at is not ISO 8601: {at!r}",
                )
            )
    return violations


def _lint_sources(doc: Doc) -> list[Violation]:
    sources = doc.frontmatter.get("sources")
    if sources is None or sources == "":
        return []
    entries = sources if isinstance(sources, list) else [sources]
    violations: list[Violation] = []
    for position, entry in enumerate(entries, start=1):
        resource = text_of(entry.get("resource")) if isinstance(entry, dict) else ""
        if not resource:
            violations.append(
                Violation(
                    doc.file,
                    "okf.sources-resource",
                    f"sources entry {position} has no resource",
                )
            )
    return violations


def _lint_stale_after(doc: Doc) -> list[Violation]:
    stale_after = text_of(doc.frontmatter.get("stale_after"))
    if stale_after and not is_iso_timestamp(stale_after):
        return [
            Violation(
                doc.file, "okf.timestamp", f"stale_after is not ISO 8601: {stale_after!r}"
            )
        ]
    return []


def _lint_archive_status(doc: Doc, config: ConformConfig) -> list[Violation]:
    """An archived path should say so: OKF marks stale content, never deletes it."""
    if not config.is_archived(doc.root, doc.file):
        return []
    status = text_of(doc.frontmatter.get("status")).lower()
    if status == "deprecated":
        return []
    if status:
        # An explicit status is the author's call; --fix never rewrites a key.
        return [
            Violation(
                doc.file,
                "locus.archive-status",
                f"matches an archive glob but status is {status!r}, not 'deprecated'",
                "warning",
            )
        ]
    return [
        Violation(
            doc.file,
            "locus.archive-status",
            "matches an archive glob but has no status",
            "error",
            "add status: deprecated",
        )
    ]


def _lint_okf_index(doc: Doc) -> list[Violation]:
    """``index.md`` carries no frontmatter, except ``okf_version`` at a bundle root."""
    violations: list[Violation] = []
    at_bundle_root = doc.file.parent == doc.root
    if doc.frontmatter_state == "unterminated":
        return [
            Violation(
                doc.file,
                "okf.frontmatter-unparseable",
                "frontmatter opens with --- but is never closed",
            )
        ]
    allowed = {"okf_version"} if at_bundle_root else set()
    extra = sorted(set(doc.frontmatter) - allowed)
    if extra:
        detail = (
            "only okf_version is allowed at a bundle root"
            if at_bundle_root
            else "index.md carries no frontmatter outside a bundle root"
        )
        violations.append(
            Violation(
                doc.file,
                "okf.index-frontmatter",
                f"unexpected frontmatter {', '.join(extra)}; {detail}",
            )
        )
    for number, line in enumerate(doc.body.splitlines(), start=1):
        item = _LIST_ITEM_RE.match(line)
        if item and not _INDEX_ENTRY_RE.match(line):
            violations.append(
                Violation(
                    doc.file,
                    "okf.index-entry",
                    f"line {number} is not '* [Title](path) - description': {line.strip()!r}",
                    "warning",
                )
            )
    return violations


def _lint_okf_log(doc: Doc) -> list[Violation]:
    """``log.md`` is date-grouped, ISO 8601, newest first, and carries no frontmatter."""
    if doc.frontmatter_state == "unterminated":
        return [
            Violation(
                doc.file,
                "okf.frontmatter-unparseable",
                "frontmatter opens with --- but is never closed",
            )
        ]
    violations: list[Violation] = []
    if doc.frontmatter:
        violations.append(
            Violation(doc.file, "okf.log-frontmatter", "log.md carries no frontmatter")
        )
    dates: list[str] = []
    for heading in _LOG_HEADING_RE.findall(doc.body):
        if not is_iso_date(heading):
            violations.append(
                Violation(
                    doc.file,
                    "okf.log-heading",
                    f"heading {heading!r} is not an ISO 8601 date (YYYY-MM-DD)",
                )
            )
            continue
        dates.append(heading)
    for earlier, later in pairwise(dates):
        if later > earlier:
            violations.append(
                Violation(
                    doc.file,
                    "okf.log-order",
                    f"entries run oldest first: {later} follows {earlier}",
                )
            )
            break
    return violations


# ---------------------------------------------------------------------------
# Locus palace rules
# ---------------------------------------------------------------------------

def _lint_size(doc: Doc, kind: str) -> list[Violation]:
    """Line-count limits from spec/size-limits.md."""
    if doc.name == "INDEX.md":
        limits, label = SIZE_LIMITS["INDEX.md"], "INDEX.md"
    elif doc.name == "MEMORY.md":
        limits, label = SIZE_LIMITS["MEMORY.md"], "MEMORY.md"
    elif kind == "palace" and not doc.episodic:
        file_class = palace_file_class(doc.root, doc.file)
        if file_class not in SIZE_LIMITS:
            return []
        limits, label = SIZE_LIMITS[file_class], f"{file_class} file"
    else:
        return []

    soft, hard = limits
    lines = doc.line_count
    if lines > hard:
        return [
            Violation(
                doc.file,
                "locus.size-limit",
                f"{lines} lines exceeds the {hard}-line hard limit for a {label}",
            )
        ]
    if lines > soft:
        return [
            Violation(
                doc.file,
                "locus.size-limit",
                f"{lines} lines exceeds the {soft}-line soft limit for a {label}",
                "warning",
            )
        ]
    return []


def _lint_palace_rooms(root: Path) -> list[Violation]:
    """Every room directory holds a main file named after the room.

    Container directories (``global/``, ``projects/``, anything holding only
    sub-rooms) have no markdown of their own and are exempt, as are the
    append-only ``sessions/`` and ``journal/`` directories.
    """
    violations: list[Violation] = []
    for directory in iter_dirs(root):
        if directory == root:
            continue
        relative = directory.relative_to(root)
        if any(part in {"sessions", "journal"} for part in relative.parts):
            continue
        try:
            names = {entry.name for entry in directory.iterdir() if entry.is_file()}
        except OSError:
            continue
        markdown = {name for name in names if name.endswith(".md")}
        if not markdown or markdown <= RESERVED:
            continue
        expected = f"{directory.name}.md"
        if expected in names or "README.md" in names or names & LOCUS_RESERVED:
            continue
        violations.append(
            Violation(
                directory,
                "locus.room-main-file",
                f"room has no main file; expected {expected} or README.md",
            )
        )
    return violations


def summarize(violations: list[Violation]) -> dict[str, Any]:
    return {
        "errors": sum(1 for v in violations if v.severity == "error"),
        "warnings": sum(1 for v in violations if v.severity == "warning"),
        "fixable": sum(1 for v in violations if v.fixable),
        "total": len(violations),
    }


def failed(violations: list[Violation], strict: bool = False) -> bool:
    """True when ``--check`` should exit non-zero."""
    if strict:
        return bool(violations)
    return any(v.severity == "error" for v in violations)


__all__ = [
    "OKF_RESERVED",
    "SIZE_LIMITS",
    "failed",
    "lint_file",
    "lint_root",
    "lint_roots",
    "summarize",
]
