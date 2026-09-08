"""Ed25519 file signing and verification for Locus memory files."""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import load_der_public_key

from .keys import KeyPair, KeyStore

log = logging.getLogger("locus.security.signing")

_PROTOCOL = "locus-sig-v1"


@dataclass
class FileSignature:
    protocol: str
    key_id: str
    palace_slug: str
    rel_path: str
    content_sha256: str
    signed_at: str
    signature_b64: str


@dataclass
class VerificationResult:
    trusted: bool
    reason: str
    key_id: str | None = None
    signed_at: str | None = None
    age_seconds: float | None = None


def _slug_from_path(p: Path) -> str:
    return str(p).replace("/", "-")


def _normalize(content: str) -> bytes:
    """Normalize content: LF line endings, strip BOM, UTF-8 encode."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.lstrip("\ufeff").encode("utf-8")


def _content_sha256(content: str) -> str:
    return hashlib.sha256(_normalize(content)).hexdigest()


def _canonical_payload(
    protocol: str,
    palace_slug: str,
    rel_path: str,
    signed_at: str,
    content_sha256: str,
) -> bytes:
    return f"{protocol}\n{palace_slug}\n{rel_path}\n{signed_at}\n{content_sha256}".encode(
        "utf-8"
    )


def _sidecar_path(file_path: Path) -> Path:
    """Return the .sig/<filename>.sig path alongside the given file."""
    return file_path.parent / ".sig" / f"{file_path.name}.sig"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".tmp") as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def sign_file(path: Path, palace_root: Path, keypair: KeyPair) -> FileSignature:
    """Sign a memory file and write a sidecar .sig file.

    The file must exist and be readable. The sidecar is written atomically.
    """
    if keypair.private_key_bytes is None:
        raise ValueError(f"KeyPair {keypair.key_id!r} has no private key (retired key)")

    content = path.read_text(encoding="utf-8")
    sha256 = _content_sha256(content)
    rel_path = str(path.relative_to(palace_root))
    palace_slug = _slug_from_path(palace_root)
    signed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    payload = _canonical_payload(_PROTOCOL, palace_slug, rel_path, signed_at, sha256)

    private_key = Ed25519PrivateKey.from_private_bytes(keypair.private_key_bytes)
    raw_sig = private_key.sign(payload)
    sig_b64 = base64.urlsafe_b64encode(raw_sig).decode()

    sig = FileSignature(
        protocol=_PROTOCOL,
        key_id=keypair.key_id,
        palace_slug=palace_slug,
        rel_path=rel_path,
        content_sha256=sha256,
        signed_at=signed_at,
        signature_b64=sig_b64,
    )

    sidecar = _sidecar_path(path)
    sidecar_yaml = yaml.dump(
        {
            "protocol": sig.protocol,
            "key_id": sig.key_id,
            "palace_slug": sig.palace_slug,
            "rel_path": sig.rel_path,
            "content_sha256": sig.content_sha256,
            "signed_at": sig.signed_at,
            "signature_b64": sig.signature_b64,
        },
        default_flow_style=False,
        allow_unicode=True,
    )
    header = "# Locus signature sidecar — do not edit manually\n"
    _atomic_write(sidecar, (header + sidecar_yaml).encode("utf-8"))
    log.debug("signed %s (key=%s)", rel_path, keypair.key_id)
    return sig


def verify_file(path: Path, palace_root: Path, keystore: KeyStore) -> VerificationResult:
    """Verify the Ed25519 signature on a memory file.

    Returns a VerificationResult indicating trust status.
    """
    sidecar = _sidecar_path(path)
    if not sidecar.exists():
        return VerificationResult(trusted=False, reason="no sidecar")

    try:
        raw = yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return VerificationResult(trusted=False, reason=f"sidecar parse error: {exc}")
    if not isinstance(raw, dict):
        # A sidecar that parses to a scalar or a list used to raise
        # AttributeError on the next line, killing the whole verify run and
        # leaving every later file unchecked.
        return VerificationResult(
            trusted=False, reason=f"sidecar is not a mapping: {type(raw).__name__}"
        )

    key_id = raw.get("key_id")
    keypair = keystore.find_by_id(key_id) if key_id else None
    if keypair is None:
        return VerificationResult(
            trusted=False, reason=f"key not found: {key_id!r}", key_id=key_id
        )

    # Verify content hash matches
    try:
        current_content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # UnicodeDecodeError is a ValueError, not an OSError, so it used to
        # escape this guard and abort verify-all on the first binary file,
        # without naming it.
        return VerificationResult(trusted=False, reason="file is not valid UTF-8")
    except OSError as exc:
        return VerificationResult(trusted=False, reason=f"file unreadable: {exc}")

    current_sha256 = _content_sha256(current_content)
    stored_sha256 = raw.get("content_sha256", "")
    if current_sha256 != stored_sha256:
        return VerificationResult(
            trusted=False,
            reason="content tampered",
            key_id=key_id,
            signed_at=raw.get("signed_at"),
        )

    # The signature binds the file's path, so the payload has to be rebuilt
    # from where the file actually is.  Taking rel_path straight from the
    # sidecar meant the signature only attested "some file had this hash":
    # copying a signed low-value note plus its sidecar over INDEX.md verified
    # clean, which promotes attacker-chosen content into the trusted tier that
    # memory_read serves first.
    actual_rel_path = str(path.relative_to(palace_root))
    stored_rel_path = raw.get("rel_path")
    if stored_rel_path != actual_rel_path:
        return VerificationResult(
            trusted=False,
            reason=f"path mismatch: signed as {stored_rel_path!r}, found at {actual_rel_path!r}",
            key_id=key_id,
            signed_at=raw.get("signed_at"),
        )

    # palace_slug stays as recorded rather than recomputed: it is the absolute
    # host path of the palace, so comparing it would invalidate every
    # signature the moment a palace is moved or mounted at another prefix.
    # The path binding above is what stops relocation inside a palace.
    palace_slug = raw.get("palace_slug", _slug_from_path(palace_root))
    rel_path = actual_rel_path
    signed_at = raw.get("signed_at", "")
    sig_b64 = raw.get("signature_b64", "")

    payload = _canonical_payload(
        raw.get("protocol", _PROTOCOL),
        palace_slug,
        rel_path,
        signed_at,
        stored_sha256,
    )

    try:
        raw_sig = base64.urlsafe_b64decode(sig_b64 + "==")
    except (binascii.Error, TypeError, ValueError) as exc:
        return VerificationResult(
            trusted=False,
            reason=f"malformed signature encoding: {exc}",
            key_id=key_id,
            signed_at=signed_at,
        )
    try:
        pub_key: Ed25519PublicKey = load_der_public_key(keypair.public_key_bytes)
    except (TypeError, ValueError) as exc:
        return VerificationResult(
            trusted=False,
            reason=f"unusable public key for {key_id!r}: {exc}",
            key_id=key_id,
            signed_at=signed_at,
        )
    try:
        pub_key.verify(raw_sig, payload)
    except InvalidSignature:
        # InvalidSignature stringifies to nothing, so the old
        # f"signature invalid: {exc}" produced a message ending in a bare
        # colon.  It also used to swallow every other error under the same
        # wording, so a corrupt public key was indistinguishable from a forgery.
        return VerificationResult(
            trusted=False,
            reason="signature does not match this key and content",
            key_id=key_id,
            signed_at=signed_at,
        )

    age_seconds: float | None = None
    try:
        signed_dt = datetime.fromisoformat(signed_at)
        age_seconds = (datetime.now(timezone.utc) - signed_dt).total_seconds()
    except ValueError:
        pass

    return VerificationResult(
        trusted=True,
        reason="signature valid",
        key_id=key_id,
        signed_at=signed_at,
        age_seconds=age_seconds,
    )


def sign_system_prompt(prompt: str, keypair: KeyPair, nonce: str) -> tuple[str, str]:
    """Sign the system prompt + nonce. Returns (signature_b64, key_id)."""
    if keypair.private_key_bytes is None:
        raise ValueError(f"KeyPair {keypair.key_id!r} has no private key")

    payload = f"locus-sys-v1\n{nonce}\n{prompt}".encode("utf-8")
    private_key = Ed25519PrivateKey.from_private_bytes(keypair.private_key_bytes)
    raw_sig = private_key.sign(payload)
    sig_b64 = base64.urlsafe_b64encode(raw_sig).decode()
    return sig_b64, keypair.key_id
