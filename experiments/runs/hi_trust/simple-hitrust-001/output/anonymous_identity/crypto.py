"""
crypto.py – Cryptographic primitives for the Anonymous Identity system.

Provides:
    - hash_concat(*parts)   → SHA-256 of the concatenation of string/bytes parts
    - generate_ed25519_keypair() → (signing_key, verify_key) via PyNaCl
"""

import hashlib
from nacl.signing import SigningKey, VerifyKey


def hash_concat(*parts: str | bytes) -> str:
    """
    SHA-256 hash of the concatenation of all parts.

    Each part is encoded to UTF-8 if it is a string.  The result is returned
    as a lowercase hex digest (64 hex chars).
    """
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, str):
            p = p.encode("utf-8")
        h.update(p)
    return h.hexdigest()


def generate_ed25519_keypair():
    """Return (SigningKey, VerifyKey) — a fresh Ed25519 key pair."""
    sk = SigningKey.generate()
    return sk, sk.verify_key
