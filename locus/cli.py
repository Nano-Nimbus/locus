"""Console entry point for the ``locus`` command.

``locus recall ...`` is dispatched here before anything heavy is imported:
a hook that runs it on every prompt should pay for ``sqlite3`` and the
recall package, not for the Agent SDK.  ``locus lint``, ``locus index`` and
``locus init`` are dispatched the same way, so a CI job that only checks
conformance, or a first-run scaffold, never installs or imports the SDK
either.  Every other invocation is forwarded unchanged to the agent CLI
(``locus --palace ... --task ...``).
"""

from __future__ import annotations

import sys

# Subcommand to the callable that takes its argv tail and returns an exit code.
# Every entry here must be importable without the Agent SDK.
_SUBCOMMANDS = {
    "recall": ("locus.recall.main", "main"),
    "lint": ("locus.conform.main", "lint_main"),
    "index": ("locus.conform.main", "index_main"),
    "init": ("locus.scaffold", "init_main"),
}


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in _SUBCOMMANDS:
        module_name, function_name = _SUBCOMMANDS[sys.argv[1]]
        module = __import__(module_name, fromlist=[function_name])
        sys.exit(getattr(module, function_name)(sys.argv[2:]))
    from locus.agent.main import cli

    cli()


if __name__ == "__main__":
    main()
