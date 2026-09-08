"""``locus recall`` command line.

Usage
-----
    locus recall [--root DIR ...] [-k N] [--budget BYTES] [--include journal]
                 [--type TYPE ...] [--json] [--refresh] [--index PATH] QUERY...

Prints ``Recalled memory:`` followed by the top hits (title, trust tier,
modified date, absolute path, one-line summary) within ``--budget`` bytes,
or nothing at all when there is no hit.  ``--json`` prints a list instead.

Exit status is 0 whenever the query ran, hits or not, so a hook can call it
on every prompt; 1 means a configuration or environment problem, reported
on stderr.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import RecallError, format_json, format_text, recall, resolve_roots


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="locus recall",
        description="Rank memory files against a prompt using a local SQLite FTS5 index.",
    )
    parser.add_argument("query", nargs="+", help="Free text; treated as a bag of words.")
    parser.add_argument(
        "--root",
        action="append",
        metavar="DIR",
        help="Directory to index (repeatable). Default: [recall] roots in .locus.toml, else LOCUS_PALACE.",
    )
    parser.add_argument("-k", "--k", type=int, default=3, help="Number of hits (default: 3).")
    parser.add_argument(
        "--budget", type=int, default=4096, help="Maximum text output in bytes; ignored with --json (default: 4096)."
    )
    parser.add_argument(
        "--include",
        action="append",
        choices=["journal"],
        default=[],
        help="Include document types that are excluded by default (journal).",
    )
    parser.add_argument(
        "--type",
        action="append",
        dest="types",
        metavar="TYPE",
        help="Only return documents of this frontmatter type (repeatable).",
    )
    parser.add_argument("--json", action="store_true", help="Print hits as a JSON list.")
    parser.add_argument("--refresh", action="store_true", help="Rebuild the index from scratch.")
    parser.add_argument(
        "--index",
        type=Path,
        default=None,
        metavar="PATH",
        help="Index file to use instead of the XDG cache location.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        roots = resolve_roots(args.root)
        hits = recall(
            " ".join(args.query),
            roots,
            k=args.k,
            include_journal="journal" in args.include,
            types=args.types,
            refresh=args.refresh,
            index_path=args.index,
        )
    except RecallError as exc:
        print(f"locus recall: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(format_json(hits))
    else:
        text = format_text(hits, budget=args.budget)
        if text:
            sys.stdout.write(text)
    return 0


def cli() -> None:
    sys.exit(main())


if __name__ == "__main__":
    cli()
