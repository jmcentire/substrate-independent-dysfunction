"""Cryptographic utilities for the Anonymous Identity system."""

import hashlib
import time
import struct

from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import BadSignatureError


def compute_credential_hash(username: str, password: str) -> str:
    """Compute hash(username || password) locally. Only the hash is transmitted."""
    data = (username + password).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def derive_site_token(username: str, site_id: str, user_id: str, user_salt: str) -> str:
    """Derive a site-specific token entirely client-side.

    token = hash(username || site_id || user_id || user_salt)
    """
    data = (username + site_id + user_id + user_salt).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def derive_verification_hash(username: str, site_id: str, user_id: str, user_salt: str) -> str:
    """Derive a verification hash used as the persistent site-local identifier.

    This is a secondary hash over the site token to serve as the public identifier
    that goes into the signed token structure.
    """
    site_token = derive_site_token(username, site_id, user_id, user_salt)
    data = ("verify" + site_token).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def derive_routing_key(username: str, site_id: str, user_id: str, user_salt: str) -> str:
    """Derive a routing key for message delivery.

    routing_key = hash(username || "routing" || site_id || user_id || user_salt)
    """
    data = (username + "routing" + site_id + user_id + user_salt).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def build_signed_token(
    username: str,
    site_id: str,
    user_id: str,
    user_salt: str,
    proof_of_human_score: float = 1.0,
    signing_key: SigningKey | None = None,
) -> tuple[bytes, VerifyKey]:
    """Build and sign a token T containing (verification_hash, site_id, timestamp, proof_of_human_score).

    Returns (signed_token_bytes, verify_key).
    The signed bytes encode the token fields; the verify key lets sites check the signature.
    """
    if signing_key is None:
        signing_key = SigningKey.generate()

    verification_hash = derive_verification_hash(username, site_id, user_id, user_salt)
    timestamp = time.time()

    # Build a deterministic payload: verification_hash | site_id | timestamp | score
    payload = "\n".join([
        verification_hash,
        site_id,
        f"{timestamp:.6f}",
        f"{proof_of_human_score:.4f}",
    ]).encode("utf-8")

    signed = signing_key.sign(payload)
    return (signed, signing_key.verify_key)


def verify_signed_token(signed_token: bytes, verify_key: VerifyKey) -> dict:
    """Verify a signed token and return the decoded fields.

    Raises nacl.exceptions.BadSignatureError if verification fails.
    Returns dict with keys: verification_hash, site_id, timestamp, proof_of_human_score.
    """
    payload = verify_key.verify(signed_token)
    parts = payload.decode("utf-8").split("\n")
    return {
        "verification_hash": parts[0],
        "site_id": parts[1],
        "timestamp": float(parts[2]),
        "proof_of_human_score": float(parts[3]),
    }
