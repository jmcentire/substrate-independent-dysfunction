"""Client-side identity primitives for the Anonymous Identity system.

All site-specific derivations happen here, on the client.  The SSO never
learns *site_id*, and individual sites never learn *user_salt* or the
real username.
"""

import hashlib
import json
import time
from typing import Tuple

from nacl.signing import SigningKey, VerifyKey
from nacl.encoding import HexEncoder


# ---------------------------------------------------------------------------
# 1. Credential hash  (client → SSO)
# ---------------------------------------------------------------------------

def compute_credential_hash(username: str, password: str) -> str:
    """hash(username || password) — only this hash is ever sent to the SSO."""
    return hashlib.sha256((username + password).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 2. Site-specific token derivation  (client-side only)
# ---------------------------------------------------------------------------

def derive_site_token(username: str, site_id: str, user_id: str, user_salt: str) -> str:
    """token = hash(username || site_id || user_id || user_salt)

    Derived entirely client-side.  The SSO provides *user_id* and
    *user_salt* during authentication but never learns *site_id*.
    """
    data = (username + site_id + user_id + user_salt).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# 3. Verification hash  (the persistent site-local identifier)
# ---------------------------------------------------------------------------

def derive_verification_hash(site_token: str) -> str:
    """A second hash of the site token used as the public identifier inside
    signed tokens, so the raw site_token is never exposed directly."""
    return hashlib.sha256(site_token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 4. Signed token construction & verification  (Ed25519)
# ---------------------------------------------------------------------------

def build_signed_token(
    signing_key: SigningKey,
    verification_hash: str,
    site_id: str,
    proof_of_human_score: float = 1.0,
    timestamp: float | None = None,
) -> dict:
    """Build and sign a token T containing
    (verification_hash, site_id, timestamp, proof_of_human_score).

    Returns a dict with 'payload' (JSON str), 'signature' (hex), and
    'verify_key' (hex-encoded public key).
    """
    if timestamp is None:
        timestamp = time.time()

    payload = json.dumps(
        {
            "verification_hash": verification_hash,
            "site_id": site_id,
            "timestamp": timestamp,
            "proof_of_human_score": proof_of_human_score,
        },
        sort_keys=True,
    )

    signed = signing_key.sign(payload.encode("utf-8"), encoder=HexEncoder)
    signature_hex = signed.signature.decode("ascii")

    verify_key_hex = signing_key.verify_key.encode(encoder=HexEncoder).decode("ascii")

    return {
        "payload": payload,
        "signature": signature_hex,
        "verify_key": verify_key_hex,
    }


def verify_signed_token(token: dict) -> Tuple[bool, dict | None]:
    """Verify the Ed25519 signature on a token.

    Returns (True, parsed_payload) on success or (False, None) on failure.
    """
    try:
        vk = VerifyKey(token["verify_key"].encode("ascii"), encoder=HexEncoder)
        vk.verify(
            token["payload"].encode("utf-8"),
            bytes.fromhex(token["signature"]),
        )
        return True, json.loads(token["payload"])
    except Exception:
        return False, None


# ---------------------------------------------------------------------------
# 5. Routing key derivation  (client-side)
# ---------------------------------------------------------------------------

def derive_routing_key(
    username: str, site_id: str, user_id: str, user_salt: str
) -> str:
    """routing_key = hash(username || "routing" || site_id || user_id || user_salt)

    Sites address messages to this key.  The SSO resolves it to the
    user's contact info without ever revealing that info to the site.
    """
    data = (username + "routing" + site_id + user_id + user_salt).encode("utf-8")
    return hashlib.sha256(data).hexdigest()
