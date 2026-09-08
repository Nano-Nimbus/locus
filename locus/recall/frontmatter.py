"""Minimal frontmatter parser, dependency-free.

Locus deliberately avoids PyYAML on the recall path: the hook that calls
``locus recall`` on every prompt should pay for ``sqlite3`` and nothing
else.  The parser covers the subset of YAML that palaces, OKF bundles, and
Claude Code memory files actually use:

- ``key: value`` scalars (surrounding quotes stripped, trailing comments dropped)
- flow lists ``[a, b, c]``
- block lists of scalars (``- item``) and of mappings (``- by: x`` / ``  at: y``)
- nested mappings (``metadata:`` / ``  type: project``), any depth

Anything else is kept as its raw string.  Unknown keys are preserved, as OKF
requires of consumers.  Scalars are always returned as ``str``; the caller
decides what a value means.
"""

from __future__ import annotations

import re
from typing import Any

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)(?:\r?\n)?---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_KEY_RE = re.compile(r"[A-Za-z0-9_.\-]+")

# (indent, content) pairs; blank lines and comment lines are dropped up front.
_Line = tuple[int, str]


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return ``(frontmatter, body)``; frontmatter is ``{}`` when absent."""
    text = text.lstrip("﻿")
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text
    return parse_mapping(match.group(1)), text[match.end():]


def parse_mapping(text: str) -> dict[str, Any]:
    """Parse a block of ``key: value`` lines into a dict."""
    lines = _lines(text)
    if not lines:
        return {}
    result, _ = _parse_mapping(lines, 0, lines[0][0])
    return result


def _lines(text: str) -> list[_Line]:
    out: list[_Line] = []
    for raw in text.splitlines():
        stripped = raw.lstrip(" ")
        if not stripped.strip() or stripped.startswith("#"):
            continue
        out.append((len(raw) - len(stripped), stripped.rstrip()))
    return out


def _parse_mapping(lines: list[_Line], i: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while i < len(lines):
        ind, content = lines[i]
        if ind < indent or content.startswith("- ") or content == "-":
            break
        if ind > indent:
            i += 1  # stray deeper line with no parent key: skip it
            continue
        key, sep, rest = content.partition(":")
        if not sep or not _KEY_RE.fullmatch(key.strip()):
            i += 1
            continue
        key = key.strip()
        rest = rest.strip()
        if rest:
            result[key] = _scalar_or_flow(rest)
            i += 1
            continue
        # ``key:`` with the value on the following, deeper-indented lines.
        if i + 1 < len(lines) and lines[i + 1][0] > indent:
            child_indent, child = lines[i + 1]
            if child.startswith("- ") or child == "-":
                result[key], i = _parse_list(lines, i + 1, child_indent)
            else:
                result[key], i = _parse_mapping(lines, i + 1, child_indent)
        else:
            result[key] = ""
            i += 1
    return result, i


def _parse_list(lines: list[_Line], i: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while i < len(lines):
        ind, content = lines[i]
        if ind != indent or not (content.startswith("- ") or content == "-"):
            break
        body = content[1:].strip()
        if not body:
            if i + 1 < len(lines) and lines[i + 1][0] > indent:
                value, i = _parse_mapping(lines, i + 1, lines[i + 1][0])
                items.append(value)
            else:
                items.append("")
                i += 1
            continue
        key, sep, rest = body.partition(":")
        if sep and _KEY_RE.fullmatch(key.strip()) and (rest == "" or rest[0] == " "):
            # ``- key: value`` opens a mapping item; its other keys sit under
            # the column where ``key`` starts.
            column = indent + (len(content) - len(content[1:].lstrip()))
            patched = lines[:]
            patched[i] = (column, body)
            value, i = _parse_mapping(patched, i, column)
            items.append(value)
            continue
        items.append(_scalar_or_flow(body))
        i += 1
    return items, i


def _scalar_or_flow(raw: str) -> Any:
    raw = _strip_comment(raw)
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_unquote(part.strip()) for part in inner.split(",") if part.strip()]
    return _unquote(raw)


def _strip_comment(raw: str) -> str:
    if raw[:1] in ("'", '"'):
        return raw
    idx = raw.find(" #")
    return raw[:idx].rstrip() if idx >= 0 else raw


def _unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    if raw in ("~", "null", "Null", "NULL"):
        return ""
    return raw
