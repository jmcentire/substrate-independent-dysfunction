"""Cryptographic utilities for the Anonymous Identity system.

All hashing uses SHA-256. Signing uses Ed25519 via PyNaCl.
"""

import hashlib
import json
import time
from typing import Tuple

from nacl.signing import SigningKey, VerifyKey
from nacl.encoding import HexEncoder


def _sha256(*parts: str) -> str:
    """Compute SHA-256 over the concatenation of string parts.

    Returns the hex digest.
    """
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
    return h.hexdigest()


# ── Property 1: Credential hash ──────────────────────────────────────────────

def hash_credential(username: str, password: str) -> str:
    """Compute hash(username || password) client-side.

    Only this hash is ever transmitted to the SSO.
    """
    return _sha256(username, password)


# ── Property 2: Site-specific token derivation ───────────────────────────────

def derive_site_token(username: str, site_id: str, user_id: str, user_salt: str) -> str:
    """Derive a site-specific verification hash entirely client-side.

    token = hash(username || site_id || user_id || user_salt)

    The SSO provides user_id and user_salt but never learns site_id.
    """
    return _sha256(username, site_id, user_id, user_salt)


# ── Property 5: Routing key derivation ───────────────────────────────────────

def derive_routing_key(username: str, site_id: str, user_id: str, user_salt: str) -> str:
    """Derive a routing key for a specific site.

    routing_key = hash(username || "routing" || site_id || user_id || user_salt)
    """
    return _sha256(username, "routing", site_id, user_id, user_salt)


# ── Property 4: Ed25519 signing ──────────────────────────────────────────────

def generate_ed25519_keypair() -> Tuple[SigningKey, VerifyKey]:
    """Generate a fresh Ed25519 signing/verification key pair."""
    sk = SigningKey.generate()
    return sk, sk.verify_key


def sign_token(
    signing_key: SigningKey,
    verification_hash: str,
    site_id: str,
    timestamp: float,
    proof_of_human_score: float,
) -> dict:
    """Construct and sign a token T.

    T contains (verification_hash, site_id, timestamp, proof_of_human_score).
    The client signs T with Ed25519.

    Returns a dict with the token payload, signature (hex), and public key (hex).
    """
    payload = {
        "verification_hash": verification_hash,
        "site_id": site_id,
        "timestamp": timestamp,
        "proof_of_human_score": proof_of_human_score,
    }
    # Canonical JSON serialisation for deterministic signing
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signed = signing_key.sign(payload_bytes, encoder=HexEncoder)
    return {
        "payload": payload,
        "signature": signed.signature.decode("ascii"),
        "public_key": signing_key.verify_key.encode(encoder=HexEncoder).decode("ascii"),
    }


def verify_token_signature(token: dict) -> dict:
    """Verify a signed token's Ed25519 signature.

    Returns the payload dict if valid.
    Raises nacl.exceptions.BadSignatureError on failure.
    """
    vk = VerifyKey(token["public_key"].encode("ascii"), encoder=HexEncoder)
    payload_bytes = json.dumps(
        token["payload"], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    signature_bytes = bytes.fromhex(token["signature"])
    # verify raises BadSignatureError on failure
    vk.verify(payload_bytes, signature_bytes)
    return token["payload"]
