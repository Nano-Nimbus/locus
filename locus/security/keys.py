"""Ed25519 keypair generation, storage, and rotation for Locus signing."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

log = logging.getLogger("locus.security.keys")

# Key material is written user-only; the store directory is user-only too so a
# stray umask cannot leave a private key group- or world-readable.
_KEY_FILE_MODE = 0o600
_KEY_DIR_MODE = 0o700


class KeyLoadError(ValueError):
    """A keystore on disk could not be loaded.

    Subclasses ``ValueError`` so existing callers that catch ``ValueError``
    keep working.  The message never contains key material or the
    passphrase, only the store path and what the operator should do.
    """


@dataclass
class KeyPair:
    key_id: str
    private_key_bytes: bytes | None  # None for retired/public-only entries
    public_key_bytes: bytes
    created_at: str
    expires_at: str | None = None

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc).isoformat() > self.expires_at

    def public_key_pem(self) -> str:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_der_public_key
        pub = load_der_public_key(self.public_key_bytes)
        return pub.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()


@dataclass
class KeyStore:
    active: KeyPair
    retired: list[KeyPair] = field(default_factory=list)
    store_path: Path | None = None

    def find_by_id(self, key_id: str) -> KeyPair | None:
        if self.active.key_id == key_id:
            return self.active
        for kp in self.retired:
            if kp.key_id == key_id:
                return kp
        return None


def _passphrase() -> bytes | None:
    """Read LOCUS_SIGNING_PASSPHRASE env var. Returns None if unset (unencrypted key)."""
    val = os.environ.get("LOCUS_SIGNING_PASSPHRASE")
    return val.encode() if val else None


def default_key_id(now: datetime | None = None) -> str:
    """The date-stamped base id used when no ``--key-id`` is given."""
    return f"locus-{(now or datetime.now(timezone.utc)).strftime('%Y-%m-%d')}"


def existing_key_ids(store_path: Path) -> set[str]:
    """Every key id the store already knows about, active and retired.

    Retired entries are named ``<key_id>.pub``, so the file stem is the id
    even when the sidecar JSON is missing or unreadable.
    """
    ids: set[str] = set()
    active_meta = store_path / "active.json"
    if active_meta.is_file():
        try:
            meta = json.loads(active_meta.read_text())
        except (OSError, json.JSONDecodeError):
            meta = {}
        key_id = meta.get("key_id")
        if key_id:
            ids.add(str(key_id))
    retired_dir = store_path / "retired"
    if retired_dir.is_dir():
        for pub_file in retired_dir.glob("*.pub"):
            ids.add(pub_file.stem)
    return ids


def unique_key_id(base: str, taken: set[str]) -> str:
    """Return ``base``, or ``base-2`` / ``base-3`` ... if it is already taken.

    Key ids double as the retired archive filename and as the lookup key in
    :meth:`KeyStore.find_by_id`, which prefers the active key.  Two keys
    sharing an id therefore both loses the retired public key (same filename)
    and makes every signature from the older key fail verification, so ids
    must be unique within a store.
    """
    if base not in taken:
        return base
    counter = 2
    while f"{base}-{counter}" in taken:
        counter += 1
    return f"{base}-{counter}"


def generate_keypair(
    key_id: str | None = None,
    expires_days: int | None = 365,
) -> KeyPair:
    """Generate a new Ed25519 keypair."""
    if key_id is None:
        key_id = default_key_id()

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_bytes = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    expires_at = None
    if expires_days is not None:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=expires_days)
        ).isoformat(timespec="seconds")

    return KeyPair(
        key_id=key_id,
        private_key_bytes=private_bytes,
        public_key_bytes=public_bytes,
        created_at=created_at,
        expires_at=expires_at,
    )


def _secure_dir(path: Path) -> None:
    """Create ``path`` if needed and make it user-only."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(_KEY_DIR_MODE)
    except OSError as exc:  # pragma: no cover - platform dependent
        log.warning("could not tighten permissions on %s: %s", path, exc)


def _atomic_write(path: Path, data: bytes, mode: int = _KEY_FILE_MODE) -> None:
    """Write ``data`` to ``path`` atomically with an explicit file mode.

    ``NamedTemporaryFile`` already creates 0600 files, but the mode is set
    explicitly so the guarantee survives a future refactor, and the temp file
    is removed if anything fails before the rename.
    """
    _secure_dir(path.parent)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.chmod(tmp_path, mode)
        tmp_path.replace(path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def save_keypair(keypair: KeyPair, store_path: Path) -> None:
    """Write keypair to store_path/active.pem (PKCS8) and active.pub (DER public).

    If LOCUS_SIGNING_PASSPHRASE is set, the private key PEM is encrypted with
    BestAvailableEncryption (AES-256-CBC). Otherwise it is stored unencrypted.
    The public key is always written as unencrypted DER.
    """
    _secure_dir(store_path)
    passphrase = _passphrase()
    private_key = Ed25519PrivateKey.from_private_bytes(keypair.private_key_bytes)
    if passphrase:
        enc = serialization.BestAvailableEncryption(passphrase)
    else:
        enc = serialization.NoEncryption()

    # PKCS8 is the standard format for PEM-encoded private keys and supports
    # optional passphrase encryption via BestAvailableEncryption.
    pem_bytes = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        enc,
    )
    _atomic_write(store_path / "active.pem", pem_bytes)

    # Public key: DER, unencrypted
    _atomic_write(store_path / "active.pub", keypair.public_key_bytes)

    # Metadata
    meta = {
        "key_id": keypair.key_id,
        "created_at": keypair.created_at,
        "expires_at": keypair.expires_at,
    }
    _atomic_write(store_path / "active.json", json.dumps(meta, indent=2).encode())
    log.info("saved keypair %s to %s", keypair.key_id, store_path)


def load_keystore(store_path: Path) -> KeyStore:
    """Load the active keypair and any retired public keys from store_path."""
    active_pem = store_path / "active.pem"
    active_pub = store_path / "active.pub"
    active_meta = store_path / "active.json"

    if not active_pem.exists() or not active_pub.exists():
        raise FileNotFoundError(
            f"No active keypair found in {store_path}. "
            "Run: locus-security init-keys --palace <path>"
        )

    passphrase = _passphrase()
    try:
        private_key = serialization.load_pem_private_key(
            active_pem.read_bytes(), password=passphrase
        )
    except TypeError as exc:
        # cryptography raises TypeError for the two "wrong shape" cases:
        # an encrypted key loaded without a password, and a plaintext key
        # loaded with one.  Neither is a bug in Locus, both are operator
        # error, so report them as such instead of a traceback.
        if passphrase is None:
            raise KeyLoadError(
                f"The private key in {active_pem} is encrypted but "
                "LOCUS_SIGNING_PASSPHRASE is not set. Export the passphrase "
                "used at init-keys time and retry."
            ) from exc
        raise KeyLoadError(
            f"The private key in {active_pem} is not encrypted, but "
            "LOCUS_SIGNING_PASSPHRASE is set. Unset it, or re-create the "
            "keypair with the passphrase exported."
        ) from exc
    except ValueError as exc:
        raise KeyLoadError(
            f"Could not decrypt the private key in {active_pem}. "
            "LOCUS_SIGNING_PASSPHRASE does not match the passphrase used at "
            "init-keys time."
        ) from exc
    private_bytes = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_bytes = active_pub.read_bytes()

    meta: dict = {}
    if active_meta.exists():
        meta = json.loads(active_meta.read_text())

    active = KeyPair(
        key_id=meta.get("key_id", "unknown"),
        private_key_bytes=private_bytes,
        public_key_bytes=public_bytes,
        created_at=meta.get("created_at", ""),
        expires_at=meta.get("expires_at"),
    )

    # Load retired public keys
    retired: list[KeyPair] = []
    retired_dir = store_path / "retired"
    if retired_dir.is_dir():
        for pub_file in sorted(retired_dir.glob("*.pub")):
            meta_file = pub_file.with_suffix(".json")
            ret_meta: dict = {}
            if meta_file.exists():
                ret_meta = json.loads(meta_file.read_text())
            retired.append(
                KeyPair(
                    key_id=ret_meta.get("key_id", pub_file.stem),
                    private_key_bytes=None,
                    public_key_bytes=pub_file.read_bytes(),
                    created_at=ret_meta.get("created_at", ""),
                    expires_at=ret_meta.get("expires_at"),
                )
            )

    return KeyStore(active=active, retired=retired, store_path=store_path)


def rotate_keypair(
    store: KeyStore,
    store_path: Path,
    expires_days: int | None = 365,
) -> KeyPair:
    """Retire the current active key and generate a new one.

    Ordering matters: the outgoing public key and its metadata are archived
    under ``retired/`` *before* ``active.pem`` is overwritten, so a crash
    mid-rotation can never leave signatures made by the old key unverifiable.

    The new key is given an id that is unique within the store.  Without that,
    two rotations on the same day both default to ``locus-YYYY-MM-DD``, the
    second archive overwrites the first, and :meth:`KeyStore.find_by_id`
    resolves the shared id to the *active* key, so every signature made with
    the retired key fails verification.
    """
    retired_dir = store_path / "retired"
    _secure_dir(retired_dir)

    # Archive current public key and metadata only (never retain private key after rotation)
    _atomic_write(
        retired_dir / f"{store.active.key_id}.pub",
        store.active.public_key_bytes,
    )
    meta = {
        "key_id": store.active.key_id,
        "created_at": store.active.created_at,
        "expires_at": store.active.expires_at,
    }
    _atomic_write(
        retired_dir / f"{store.active.key_id}.json",
        json.dumps(meta, indent=2).encode(),
    )

    taken = existing_key_ids(store_path)
    taken.add(store.active.key_id)
    taken.update(kp.key_id for kp in store.retired)
    new_keypair = generate_keypair(
        key_id=unique_key_id(default_key_id(), taken),
        expires_days=expires_days,
    )
    save_keypair(new_keypair, store_path)

    # Read the store back before reporting success: a half-written active.pem
    # or a stale active.json would otherwise surface much later, as signing
    # failures against a key nobody can load.
    reloaded = load_keystore(store_path)
    if reloaded.active.key_id != new_keypair.key_id:
        raise KeyLoadError(
            f"Rotation left {store_path} inconsistent: active key reads back as "
            f"{reloaded.active.key_id!r}, expected {new_keypair.key_id!r}. "
            f"The retired public key for {store.active.key_id} is intact under "
            f"{retired_dir}."
        )
    if reloaded.active.public_key_bytes != new_keypair.public_key_bytes:
        raise KeyLoadError(
            f"Rotation left {store_path} inconsistent: the stored public key "
            "does not match the newly generated private key."
        )

    log.info("rotated key: %s to %s", store.active.key_id, new_keypair.key_id)
    return new_keypair
