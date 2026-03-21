"""
Token data structure and construction/verification module.

This module implements the Token that a client presents to a site in
the Anonymous Identity system.  The token proves that the client has
authenticated (via the SSO) and provides a **site-specific**
verification hash as the user's persistent local identifier — without
revealing the user's real identity or enabling cross-site correlation.

Workflow
--------
1. The client authenticates to the SSO and receives ``user_id`` and
   ``user_salt``.
2. For each site the client visits, it calls :func:`create_token` which:
   - derives a ``verification_hash`` via :func:`crypto.derive_site_token`
   - records the current UTC timestamp
   - signs the payload ``(verification_hash || site_id || timestamp ||
     proof_of_human_score)`` with Ed25519
3. The site calls :func:`verify_token` with the client's public key
   to confirm the signature is valid.
4. :func:`serialize_token` / :func:`deserialize_token` enable JSON
   round-tripping for network transmission and storage.

Dependencies
------------
- ``src.crypto``: provides ``derive_site_token``, ``ed25519_sign``,
  ``ed25519_verify`` (see ``src/crypto.py``).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from typing import Optional

# Crypto primitives from the sibling module.
# Expected interface:
#   derive_site_token(username, site_id, user_id, user_salt) -> str (hex)
#   ed25519_sign(private_key_bytes: bytes, message: bytes) -> bytes (64-byte sig)
#   ed25519_verify(public_key_bytes: bytes, message: bytes, signature: bytes) -> bool
from src.crypto import derive_site_token, ed25519_sign, ed25519_verify


@dataclass
class Token:
    """Signed anonymous identity token presented by a client to a site.

    Attributes:
        verification_hash:    Site-specific user identifier derived from
                              ``hash(username || site_id || user_id || user_salt)``.
                              Hex-encoded, 64 characters.
        site_id:              Unique identifier of the target site.
        timestamp:            Token creation time as a UTC Unix timestamp
                              (float, seconds since epoch).
        proof_of_human_score: A ``[0, 1]`` float indicating confidence
                              the presenter is human (e.g. CAPTCHA score).
        signature:            Ed25519 signature over the canonical payload,
                              hex-encoded (128 hex characters / 64 bytes).
    """

    verification_hash: str
    site_id: str
    timestamp: float
    proof_of_human_score: float
    signature: str  # hex-encoded 64-byte Ed25519 signature


def _build_payload(
    verification_hash: str,
    site_id: str,
    timestamp: float,
    proof_of_human_score: float,
) -> bytes:
    """Construct the canonical byte payload that is signed / verified.

    The payload is the UTF-8 encoding of the four fields concatenated
    with ``||`` as a delimiter.  Using a fixed representation (``repr``
    for the float fields) ensures deterministic round-tripping.

    Args:
        verification_hash:    Hex string.
        site_id:              Site identifier string.
        timestamp:            UTC Unix timestamp (float).
        proof_of_human_score: Human-ness score (float).

    Returns:
        Canonical UTF-8 bytes ready for signing or verification.
    """
    canonical = "||".join([
        verification_hash,
        site_id,
        repr(timestamp),
        repr(proof_of_human_score),
    ])
    return canonical.encode("utf-8")


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------

def create_token(
    username: str,
    site_id: str,
    user_id: str,
    user_salt: str,
    signing_key: bytes,
    proof_of_human_score: float,
    *,
    _timestamp: Optional[float] = None,
) -> Token:
    """Create a signed anonymous identity token for a specific site.

    Steps:
      1. Derive ``verification_hash`` via :func:`crypto.derive_site_token`.
      2. Record the current UTC time (or use ``_timestamp`` for testing).
      3. Build the canonical payload and sign it with Ed25519.
      4. Return a :class:`Token` with all fields populated.

    Args:
        username:             The user's login name (client-side only).
        site_id:              The site the token is destined for.
        user_id:              Opaque user id returned by the SSO.
        user_salt:            Random salt returned by the SSO.
        signing_key:          32-byte raw Ed25519 private key.
        proof_of_human_score: Float in ``[0, 1]``.
        _timestamp:           *Testing only* — override the UTC timestamp.

    Returns:
        A fully populated and signed :class:`Token`.

    Raises:
        ValueError: If ``signing_key`` is not 32 bytes or score is out
                    of range.
    """
    if not (0.0 <= proof_of_human_score <= 1.0):
        raise ValueError(
            f"proof_of_human_score must be in [0, 1], got {proof_of_human_score}"
        )

    verification_hash = derive_site_token(username, site_id, user_id, user_salt)
    timestamp = _timestamp if _timestamp is not None else time.time()

    payload = _build_payload(verification_hash, site_id, timestamp, proof_of_human_score)
    signature_bytes = ed25519_sign(signing_key, payload)

    return Token(
        verification_hash=verification_hash,
        site_id=site_id,
        timestamp=timestamp,
        proof_of_human_score=proof_of_human_score,
        signature=signature_bytes.hex(),
    )


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

def verify_token(token: Token, public_key: bytes) -> bool:
    """Verify the Ed25519 signature on a :class:`Token`.

    Rebuilds the canonical payload from the token's fields and checks
    the signature against the supplied public key.

    Args:
        token:      The :class:`Token` to verify.
        public_key: 32-byte raw Ed25519 public key of the signer.

    Returns:
        ``True`` if the signature is valid, ``False`` otherwise.
    """
    payload = _build_payload(
        token.verification_hash,
        token.site_id,
        token.timestamp,
        token.proof_of_human_score,
    )
    signature_bytes = bytes.fromhex(token.signature)
    return ed25519_verify(public_key, payload, signature_bytes)


# ---------------------------------------------------------------------------
# Serialization / deserialization (JSON)
# ---------------------------------------------------------------------------

def serialize_token(token: Token) -> str:
    """Serialize a :class:`Token` to a JSON string.

    All fields are stored as their natural JSON types (strings and
    numbers).  The signature is kept as a hex string.

    Args:
        token: The :class:`Token` to serialize.

    Returns:
        A JSON string representation.
    """
    return json.dumps(asdict(token), sort_keys=True)


def deserialize_token(data: str) -> Token:
    """Deserialize a JSON string back into a :class:`Token`.

    Args:
        data: A JSON string produced by :func:`serialize_token`.

    Returns:
        A :class:`Token` instance.

    Raises:
        json.JSONDecodeError: If *data* is not valid JSON.
        KeyError / TypeError: If required fields are missing or malformed.
    """
    d = json.loads(data)
    return Token(
        verification_hash=str(d["verification_hash"]),
        site_id=str(d["site_id"]),
        timestamp=float(d["timestamp"]),
        proof_of_human_score=float(d["proof_of_human_score"]),
        signature=str(d["signature"]),
    )
