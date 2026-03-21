"""
Core cryptographic primitives for the Anonymous Identity system.

Every derivation is a domain-separated HMAC-SHA256 or SHA-256.
Domain separation ensures that outputs from different functions are
independent even when fed overlapping inputs.
"""

import hashlib
import hmac
import time
import json
from nacl.signing import SigningKey, VerifyKey
from nacl.encoding import HexEncoder


# ---------------------------------------------------------------------------
# Property 1 – Credential hash (client-side only)
# ---------------------------------------------------------------------------

def compute_credential_hash(username: str, password: str) -> str:
    """
    hash(username || password).
    Only this hash is ever transmitted to the SSO.
    The SSO never sees the plaintext password.
    """
    data = (username + password).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Property 2 – Site-specific token derivation (client-side only)
# ---------------------------------------------------------------------------

def derive_site_token(username: str, site_id: str,
                      user_id: str, user_salt: str) -> str:
    """
    token = HMAC-SHA256(key=user_salt, msg="site_token" || username || site_id || user_id)

    Domain: "site_token"
    The SSO supplies user_id and user_salt during auth, but never learns
    which site_id the client plugs in.
    """
    msg = f"site_token|{username}|{site_id}|{user_id}".encode("utf-8")
    return hmac.new(user_salt.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def derive_verification_hash(site_token: str) -> str:
    """
    An additional hash of the site_token used as the persistent
    site-local identifier inside the signed token structure.
    This prevents the raw site_token (which encodes username) from
    being exposed in the clear.
    """
    return hashlib.sha256(
        f"verification|{site_token}".encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Property 5 – Routing key derivation (client-side only)
# ---------------------------------------------------------------------------

def derive_routing_key(username: str, site_id: str,
                       user_id: str, user_salt: str) -> str:
    """
    routing_key = HMAC-SHA256(key=user_salt,
                              msg="routing" || username || site_id || user_id)

    Domain: "routing"
    The routing key is given to the site so it can send messages via the
    SSO without learning the user's contact info.
    """
    msg = f"routing|{username}|{site_id}|{user_id}".encode("utf-8")
    return hmac.new(user_salt.encode("utf-8"), msg, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Property 4 – Token construction & Ed25519 signing
# ---------------------------------------------------------------------------

def generate_signing_keypair():
    """Return (SigningKey, VerifyKey) for Ed25519."""
    sk = SigningKey.generate()
    return sk, sk.verify_key


def build_token_payload(verification_hash: str, site_id: str,
                        timestamp: float, proof_of_human_score: float) -> bytes:
    """Canonical JSON serialisation of the token fields."""
    payload = {
        "verification_hash": verification_hash,
        "site_id": site_id,
        "timestamp": timestamp,
        "proof_of_human_score": proof_of_human_score,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_token(signing_key: SigningKey, verification_hash: str,
               site_id: str, timestamp: float,
               proof_of_human_score: float) -> dict:
    """
    The client signs T = (verification_hash, site_id, timestamp, proof_of_human_score)
    with its Ed25519 private key.

    Returns a dict with the payload fields plus 'signature' and 'public_key'.
    """
    payload_bytes = build_token_payload(
        verification_hash, site_id, timestamp, proof_of_human_score
    )
    signed = signing_key.sign(payload_bytes, encoder=HexEncoder)
    return {
        "verification_hash": verification_hash,
        "site_id": site_id,
        "timestamp": timestamp,
        "proof_of_human_score": proof_of_human_score,
        "signature": signed.signature.decode("ascii"),
        "public_key": signing_key.verify_key.encode(encoder=HexEncoder).decode("ascii"),
    }


def verify_token(token: dict) -> bool:
    """
    A site verifies the Ed25519 signature on the token.
    Returns True iff the signature is valid.
    """
    vk = VerifyKey(token["public_key"].encode("ascii"), encoder=HexEncoder)
    payload_bytes = build_token_payload(
        token["verification_hash"],
        token["site_id"],
        token["timestamp"],
        token["proof_of_human_score"],
    )
    signature = bytes.fromhex(token["signature"])
    try:
        vk.verify(payload_bytes, signature)
        return True
    except Exception:
        return False
