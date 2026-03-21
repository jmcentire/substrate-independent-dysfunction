"""
client.py – Client-side logic for the Anonymous Identity system.

The Client holds the user's plaintext username (kept only in local memory),
the Ed25519 signing key, and — after authentication — the user_id and
user_salt received from the SSO.

All site-specific derivations happen here; no server ever sees site_id
combined with user credentials.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Optional

from nacl.signing import SigningKey, VerifyKey

from .crypto import hash_concat, generate_ed25519_keypair


@dataclass
class Client:
    """
    Represents a single end-user device.

    Usage:
        client = Client("alice", "hunter2")
        # Register with SSO
        user_id = sso.register(client.credential_hash, encrypted_contact)
        # … or authenticate
        user_id, user_salt, session = sso.authenticate(client.credential_hash)
        client.receive_auth(user_id, user_salt, session)
        # Derive site-specific token
        token = client.derive_site_token("example.com")
        # Build a signed token for the site
        signed = client.build_signed_token("example.com", proof_of_human_score=0.99)
    """

    username: str
    _password: str  # only used to compute credential_hash, never transmitted
    signing_key: SigningKey = field(default_factory=SigningKey.generate)
    verify_key: VerifyKey = field(init=False)

    # Populated after SSO authentication
    user_id: Optional[str] = field(default=None, init=False)
    user_salt: Optional[str] = field(default=None, init=False)
    session: Optional[str] = field(default=None, init=False)

    def __post_init__(self):
        self.verify_key = self.signing_key.verify_key

    # ── Credential hash (Property 1) ─────────────────────────────────

    @property
    def credential_hash(self) -> str:
        """hash(username || password) — computed locally, only hash sent to SSO."""
        return hash_concat(self.username, self._password)

    # ── Receive SSO auth response ────────────────────────────────────

    def receive_auth(self, user_id: str, user_salt: str, session: str):
        """Store user_id and user_salt returned by the SSO after auth."""
        self.user_id = user_id
        self.user_salt = user_salt
        self.session = session

    # ── Site-specific token (Property 2) ─────────────────────────────

    def derive_site_token(self, site_id: str) -> str:
        """
        token = hash(username || site_id || user_id || user_salt)

        Entirely client-side.  The SSO never learns site_id.
        """
        assert self.user_id is not None, "Must authenticate first"
        return hash_concat(self.username, site_id, self.user_id, self.user_salt)

    # ── Routing key (Property 5) ─────────────────────────────────────

    def derive_routing_key(self, site_id: str) -> str:
        """
        routing_key = hash(username || "routing" || site_id || user_id || user_salt)
        """
        assert self.user_id is not None, "Must authenticate first"
        return hash_concat(self.username, "routing", site_id, self.user_id, self.user_salt)

    # ── Signed token construction (Property 4) ───────────────────────

    def build_signed_token(
        self,
        site_id: str,
        proof_of_human_score: float = 1.0,
        timestamp: Optional[float] = None,
    ) -> dict:
        """
        Build and sign a token T containing:
            verification_hash  – the site-specific token (persistent site-local id)
            site_id
            timestamp
            proof_of_human_score

        Returns a dict:
            {
                "payload": <JSON string of T>,
                "signature": <hex-encoded Ed25519 signature>,
                "public_key": <hex-encoded Ed25519 verify key>,
            }
        """
        if timestamp is None:
            timestamp = time.time()

        verification_hash = self.derive_site_token(site_id)

        payload = json.dumps(
            {
                "verification_hash": verification_hash,
                "site_id": site_id,
                "timestamp": timestamp,
                "proof_of_human_score": proof_of_human_score,
            },
            sort_keys=True,
        )

        signed = self.signing_key.sign(payload.encode("utf-8"))
        signature = signed.signature

        return {
            "payload": payload,
            "signature": signature.hex(),
            "public_key": self.verify_key.encode().hex(),
        }
