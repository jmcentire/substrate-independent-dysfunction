"""
Core cryptographic primitives for the Anonymous Identity system.

This module provides all cryptographic building blocks needed for
privacy-preserving authentication and communication:

  - **credential_hash**: Hashes username||password so the plaintext password
    never leaves the client.
  - **derive_site_token**: Produces a deterministic, site-specific
    verification hash that is unlinkable across sites.
  - **derive_routing_key**: Produces a deterministic routing key with a
    domain separator so the SSO can deliver messages without learning
    site identities.
  - **Ed25519 key generation / signing / verification**: Used by the client
    to sign tokens that sites can verify.

Design choices
--------------
* SHA-256 via ``hashlib`` for all hashing (deterministic, pure).
* ``cryptography`` library for Ed25519 (FIPS-friendly, well-audited).
* All hash inputs are length-prefixed to prevent ambiguous concatenation
  (e.g. "ab"+"c" vs "a"+"bc").  Each component is encoded as
  ``<4-byte big-endian length><utf-8 bytes>``.
* Every function (except ``generate_ed25519_keypair``) is **pure and
  deterministic** — same inputs always produce the same output.

Usage
-----
    >>> from src.crypto import credential_hash, derive_site_token
    >>> cred = credential_hash("alice", "hunter2")
    >>> token = derive_site_token("alice", "example.com", "uid-42", "random-salt")
"""

from __future__ import annotations

import hashlib
from typing import Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _length_prefix(data: str) -> bytes:
    """Return *data* as UTF-8 bytes preceded by a 4-byte big-endian length.

    Length-prefixing prevents domain-separation ambiguities when
    concatenating variable-length strings (e.g. ``"ab"+"c"`` vs
    ``"a"+"bc"``).

    Args:
        data: An arbitrary Unicode string.

    Returns:
        ``len(encoded).to_bytes(4, 'big') + encoded``
    """
    encoded = data.encode("utf-8")
    return len(encoded).to_bytes(4, "big") + encoded


def _sha256_hex(*parts: str) -> str:
    """SHA-256 hash of length-prefixed concatenation of *parts*.

    Each element of *parts* is individually length-prefixed before
    being fed into the hasher, so the mapping from input tuple to
    digest is injective (no collisions from regrouping).

    Args:
        *parts: Arbitrary strings to hash.

    Returns:
        Lowercase hex-encoded SHA-256 digest (64 characters).
    """
    h = hashlib.sha256()
    for p in parts:
        h.update(_length_prefix(p))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Credential hashing
# ---------------------------------------------------------------------------

def credential_hash(username: str, password: str) -> str:
    """Compute ``SHA-256(username || password)`` for SSO authentication.

    The client computes this locally so the SSO **never** sees the
    plaintext password.  Only the resulting hash is transmitted.

    Args:
        username: The user's login name.
        password: The user's plaintext password (never transmitted).

    Returns:
        Hex-encoded SHA-256 digest (64 characters).

    Example:
        >>> credential_hash("alice", "hunter2")  # doctest: +SKIP
        'a1b2c3...'
    """
    return _sha256_hex(username, password)


# ---------------------------------------------------------------------------
# Site-specific token derivation
# ---------------------------------------------------------------------------

def derive_site_token(
    username: str,
    site_id: str,
    user_id: str,
    user_salt: str,
) -> str:
    """Derive a site-specific verification hash (client-side only).

    ``token = SHA-256(username || site_id || user_id || user_salt)``

    Because ``site_id`` is mixed in and the SSO never learns it, the
    SSO cannot compute tokens.  Because ``user_salt`` is mixed in and
    sites never learn it, one site cannot compute tokens for another
    site.  Tokens for different sites are therefore **unlinkable**.

    Args:
        username:  The user's login name (known only to the client).
        site_id:   The target site's unique identifier.
        user_id:   Opaque user identifier issued by the SSO.
        user_salt: Random salt issued by the SSO during authentication.

    Returns:
        Hex-encoded SHA-256 digest used as the persistent site-local
        identifier (``verification_hash``).
    """
    return _sha256_hex(username, site_id, user_id, user_salt)


# ---------------------------------------------------------------------------
# Routing key derivation
# ---------------------------------------------------------------------------

_ROUTING_DOMAIN_SEPARATOR: str = "routing"


def derive_routing_key(
    username: str,
    site_id: str,
    user_id: str,
    user_salt: str,
) -> str:
    """Derive a routing key so the SSO can deliver messages.

    ``routing_key = SHA-256(username || "routing" || site_id || user_id || user_salt)``

    The ``"routing"`` domain separator ensures that routing keys live
    in a different hash domain than site tokens, even when all other
    inputs are identical.

    Args:
        username:  The user's login name.
        site_id:   The site that is sending the message.
        user_id:   Opaque user identifier from the SSO.
        user_salt: Random salt from the SSO.

    Returns:
        Hex-encoded SHA-256 routing key (64 characters).
    """
    return _sha256_hex(
        username,
        _ROUTING_DOMAIN_SEPARATOR,
        site_id,
        user_id,
        user_salt,
    )


# ---------------------------------------------------------------------------
# Ed25519 key management, signing, and verification
# ---------------------------------------------------------------------------

def generate_ed25519_keypair() -> Tuple[bytes, bytes]:
    """Generate a fresh Ed25519 private/public key pair.

    **Non-deterministic** — each call produces a new random key pair.

    Returns:
        A tuple ``(private_key_bytes, public_key_bytes)`` where both
        values are raw 32-byte keys (suitable for storage or
        transmission).
    """
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_bytes, public_bytes


def ed25519_sign(private_key_bytes: bytes, message: bytes) -> bytes:
    """Sign *message* with an Ed25519 private key.

    Deterministic: the same ``(key, message)`` pair always produces the
    same 64-byte signature (Ed25519 is inherently deterministic).

    Args:
        private_key_bytes: 32-byte raw Ed25519 private key.
        message:           Arbitrary bytes to sign.

    Returns:
        64-byte Ed25519 signature.

    Raises:
        ValueError: If the key is not exactly 32 bytes.
    """
    if len(private_key_bytes) != 32:
        raise ValueError(
            f"Ed25519 private key must be 32 bytes, got {len(private_key_bytes)}"
        )
    private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    return private_key.sign(message)


def ed25519_verify(
    public_key_bytes: bytes,
    message: bytes,
    signature: bytes,
) -> bool:
    """Verify an Ed25519 signature.

    Args:
        public_key_bytes: 32-byte raw Ed25519 public key.
        message:          The original signed payload.
        signature:        64-byte Ed25519 signature to check.

    Returns:
        ``True`` if the signature is valid, ``False`` otherwise.

    Raises:
        ValueError: If the public key is not exactly 32 bytes.
    """
    if len(public_key_bytes) != 32:
        raise ValueError(
            f"Ed25519 public key must be 32 bytes, got {len(public_key_bytes)}"
        )
    public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
    try:
        public_key.verify(signature, message)
        return True
    except InvalidSignature:
        return False


def ed25519_public_key_from_private(private_key_bytes: bytes) -> bytes:
    """Derive the public key from a raw 32-byte Ed25519 private key.

    Deterministic: always returns the same public key for a given
    private key.

    Args:
        private_key_bytes: 32-byte raw Ed25519 private key.

    Returns:
        32-byte raw Ed25519 public key.

    Raises:
        ValueError: If the key is not exactly 32 bytes.
    """
    if len(private_key_bytes) != 32:
        raise ValueError(
            f"Ed25519 private key must be 32 bytes, got {len(private_key_bytes)}"
        )
    private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
