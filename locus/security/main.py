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
existing keys without ``--force``, at least one file failing ``verify-all``,
or at least one file skipped by ``sign-all``), 2 on a usage error.

``sign-all`` never stops at the first bad file.  A file it cannot read as
UTF-8, or cannot read at all, is named on stderr and skipped, the remaining
files are still signed, and the command exits 1 so a pipeline does not treat
a partial run as a success.

Symlinks whose target resolves outside the palace root are never signed or
verified: signing one would attach palace-trusted provenance to content the
palace does not own.  They are named on stderr and skipped.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

from .config import SecurityConfig, load_security_config
from .keys import (
    KeyPair,
    default_key_id,
    existing_key_ids,
    generate_keypair,
    load_keystore,
    retire_active,
    rotate_keypair,
    save_keypair,
    unique_key_id,
    validate_key_id,
)
from .signing import sign_file, verify_file

# Directories whose markdown files are never signed or verified: signature
# sidecars and key material live in dot-directories, and _metrics/ is owned
# by the audit pipeline.  Any other dot-directory (.git, .obsidian) is
# tooling, not knowledge.
_SKIP_DIRS = {"_metrics"}

log = logging.getLogger("locus.security.cli")

# Well inside timedelta's range, and far past any sane key lifetime.
_MAX_EXPIRES_DAYS = 36500


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


def iter_signable_files(palace: Path, escaped: list[Path] | None = None) -> Iterator[Path]:
    """Yield every ``*.md`` under ``palace`` outside skipped directories, sorted.

    A path whose target resolves outside ``palace`` is skipped and appended to
    ``escaped`` if a list is given.  ``rglob`` does not descend into symlinked
    directories, but it does yield symlinked *files*, and signing one would
    stamp palace provenance onto content stored outside the palace: anyone who
    can drop a symlink into the palace could otherwise launder an arbitrary
    file into the trusted tier.  Broken symlinks fail ``is_file()`` and are
    skipped silently.
    """
    root = palace.resolve()
    for path in sorted(palace.rglob("*.md")):
        parent_parts = path.relative_to(palace).parts[:-1]
        if any(part in _SKIP_DIRS or part.startswith(".") for part in parent_parts):
            continue
        if not path.is_file():
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if not resolved.is_relative_to(root):
            log.warning("skipping symlink outside palace: %s -> %s", path, resolved)
            if escaped is not None:
                escaped.append(path)
            continue
        yield path


def _reject_expired(keypair: KeyPair) -> None:
    """Refuse to sign with a key that is past its ``expires_at``.

    Nothing consulted ``KeyPair.is_expired`` before, so ``--expires-days`` was
    decorative metadata: an expired key kept signing and its signatures kept
    verifying, which is worse than having no expiry at all because the CLI
    help implies the flag gates something.
    """
    if keypair.is_expired:
        raise CliError(
            f"Key {keypair.key_id} expired on {keypair.expires_at}. "
            "Run rotate-keys to issue a new one; signatures already made with "
            "it keep verifying against the retired public key."
        )


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
    # Check every file, not just active.pem: a store missing only the private
    # key is exactly what a crash mid-save leaves behind, and treating that as
    # "no keys here" silently discarded the rest of the store.
    present = [n for n in ("active.pem", "active.pub", "active.json") if (store / n).exists()]
    if present and not force:
        raise CliError(
            f"An active keypair already exists in {store} ({', '.join(present)}). "
            "Use rotate-keys to rotate it, or --force to overwrite it."
        )
    if force and (store / "active.pem").exists():
        # --force used to overwrite the active key without archiving its
        # public half, so every signature made with it became permanently
        # unverifiable and the key needed to check them was gone from disk.
        try:
            outgoing = load_keystore(store).active
        except (OSError, ValueError) as exc:
            print(
                f"locus-security: cannot archive the outgoing key ({exc}); "
                "signatures made with it will not verify",
                file=sys.stderr,
            )
        else:
            retire_active(outgoing, store)
            print(f"Retired {outgoing.key_id} to {store / 'retired'}")

    taken = existing_key_ids(store)
    if key_id is None:
        # Auto-generated ids are date-stamped, so --force on the same day as an
        # earlier key would reuse that key's id.  Suffix instead.
        key_id = unique_key_id(default_key_id(), taken)
    elif key_id in taken:
        raise CliError(
            f"Key id {key_id!r} is already used by another key in {store}. "
            "Key ids must be unique within a store, or signatures made with "
            "the older key stop verifying. Choose a different --key-id."
        )
    keypair = generate_keypair(
        key_id=key_id,
        expires_days=None if expires_days == 0 else expires_days,
    )
    save_keypair(keypair, store)
    print(f"Generated keypair {keypair.key_id} in {store}")
    print(f"  expires: {keypair.expires_at or 'never'}")
    return 0


def cmd_sign_all(palace: Path) -> int:
    """Sign every signable file, naming and skipping the ones that fail.

    One unreadable file used to abort the whole run, unnamed, leaving every
    later file unsigned.  Each failure is now reported with its path, the run
    continues, and the exit status is 1 if anything was skipped so a pipeline
    cannot mistake a partial run for a clean one.
    """
    config = _load_config(palace)
    keystore = load_keystore(config.key_store_path)
    _reject_expired(keystore.active)
    escaped: list[Path] = []
    count = 0
    skipped: list[str] = []
    for path in iter_signable_files(palace, escaped):
        try:
            sign_file(path, palace, keystore.active)
        except UnicodeDecodeError:
            skipped.append(f"{path.relative_to(palace)}: not valid UTF-8")
            continue
        except OSError as exc:
            skipped.append(f"{path.relative_to(palace)}: {exc.strerror or exc}")
            continue
        count += 1
    for path in escaped:
        skipped.append(f"{path.relative_to(palace)}: symlink resolves outside the palace")
    for line in skipped:
        print(f"skipped {line}", file=sys.stderr)
    print(f"Signed {count} file{'s' if count != 1 else ''} with key {keystore.active.key_id}")
    if skipped:
        print(f"Skipped {len(skipped)} file{'s' if len(skipped) != 1 else ''}", file=sys.stderr)
        return 1
    return 0


def cmd_verify_all(palace: Path) -> int:
    config = _load_config(palace)
    keystore = load_keystore(config.key_store_path)
    escaped: list[Path] = []
    total = failed = 0
    for path in iter_signable_files(palace, escaped):
        total += 1
        result = verify_file(path, palace, keystore)
        if not result.trusted:
            failed += 1
        status = "ok  " if result.trusted else "FAIL"
        print(f"{status} {path.relative_to(palace)}  ({result.reason})")
    for path in escaped:
        print(
            f"FAIL {path.relative_to(palace)}  (symlink resolves outside the palace)",
            file=sys.stderr,
        )
    print(f"{total - failed}/{total} files verified")
    if total == 0 and not escaped:
        # A gate that passes because it looked at nothing is worse than one
        # that fails: the usual cause is verify-all pointed at the wrong root.
        print(f"locus-security: no signable files found in {palace}", file=sys.stderr)
        return 1
    return 1 if (failed or escaped) else 0


def cmd_rotate_keys(palace: Path, expires_days: int = 365) -> int:
    config = _load_config(palace)
    keystore = load_keystore(config.key_store_path)
    old_id = keystore.active.key_id
    new_key = rotate_keypair(
        keystore,
        config.key_store_path,
        expires_days=None if expires_days == 0 else expires_days,
    )
    print(f"Rotated key {old_id} to {new_key.key_id}")
    print(f"  retired public key kept in {config.key_store_path / 'retired'}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _key_id(value: str) -> str:
    """argparse type for ``--key-id``: rejects anything unsafe as a filename."""
    try:
        return validate_key_id(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _expires_days(value: str) -> int:
    """argparse type for ``--expires-days``: a non-negative integer.

    A negative value used to fall through the ``> 0`` guard and silently mean
    "never expires", which is the opposite of what someone typing ``-5``
    intends.
    """
    try:
        days = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if days < 0:
        raise argparse.ArgumentTypeError(
            f"must be 0 (never expires) or a positive number of days, got {days}"
        )
    if days > _MAX_EXPIRES_DAYS:
        # datetime.timedelta overflows past year 9999, which used to escape
        # main()'s handler as an OverflowError traceback.
        raise argparse.ArgumentTypeError(
            f"must be at most {_MAX_EXPIRES_DAYS} days (about 100 years); "
            "use 0 for a key that never expires"
        )
    return days


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
    p_init.add_argument(
        "--key-id",
        default=None,
        type=_key_id,
        help="Key identifier (default: locus-YYYY-MM-DD).",
    )
    p_init.add_argument(
        "--expires-days",
        type=_expires_days,
        default=365,
        help="Days until the key expires; 0 means never (default: 365).",
    )
    p_init.add_argument("--force", action="store_true", help="Overwrite an existing active keypair.")

    add_palace(sub.add_parser("sign-all", help="Sign every markdown file in the palace."))
    add_palace(sub.add_parser("verify-all", help="Verify every markdown file; exit 1 on any failure."))
    p_rotate = sub.add_parser("rotate-keys", help="Retire the active key and generate a new one.")
    add_palace(p_rotate)
    p_rotate.add_argument(
        "--expires-days",
        type=_expires_days,
        default=365,
        help="Days until the new key expires; 0 means never (default: 365).",
    )
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
        return cmd_rotate_keys(palace, args.expires_days)
    except (CliError, OSError, ValueError) as exc:
        # ValueError covers KeyLoadError (bad or missing passphrase) and the
        # signing module's own validation; OSError covers a missing or
        # unreadable key store.  Neither carries key material in its message.
        print(f"locus-security: {exc}", file=sys.stderr)
        return 1


def cli() -> None:
    sys.exit(main())


if __name__ == "__main__":
    cli()
