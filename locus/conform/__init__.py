"""``locus lint`` and ``locus index``: OKF conformance and generated indexes.

Importable API::

    from locus.conform import lint_root, generate_root, load_config

    violations = lint_root(Path("docs"), load_config())
    for item in generate_root(Path("docs")):
        print(item.path, item.status)

See :mod:`locus.conform.lint` for the rules, :mod:`locus.conform.fix` for what
``--fix`` will and will not touch, and :mod:`locus.conform.generate` for the
three index formats.
"""

from __future__ import annotations

from .config import ConformConfig, ConformError, load_config
from .fix import GitDates, fix_root, fix_text
from .generate import Generated, IndexError_, generate_root, write
from .lint import failed, lint_file, lint_root, lint_roots, summarize
from .model import Doc, Violation, load_doc, root_kind

__all__ = [
    "ConformConfig",
    "ConformError",
    "Doc",
    "Generated",
    "GitDates",
    "IndexError_",
    "Violation",
    "failed",
    "fix_root",
    "fix_text",
    "generate_root",
    "lint_file",
    "lint_root",
    "lint_roots",
    "load_config",
    "load_doc",
    "root_kind",
    "summarize",
    "write",
]
