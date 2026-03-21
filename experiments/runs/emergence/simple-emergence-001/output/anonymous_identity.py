"""
Anonymous Identity System — Core Crypto Module

Provides primitives for:
  - credential_hash: deterministic hash of (username, password)
  - site_specific_token: unlinkable per-site identity derived from
        (username, site_id, user_id, user_salt)
  - verification_hash: hash published on-site so the site can verify
        the token holder without learning the underlying identity
  - routing_key: deterministic key that the SSO can map to a user
        for message delivery, without revealing site-specific tokens
  - Ed25519 signing / verification of structured Token objects
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import asdict, dataclass
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Optional: use the pure-python fallback shipped with newer Pythons.
# We try the fast C bindings first.
# ---------------------------------------------------------------------------
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    _HAS_CRYPTOGRAPHY = True
except ImportError:  # pragma: no cover – fallback handled below
    _HAS_CRYPTOGRAPHY = False

# If `cryptography` is not installed we fall back to PyNaCl
if not _HAS_CRYPTOGRAPHY:
    try:
        import nacl.signing  # type: ignore

        _HAS_NACL = True
    except ImportError:
        _HAS_NACL = False
else:
    _HAS_NACL = False

# =========================================================================
# 1. Credential hash
# =========================================================================

def credential_hash(username: str, password: str) -> str:
    """Return hex-encoded SHA-256 of (username || ":" || password).

    This is the *only* value the SSO server ever sees — it cannot
    recover username or password from this hash.
    """
    data = f"{username}:{password}".encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# =========================================================================
# 2. Site-specific token
# =========================================================================

def site_specific_token(
    username: str,
    site_id: str,
    user_id: str,
    user_salt: str,
) -> str:
    """Derive an unlinkable site-specific token.

    token = HMAC-SHA256(key=user_salt, msg=username||site_id||user_id)

    Because *user_salt* is secret (known only to the SSO and the user)
    and the derivation includes *site_id*, two tokens for the same user
    on different sites are computationally unlinkable.
    """
    msg = f"{username}||{site_id}||{user_id}".encode("utf-8")
    return hmac.new(
        user_salt.encode("utf-8"),
        msg,
        hashlib.sha256,
    ).hexdigest()


# =========================================================================
# 3. Verification hash
# =========================================================================

def verification_hash(site_token: str) -> str:
    """One-way hash of a site-specific token.

    Published on the site so it can verify the token holder without
    being able to reverse the token itself.
    """
    return hashlib.sha256(site_token.encode("utf-8")).hexdigest()


# =========================================================================
# 4. Routing key
# =========================================================================

def routing_key(
    username: str,
    site_id: str,
    user_id: str,
    user_salt: str,
) -> str:
    """Derive a routing key:

        hash(username || "routing" || site_id || user_id || user_salt)

    The SSO stores a mapping  routing_key → user_id  so it can deliver
    encrypted messages without learning the site-specific token.
    """
    data = f"{username}||routing||{site_id}||{user_id}||{user_salt}".encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# =========================================================================
# 5. Ed25519 key-pair helpers
# =========================================================================

class SigningKeyPair:
    """Thin wrapper around Ed25519 key operations.

    Supports both `cryptography` and `PyNaCl` backends transparently.
    """

    def __init__(self) -> None:
        if _HAS_CRYPTOGRAPHY:
            self._backend = "cryptography"
            self._sk = Ed25519PrivateKey.generate()
            self._pk = self._sk.public_key()
        elif _HAS_NACL:
            self._backend = "nacl"
            self._sk = nacl.signing.SigningKey.generate()
            self._pk = self._sk.verify_key
        else:
            raise RuntimeError(
                "Either 'cryptography' or 'PyNaCl' is required for Ed25519 support."
            )

    # -- public key bytes ---------------------------------------------------
    def public_key_bytes(self) -> bytes:
        if self._backend == "cryptography":
            return self._pk.public_bytes(Encoding.Raw, PublicFormat.Raw)
        return bytes(self._pk)

    # -- sign / verify ------------------------------------------------------
    def sign(self, data: bytes) -> bytes:
        if self._backend == "cryptography":
            return self._sk.sign(data)
        signed = self._sk.sign(data)
        return signed.signature  # first 64 bytes

    def verify(self, signature: bytes, data: bytes) -> bool:
        """Return True when signature is valid, False otherwise."""
        try:
            if self._backend == "cryptography":
                self._pk.verify(signature, data)
            else:
                self._pk.verify(data, signature)
            return True
        except Exception:
            return False

    # -- class-level verify from raw public key bytes -----------------------
    @staticmethod
    def verify_with_public_key(
        public_key_bytes_raw: bytes,
        signature: bytes,
        data: bytes,
    ) -> bool:
        try:
            if _HAS_CRYPTOGRAPHY:
                from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                    Ed25519PublicKey as _PK,
                )

                pk = _PK.from_public_key_bytes(public_key_bytes_raw)
                pk.verify(signature, data)
            elif _HAS_NACL:
                vk = nacl.signing.VerifyKey(public_key_bytes_raw)
                vk.verify(data, signature)
            else:
                raise RuntimeError("No Ed25519 backend available.")
            return True
        except Exception:
            return False


# =========================================================================
# 6. Token dataclass
# =========================================================================

@dataclass
class Token:
    """Structured identity token presented to a site.

    Fields
    ------
    verification_hash : str
        SHA-256 of the site-specific token (the site stores this).
    site_id : str
        Identifier of the relying site.
    timestamp : float
        Unix epoch when the token was created.
    proof_of_human_score : float
        Score in [0, 1] from a proof-of-human challenge.
    signature : Optional[bytes]
        Ed25519 signature over the canonical JSON of the other fields.
    public_key : Optional[bytes]
        Raw Ed25519 public key (32 bytes) of the signer.
    """

    verification_hash: str
    site_id: str
    timestamp: float
    proof_of_human_score: float
    signature: Optional[bytes] = None
    public_key: Optional[bytes] = None

    # -- canonical payload (excludes signature & public_key) ----------------
    def _canonical_payload(self) -> bytes:
        d = {
            "verification_hash": self.verification_hash,
            "site_id": self.site_id,
            "timestamp": self.timestamp,
            "proof_of_human_score": self.proof_of_human_score,
        }
        return json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def sign(self, keypair: SigningKeyPair) -> None:
        """Sign the token in-place."""
        payload = self._canonical_payload()
        self.signature = keypair.sign(payload)
        self.public_key = keypair.public_key_bytes()

    def verify_signature(self) -> bool:
        """Verify the embedded signature using the embedded public key."""
        if self.signature is None or self.public_key is None:
            return False
        payload = self._canonical_payload()
        return SigningKeyPair.verify_with_public_key(
            self.public_key, self.signature, payload
        )


# =========================================================================
# Convenience: build a fully signed token
# =========================================================================

def build_signed_token(
    username: str,
    site_id: str,
    user_id: str,
    user_salt: str,
    keypair: SigningKeyPair,
    proof_of_human_score: float = 1.0,
    timestamp: Optional[float] = None,
) -> Token:
    """High-level helper: derive token, build Token object, sign it."""
    st = site_specific_token(username, site_id, user_id, user_salt)
    vh = verification_hash(st)
    tok = Token(
        verification_hash=vh,
        site_id=site_id,
        timestamp=timestamp if timestamp is not None else time.time(),
        proof_of_human_score=proof_of_human_score,
    )
    tok.sign(keypair)
    return tok
