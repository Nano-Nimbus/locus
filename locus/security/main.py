"""locus-security CLI: key management and bulk signing for a palace.

Usage
-----
    locus-security init-keys   --palace PATH [--key-id ID] [--expires-days N] [--force]
    locus-security sign-all    --palace PATH
    locus-security verify-all  --palace PATH
    locus-security rotate-keys --palace PATH

Every subcommand reads ``locus-security.yaml`` from the palace root to find
the key store (``key_store``, default ``.security/keys/``).  Set
``LOCUS_SIGNING_PASSPHRASE`` to encrypt the private key at rest; the same
variable must be set again whenever the key is loaded.

Exit codes: 0 on success, 1 on a reported error (missing config or keys,
existing keys without ``--force``, or at least one file failing
``verify-all``).
"""

from __future__ import annotations

import argparse
import importlib.metadata
import sys
from collections.abc import Iterator
from pathlib import Path

from .config import SecurityConfig, load_security_config
from .keys import generate_keypair, load_keystore, rotate_keypair, save_keypair
from .signing import sign_file, verify_file

# Directories whose markdown files are never signed or verified: signature
# sidecars and key material live in dot-directories, and _metrics/ is owned
# by the audit pipeline.  Any other dot-directory (.git, .obsidian) is
# tooling, not knowledge.
_SKIP_DIRS = {"_metrics"}


class CliError(Exception):
    """A user-facing error: printed to stderr, exit status 1."""


def _load_config(palace: Path) -> SecurityConfig:
    config = load_security_config(palace)
    if config is None:
        raise CliError(
            f"No locus-security.yaml found in {palace}. "
            "Copy templates/locus-security.yaml to the palace root first."
        )
    return config


def iter_signable_files(palace: Path) -> Iterator[Path]:
    """Yield every ``*.md`` under ``palace`` outside skipped directories, sorted."""
    for path in sorted(palace.rglob("*.md")):
        parent_parts = path.relative_to(palace).parts[:-1]
        if any(part in _SKIP_DIRS or part.startswith(".") for part in parent_parts):
            continue
        if path.is_file():
            yield path


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_init_keys(
    palace: Path,
    key_id: str | None = None,
    expires_days: int = 365,
    force: bool = False,
) -> int:
    config = _load_config(palace)
    store = config.key_store_path
    if (store / "active.pem").exists() and not force:
        raise CliError(
            f"An active keypair already exists in {store}. "
            "Use rotate-keys to rotate it, or --force to overwrite it."
        )
    keypair = generate_keypair(
        key_id=key_id,
        expires_days=expires_days if expires_days > 0 else None,
    )
    save_keypair(keypair, store)
    print(f"Generated keypair {keypair.key_id} in {store}")
    print(f"  expires: {keypair.expires_at or 'never'}")
    return 0


def cmd_sign_all(palace: Path) -> int:
    config = _load_config(palace)
    keystore = load_keystore(config.key_store_path)
    count = 0
    for path in iter_signable_files(palace):
        sign_file(path, palace, keystore.active)
        count += 1
    print(f"Signed {count} file{'s' if count != 1 else ''} with key {keystore.active.key_id}")
    return 0


def cmd_verify_all(palace: Path) -> int:
    config = _load_config(palace)
    keystore = load_keystore(config.key_store_path)
    total = failed = 0
    for path in iter_signable_files(palace):
        total += 1
        result = verify_file(path, palace, keystore)
        if not result.trusted:
            failed += 1
        status = "ok  " if result.trusted else "FAIL"
        print(f"{status} {path.relative_to(palace)}  ({result.reason})")
    print(f"{total - failed}/{total} files verified")
    return 1 if failed else 0


def cmd_rotate_keys(palace: Path) -> int:
    config = _load_config(palace)
    keystore = load_keystore(config.key_store_path)
    old_id = keystore.active.key_id
    new_key = rotate_keypair(keystore, config.key_store_path)
    print(f"Rotated key {old_id} to {new_key.key_id}")
    print(f"  retired public key kept in {config.key_store_path / 'retired'}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="locus-security",
        description="Manage signing keys and file signatures for a Locus palace.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {importlib.metadata.version('locus-mcp')}",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add_palace(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--palace",
            required=True,
            type=Path,
            help="Path to the palace root (must contain locus-security.yaml).",
        )

    p_init = sub.add_parser("init-keys", help="Generate the active Ed25519 keypair.")
    add_palace(p_init)
    p_init.add_argument("--key-id", default=None, help="Key identifier (default: locus-YYYY-MM-DD).")
    p_init.add_argument(
        "--expires-days",
        type=int,
        default=365,
        help="Days until the key expires; 0 means never (default: 365).",
    )
    p_init.add_argument("--force", action="store_true", help="Overwrite an existing active keypair.")

    add_palace(sub.add_parser("sign-all", help="Sign every markdown file in the palace."))
    add_palace(sub.add_parser("verify-all", help="Verify every markdown file; exit 1 on any failure."))
    add_palace(sub.add_parser("rotate-keys", help="Retire the active key and generate a new one."))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    palace = args.palace.expanduser().resolve()
    if not palace.is_dir():
        parser.error(f"Palace path does not exist: {palace}")

    try:
        if args.command == "init-keys":
            return cmd_init_keys(palace, args.key_id, args.expires_days, args.force)
        if args.command == "sign-all":
            return cmd_sign_all(palace)
        if args.command == "verify-all":
            return cmd_verify_all(palace)
        return cmd_rotate_keys(palace)
    except (CliError, FileNotFoundError, ValueError) as exc:
        print(f"locus-security: {exc}", file=sys.stderr)
        return 1


def cli() -> None:
    sys.exit(main())


if __name__ == "__main__":
    cli()
