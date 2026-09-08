"""Console entry point for the ``locus`` command.

``locus recall ...`` is dispatched here before anything heavy is imported:
a hook that runs it on every prompt should pay for ``sqlite3`` and the
recall package, not for the Agent SDK.  Every other invocation is forwarded
unchanged to the agent CLI (``locus --palace ... --task ...``).
"""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "recall":
        from locus.recall.main import main as recall_main

        sys.exit(recall_main(sys.argv[2:]))
    from locus.agent.main import cli

    cli()


if __name__ == "__main__":
    main()
