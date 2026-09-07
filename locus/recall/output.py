"""Text and JSON rendering for recall hits."""

from __future__ import annotations

import json

from .index import Hit

HEADER = "Recalled memory:\n"
_MIN_SUMMARY_BYTES = 40


def format_json(hits: list[Hit]) -> str:
    return json.dumps([h.to_dict() for h in hits], indent=2, ensure_ascii=False)


def format_text(hits: list[Hit], budget: int = 4096) -> str:
    """Render hits for a hook injection, never exceeding ``budget`` UTF-8 bytes.

    Empty when there are no hits, so a hook emits nothing rather than a
    header with no content.  Hits that do not fit are dropped; the last hit
    that partially fits has its summary truncated.
    """
    if not hits or len(HEADER.encode("utf-8")) > budget:
        return ""
    out = HEADER
    used = len(out.encode("utf-8"))
    for number, hit in enumerate(hits, 1):
        head = _head(number, hit)
        block = head + _indent(hit.summary)
        size = len(block.encode("utf-8"))
        if used + size <= budget:
            out += block
            used += size
            continue
        # Wrapper cost of a summary line: three-space indent plus newline.
        room = budget - used - len(head.encode("utf-8")) - len(_indent("x").encode("utf-8")) + 1
        if room >= _MIN_SUMMARY_BYTES:
            out += head + _indent(truncate_bytes(hit.summary, room))
        break
    return out


def _head(number: int, hit: Hit) -> str:
    flag = "[STALE] " if hit.stale else ""
    return f"{number}. {flag}{hit.title} ({hit.tier}, {hit.modified[:10]})\n   {hit.abs_path}\n"


def _indent(summary: str) -> str:
    return f"   {summary}\n" if summary else ""


def truncate_bytes(text: str, limit: int, marker: str = "...") -> str:
    """Cut ``text`` so that its UTF-8 encoding plus ``marker`` fits in ``limit`` bytes."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    keep = max(limit - len(marker.encode("utf-8")), 0)
    return encoded[:keep].decode("utf-8", errors="ignore").rstrip() + marker
