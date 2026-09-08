"""Command lines for ``locus lint`` and ``locus index``.

Usage
-----
    locus lint  [--root DIR ...] [--fix] [--check] [--json] [--strict]
                [--type-map DIR=TYPE ...] [--archive-glob GLOB ...]
    locus index [--root DIR ...] [--check] [--json] [--kind KIND]

``lint`` reports and exits 0 by default, so it can be run for information.
``--check`` is the CI gate: it exits 1 when any error was reported, and with
``--strict`` when any warning was too.  ``index`` writes what drifted and
exits 0; ``--check`` writes nothing and exits 1 if anything would change.

Neither path imports the Agent SDK, so ``locus.cli`` can dispatch both as
cheaply as it dispatches ``recall``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from locus.recall.config import RecallError, resolve_roots

from .config import SECTIONS, load_config
from .fix import GitDates, fix_root
from .generate import Generated, IndexError_, generate_root, write
from .lint import failed, lint_roots, summarize
from .model import Violation, display_path


def _root_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        action="append",
        metavar="DIR",
        help="Directory to check (repeatable). Default: [lint] or [recall] roots "
        "in .locus.toml, else LOCUS_PALACE.",
    )
    parser.add_argument("--json", action="store_true", help="Print a JSON report instead of text.")


def build_lint_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="locus lint",
        description="Check markdown roots for OKF v0.2 conformance and Locus palace conventions.",
    )
    _root_arguments(parser)
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Add inferable frontmatter (type, generated.at, status). Never rewrites a key.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero when anything is reported. For CI.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors under --check.",
    )
    parser.add_argument(
        "--type-map",
        action="append",
        metavar="DIR=TYPE",
        help="Infer this OKF type for files under DIR (repeatable). Also [lint.types].",
    )
    parser.add_argument(
        "--archive-glob",
        action="append",
        metavar="GLOB",
        help="Root-relative glob whose files should carry status: deprecated (repeatable).",
    )
    return parser


def build_index_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="locus index",
        description="Generate index.md, INDEX.md, and MEMORY.md from frontmatter.",
    )
    _root_arguments(parser)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Write nothing; exit non-zero if any index has drifted. For CI.",
    )
    parser.add_argument(
        "--kind",
        choices=["auto", "okf", "palace", "memory"],
        default="auto",
        help="Override how a root is classified (default: auto).",
    )
    return parser


def lint_main(argv: list[str] | None = None) -> int:
    args = build_lint_parser().parse_args(argv)
    try:
        roots = resolve_roots(args.root, sections=SECTIONS)
        config = load_config(
            type_map=args.type_map,
            archive_globs=args.archive_glob,
            strict=args.strict,
        )
        fixed: list[Path] = []
        if args.fix:
            dates = GitDates()
            for root in roots:
                fixed.extend(fix_root(root, config, dates))
        violations = lint_roots(roots, config)
    except RecallError as exc:
        print(f"locus lint: {exc}", file=sys.stderr)
        return 1

    base = roots[0] if len(roots) == 1 else None
    if args.json:
        print(
            json.dumps(
                {
                    "roots": [str(root) for root in roots],
                    "fixed": [display_path(path, base) for path in sorted(fixed)],
                    "violations": [v.to_dict(base) for v in violations],
                    "summary": summarize(violations),
                },
                indent=2,
            )
        )
    else:
        _print_lint_text(violations, fixed, base)

    if args.check and failed(violations, args.strict):
        return 1
    return 0


def _print_lint_text(violations: list[Violation], fixed: list[Path], base: Path | None) -> None:
    for path in sorted(fixed):
        print(f"fixed  {display_path(path, base)}")
    for violation in violations:
        suffix = f" (fix: {violation.fix})" if violation.fix else ""
        print(
            f"{display_path(violation.path, base)}: {violation.severity} "
            f"[{violation.rule}] {violation.message}{suffix}"
        )
    counts = summarize(violations)
    if counts["total"] == 0:
        print("clean: no violations")
        return
    print(
        f"{counts['errors']} error(s), {counts['warnings']} warning(s), "
        f"{counts['fixable']} fixable"
    )


def index_main(argv: list[str] | None = None) -> int:
    args = build_index_parser().parse_args(argv)
    kind = None if args.kind == "auto" else args.kind
    try:
        roots = resolve_roots(args.root, sections=SECTIONS)
        generated: list[Generated] = []
        for root in roots:
            generated.extend(generate_root(root, kind))
        written = [] if args.check else write(generated)
    except (RecallError, IndexError_) as exc:
        print(f"locus index: {exc}", file=sys.stderr)
        return 1

    base = roots[0] if len(roots) == 1 else None
    drifted = [item for item in generated if item.needs_write]
    if args.json:
        print(
            json.dumps(
                {
                    "roots": [str(root) for root in roots],
                    "files": [
                        {
                            "path": display_path(item.path, base),
                            "status": item.status,
                            "lines": len(item.content.splitlines()),
                        }
                        for item in generated
                    ],
                    "written": [display_path(item.path, base) for item in written],
                    "summary": {"total": len(generated), "drifted": len(drifted)},
                },
                indent=2,
            )
        )
    else:
        for item in generated:
            label = "ok" if not item.needs_write else ("drift" if args.check else "wrote")
            print(f"{label:<6} {display_path(item.path, base)}")
        if not generated:
            print("nothing to index")

    if args.check and drifted:
        return 1
    return 0


def lint_cli() -> None:
    sys.exit(lint_main())


def index_cli() -> None:
    sys.exit(index_main())
