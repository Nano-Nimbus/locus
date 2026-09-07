"""SQLite FTS5 index over one or more markdown roots.

A root is any tree of markdown files: a Locus palace, an OKF bundle, or a
Claude Code memory directory.  Every ``*.md`` file becomes one document with
its frontmatter lifted into columns (type, title, description, tags,
modified, status, stale_after, trust tier) and its body indexed for
full-text search.

The index is incremental: a file is re-read only when its mtime or size
changed, and re-parsed only when its content hash changed.  Removed files
are dropped on the next refresh.

Ranking is FTS5 bm25 with title and description weighted above the body.
Exact score ties are broken by trust tier (human-reviewed first) and then by
``modified`` (newest first).  Staleness is evaluated at query time so an
index never has to be rebuilt just because a ``stale_after`` date passed.

Only the standard library is used.  FTS5 ships with every CPython build we
have seen, but it is a compile-time option, so :func:`fts5_available` is
checked before any index is opened.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import RecallError, default_index_path
from .frontmatter import split_frontmatter

log = logging.getLogger("locus.recall.index")

SCHEMA_VERSION = "1"
MAX_FILE_BYTES = 1_000_000

# Sub-directories never indexed: tooling, VCS, sidecars, and pipeline output.
_SKIP_DIR_NAMES = {"node_modules", "__pycache__", "_metrics", ".venv"}

# FTS5 column order and bm25 weights: a hit in the title or description says
# far more about relevance than a mention deep in the body.
_FTS_COLUMNS = ("title", "description", "tags", "type", "body")
_BM25_WEIGHTS = (10.0, 5.0, 3.0, 2.0, 1.0)
_BODY_COLUMN = _FTS_COLUMNS.index("body")

_TIER_RANK = {"unverified": 0, "machine-confirmed": 1, "human-reviewed": 2}

_TOKEN_RE = re.compile(r"[^\W_]+")
_MAX_QUERY_TERMS = 24
_MAX_PHRASE_TOKENS = 32
_STOPWORDS = frozenset(
    """a about an and any are as at be been but by can could did do does for from
    get had has have how i if in into is it its just like me my need no not of on
    or our please should so some that the their them then there these they this
    to us want was we were what when where which who why will with would you
    your""".split()
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS docs (
    id INTEGER PRIMARY KEY,
    root TEXT NOT NULL,
    path TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    modified TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    stale_after TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT 'unverified',
    excerpt TEXT NOT NULL DEFAULT '',
    UNIQUE (root, path)
);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    title, description, tags, type, body,
    tokenize = 'porter unicode61'
);
"""


# ---------------------------------------------------------------------------
# Capability check
# ---------------------------------------------------------------------------

_fts5_cache: bool | None = None


def fts5_available() -> bool:
    """True if this Python's sqlite3 was built with the FTS5 extension."""
    global _fts5_cache
    if _fts5_cache is None:
        try:
            con = sqlite3.connect(":memory:")
            con.execute("CREATE VIRTUAL TABLE probe USING fts5(x)")
            con.close()
            _fts5_cache = True
        except sqlite3.OperationalError:
            _fts5_cache = False
    return _fts5_cache


def require_fts5() -> None:
    if not fts5_available():
        raise RecallError(
            "This Python's sqlite3 module was built without FTS5, which locus recall "
            f"requires (sqlite {sqlite3.sqlite_version}). Use a Python build whose "
            "sqlite3 includes FTS5 (python.org, uv-managed, Homebrew, and Debian "
            "builds all do)."
        )


# ---------------------------------------------------------------------------
# Document extraction
# ---------------------------------------------------------------------------

@dataclass
class Document:
    root: str
    path: str
    type: str = ""
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    modified: str = ""
    status: str = ""
    stale_after: str = ""
    tier: str = "unverified"
    excerpt: str = ""
    body: str = ""


def extract_document(root: Path, file: Path, text: str, mtime: float) -> Document:
    """Lift frontmatter into :class:`Document` fields; body is the rest of the file."""
    fm, body = split_frontmatter(text)
    metadata = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    generated = fm.get("generated") if isinstance(fm.get("generated"), dict) else {}

    title = _text(fm.get("title")) or _text(fm.get("name")) or _first_heading(body) or file.stem
    doc_type = _text(fm.get("type")) or _text(metadata.get("type"))
    modified = (
        _text(fm.get("modified"))
        or _text(generated.get("at"))
        or datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(timespec="seconds")
    )
    return Document(
        root=str(root),
        path=file.relative_to(root).as_posix(),
        type=doc_type,
        title=title,
        description=_text(fm.get("description")),
        tags=_tags(fm.get("tags")) or _tags(metadata.get("tags")),
        modified=modified,
        status=_text(fm.get("status")).lower(),
        stale_after=_text(fm.get("stale_after")),
        tier=trust_tier(fm.get("verified")),
        excerpt=_excerpt(body),
        body=body,
    )


def trust_tier(verified: Any) -> str:
    """OKF trust tier from a ``verified`` list: who, if anyone, confirmed the file."""
    if isinstance(verified, str):
        entries: list[Any] = [verified] if verified else []
    elif isinstance(verified, list):
        entries = verified
    else:
        entries = []
    if not entries:
        return "unverified"
    for entry in entries:
        actor = entry.get("by", "") if isinstance(entry, dict) else entry
        if str(actor).startswith("human:"):
            return "human-reviewed"
    return "machine-confirmed"


def _text(value: Any) -> str:
    if value is None or isinstance(value, dict):
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value).strip()


def _tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def _first_heading(body: str) -> str:
    match = _HEADING_RE.search(body)
    return match.group(1).strip() if match else ""


def _excerpt(body: str, limit: int = 280) -> str:
    """First prose paragraph of the body, whitespace-collapsed and capped."""
    paragraph: list[str] = []
    in_comment = False
    for line in body.splitlines():
        stripped = line.strip()
        if in_comment:
            in_comment = "-->" not in stripped
            continue
        if stripped.startswith("<!--"):
            in_comment = "-->" not in stripped
            continue
        if not stripped or stripped.startswith(("#", "|", "```", "---")):
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    text = " ".join(paragraph)
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


# ---------------------------------------------------------------------------
# Query results
# ---------------------------------------------------------------------------

@dataclass
class Hit:
    title: str
    root: str
    path: str
    abs_path: str
    type: str
    description: str
    snippet: str
    excerpt: str
    tags: list[str]
    modified: str
    status: str
    stale_after: str
    tier: str
    stale: bool
    score: float

    @property
    def summary(self) -> str:
        """One line for a hook injection: description, else match context, else excerpt."""
        return self.description or self.snippet or self.excerpt

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["summary"] = self.summary
        return data


@dataclass
class RefreshStats:
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0

    @property
    def changed(self) -> int:
        return self.added + self.updated + self.removed


def is_stale(status: str, stale_after: str, now: datetime | None = None) -> bool:
    """OKF lifecycle: ``status: deprecated`` or a ``stale_after`` in the past."""
    if status == "deprecated":
        return True
    if not stale_after:
        return False
    try:
        deadline = datetime.fromisoformat(stale_after)
    except ValueError:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline <= (now or datetime.now(timezone.utc))


def query_terms(query: str) -> list[str]:
    """Distinct, lower-cased query tokens worth matching on (no stopwords, no 1-char tokens)."""
    terms: list[str] = []
    for token in _TOKEN_RE.findall(query.lower()):
        if len(token) < 2 or token in _STOPWORDS or token in terms:
            continue
        terms.append(token)
        if len(terms) >= _MAX_QUERY_TERMS:
            break
    return terms


def build_match(query: str) -> str | None:
    """Turn free text into an FTS5 ``MATCH`` expression, or ``None`` if nothing usable.

    The prompt is treated as a bag of words: tokens are quoted (so FTS5
    operators and punctuation in the prompt are inert) and joined with OR, and
    bm25 rewards documents that match more of them.  When there are at least
    two tokens the whole sequence is added as one phrase too, so a document
    containing the literal ``10.0.0.201`` or ``pg_basebackup`` outranks
    one that merely mentions ``192`` or ``pg`` somewhere.
    """
    tokens = _TOKEN_RE.findall(query.lower())
    terms = query_terms(query)
    if not terms:
        return None
    parts: list[str] = []
    if len(tokens) >= 2:
        parts.append('"' + " ".join(tokens[:_MAX_PHRASE_TOKENS]) + '"')
    parts.extend(f'"{t}"' for t in terms)
    return " OR ".join(parts)


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

class RecallIndex:
    """An FTS5 index over ``roots``; use as a context manager or call :meth:`close`."""

    def __init__(self, roots: list[Path], index_path: Path | None = None) -> None:
        require_fts5()
        self.roots = [Path(r).resolve() for r in roots]
        self.index_path = index_path or default_index_path(self.roots)
        self.in_memory = False
        self._con = self._connect()
        self._ensure_schema()

    def __enter__(self) -> RecallIndex:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._con.close()

    # -- setup ---------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        try:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(self.index_path, timeout=5.0)
            con.execute("PRAGMA journal_mode=WAL")
            return con
        except (OSError, sqlite3.OperationalError) as exc:
            # A read-only home or cache dir must not break recall; the index
            # is derived data, so rebuilding it in memory each time is fine.
            log.warning("cannot open %s (%s); using an in-memory index", self.index_path, exc)
            self.in_memory = True
            return sqlite3.connect(":memory:")

    def _ensure_schema(self) -> None:
        con = self._con
        con.executescript(_SCHEMA)
        row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None or row[0] != SCHEMA_VERSION:
            con.executescript("DELETE FROM docs; DELETE FROM docs_fts;")
            con.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
            con.commit()

    # -- refresh -------------------------------------------------------------

    def refresh(self, force: bool = False) -> RefreshStats:
        """Bring the index in line with the roots; ``force`` rebuilds from scratch."""
        con = self._con
        stats = RefreshStats()
        if force:
            con.executescript("DELETE FROM docs; DELETE FROM docs_fts;")
        known: dict[tuple[str, str], tuple[int, int, int, str]] = {
            (root, path): (doc_id, mtime_ns, size, content_hash)
            for doc_id, root, path, mtime_ns, size, content_hash in con.execute(
                "SELECT id, root, path, mtime_ns, size, content_hash FROM docs"
            )
        }
        seen: set[tuple[str, str]] = set()

        for root in self.roots:
            root_key = str(root)
            for file in iter_markdown(root):
                rel = file.relative_to(root).as_posix()
                key = (root_key, rel)
                seen.add(key)
                try:
                    st = file.stat()
                except OSError:
                    continue
                prev = known.get(key)
                if prev is not None and prev[1] == st.st_mtime_ns and prev[2] == st.st_size:
                    stats.unchanged += 1
                    continue
                if st.st_size > MAX_FILE_BYTES:
                    log.info("skipping %s: %d bytes exceeds %d", file, st.st_size, MAX_FILE_BYTES)
                    seen.discard(key)
                    continue
                try:
                    text = file.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    seen.discard(key)
                    continue
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if prev is not None and prev[3] == content_hash:
                    con.execute(
                        "UPDATE docs SET mtime_ns = ?, size = ? WHERE id = ?",
                        (st.st_mtime_ns, st.st_size, prev[0]),
                    )
                    stats.unchanged += 1
                    continue
                doc = extract_document(root, file, text, st.st_mtime)
                self._upsert(doc, st.st_mtime_ns, st.st_size, content_hash, prev[0] if prev else None)
                if prev is None:
                    stats.added += 1
                else:
                    stats.updated += 1

        for key, (doc_id, *_rest) in known.items():
            if key not in seen:
                con.execute("DELETE FROM docs_fts WHERE rowid = ?", (doc_id,))
                con.execute("DELETE FROM docs WHERE id = ?", (doc_id,))
                stats.removed += 1

        con.commit()
        return stats

    def _upsert(
        self,
        doc: Document,
        mtime_ns: int,
        size: int,
        content_hash: str,
        existing_id: int | None,
    ) -> None:
        con = self._con
        tags = ", ".join(doc.tags)
        if existing_id is None:
            cur = con.execute(
                """INSERT INTO docs (root, path, mtime_ns, size, content_hash, type, title,
                   description, tags, modified, status, stale_after, tier, excerpt)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (doc.root, doc.path, mtime_ns, size, content_hash, doc.type, doc.title,
                 doc.description, tags, doc.modified, doc.status, doc.stale_after,
                 doc.tier, doc.excerpt),
            )
            doc_id = int(cur.lastrowid)
        else:
            doc_id = existing_id
            con.execute(
                """UPDATE docs SET mtime_ns = ?, size = ?, content_hash = ?, type = ?, title = ?,
                   description = ?, tags = ?, modified = ?, status = ?, stale_after = ?,
                   tier = ?, excerpt = ? WHERE id = ?""",
                (mtime_ns, size, content_hash, doc.type, doc.title, doc.description, tags,
                 doc.modified, doc.status, doc.stale_after, doc.tier, doc.excerpt, doc_id),
            )
            con.execute("DELETE FROM docs_fts WHERE rowid = ?", (doc_id,))
        con.execute(
            "INSERT INTO docs_fts (rowid, title, description, tags, type, body) VALUES (?, ?, ?, ?, ?, ?)",
            (doc_id, doc.title, doc.description, tags, doc.type, doc.body),
        )

    # -- query ---------------------------------------------------------------

    def search(
        self,
        query: str,
        k: int = 3,
        include_journal: bool = False,
        types: list[str] | None = None,
        scope: str | None = None,
        now: datetime | None = None,
    ) -> list[Hit]:
        """Top-``k`` hits for ``query``.

        ``scope`` limits results to one relative path (a file or a directory
        prefix) within a root, which is how ``memory_search`` honours its
        ``path`` argument.  ``types`` limits results to those document types
        (case-insensitive).  ``type: Journal`` documents are skipped unless
        ``include_journal`` is set.
        """
        match = build_match(query)
        if match is None or k <= 0:
            return []
        weights = ", ".join(str(w) for w in _BM25_WEIGHTS)
        sql = [
            f"""SELECT d.root, d.path, d.type, d.title, d.description, d.tags, d.modified,
                       d.status, d.stale_after, d.tier, d.excerpt,
                       bm25(docs_fts, {weights}) AS score,
                       snippet(docs_fts, {_BODY_COLUMN}, '', '', '...', 20) AS snip,
                       docs_fts.body AS body
                FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid
                WHERE docs_fts MATCH ?"""
        ]
        params: list[Any] = [match]
        if not include_journal:
            sql.append("AND lower(d.type) <> 'journal'")
        if types:
            sql.append(f"AND lower(d.type) IN ({', '.join('?' for _ in types)})")
            params.extend(t.lower() for t in types)
        if scope:
            clean = scope.strip("/")
            if clean:
                sql.append("AND (d.path = ? OR d.path LIKE ? ESCAPE '\\')")
                params.extend([clean, _like_prefix(clean + "/")])
        # Over-fetch so tie-breaking sees the whole cluster of equal scores.
        sql.append("ORDER BY score LIMIT ?")
        params.append(max(k * 4, 40))

        try:
            rows = self._con.execute("\n".join(sql), params).fetchall()
        except sqlite3.OperationalError as exc:
            log.warning("FTS5 query failed for %r: %s", match, exc)
            return []

        moment = now or datetime.now(timezone.utc)
        terms = query_terms(query)
        hits = [
            Hit(
                title=title,
                root=root,
                path=path,
                abs_path=str(Path(root) / path),
                type=doc_type,
                description=description,
                snippet=best_line(body, terms) or _clean_snippet(snip),
                excerpt=excerpt,
                tags=[t for t in tags.split(", ") if t],
                modified=modified,
                status=status,
                stale_after=stale_after,
                tier=tier,
                stale=is_stale(status, stale_after, moment),
                score=round(float(score), 4),
            )
            for (root, path, doc_type, title, description, tags, modified, status,
                 stale_after, tier, excerpt, score, snip, body) in rows
        ]
        hits.sort(key=lambda h: (round(h.score, 3), -_TIER_RANK.get(h.tier, 0), _desc(h.modified)))
        return hits[:k]


def _desc(value: str) -> tuple[int, ...]:
    """Sort key that orders ISO-ish date strings newest first."""
    return tuple(-ord(c) for c in value)


def _like_prefix(prefix: str) -> str:
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


def _clean_snippet(snippet: str) -> str:
    return re.sub(r"\s+", " ", snippet).strip()


_SNIPPET_CHARS = 240


def best_line(body: str, terms: list[str], limit: int = _SNIPPET_CHARS) -> str:
    """The body line that contains the most query terms, whitespace-collapsed and capped.

    Markdown knowledge is mostly one fact per bullet or table row, so a whole
    line is a better snippet than a fixed token window around the match.
    Returns ``""`` when no line contains any term (for example when only the
    title matched); callers fall back to the FTS5 snippet then.
    """
    if not terms:
        return ""
    wanted = set(terms)
    best = ""
    best_count = 0
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        count = len(wanted.intersection(_TOKEN_RE.findall(stripped.lower())))
        if count > best_count:
            best, best_count = stripped, count
            if count == len(wanted):
                break
    if not best:
        return ""
    best = re.sub(r"\s+", " ", best)
    return best if len(best) <= limit else best[: limit - 3].rstrip() + "..."


def iter_markdown(root: Path):
    """Yield every ``*.md`` under ``root``, skipping dot-directories and tooling dirs."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIR_NAMES
        )
        for name in sorted(filenames):
            if name.endswith(".md"):
                yield Path(dirpath) / name
