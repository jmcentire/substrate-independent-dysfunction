"""Client — performs all privacy-sensitive operations locally.

The client:
  1. Hashes credentials before sending to the SSO.
  2. Derives site-specific tokens entirely client-side.
  3. Signs tokens with Ed25519.
  4. Derives routing keys client-side.
"""

import time
from typing import Optional, Tuple

from nacl.signing import SigningKey

from .crypto_utils import (
    hash_credential,
    derive_site_token,
    derive_routing_key,
    generate_ed25519_keypair,
    sign_token,
)


class Client:
    """Represents a user's local client application."""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password  # kept only in local memory
        self.credential_hash = hash_credential(username, password)

        # Ed25519 keypair per client instance
        self.signing_key, self.verify_key = generate_ed25519_keypair()

        # Populated after authentication with the SSO
        self.user_id: Optional[str] = None
        self.user_salt: Optional[str] = None
        self.session: Optional[str] = None

    # ── SSO interaction ──────────────────────────────────────────────────

    def register(self, sso, encrypted_contact: str) -> str:
        """Register with the SSO. Only credential_hash is transmitted."""
        self.user_id = sso.register(self.credential_hash, encrypted_contact)
        return self.user_id

    def authenticate(self, sso) -> Tuple[str, str, str]:
        """Authenticate with the SSO. Returns (user_id, user_salt, session)."""
        result = sso.authenticate(self.credential_hash)
        self.user_id = result["user_id"]
        self.user_salt = result["user_salt"]
        self.session = result["session"]
        return self.user_id, self.user_salt, self.session

    # ── Client-side derivations (SSO never involved) ─────────────────────

    def get_site_token(self, site_id: str) -> str:
        """Derive the site-specific verification hash client-side."""
        if self.user_id is None or self.user_salt is None:
            raise RuntimeError("Must authenticate with SSO first.")
        return derive_site_token(self.username, site_id, self.user_id, self.user_salt)

    def get_routing_key(self, site_id: str) -> str:
        """Derive the routing key for a specific site, client-side."""
        if self.user_id is None or self.user_salt is None:
            raise RuntimeError("Must authenticate with SSO first.")
        return derive_routing_key(self.username, site_id, self.user_id, self.user_salt)

    def create_signed_token(
        self,
        site_id: str,
        proof_of_human_score: float = 1.0,
        timestamp: Optional[float] = None,
    ) -> dict:
        """Create a signed token for a specific site.

        The token contains (verification_hash, site_id, timestamp, proof_of_human_score)
        signed with the client's Ed25519 key.
        """
        verification_hash = self.get_site_token(site_id)
        ts = timestamp if timestamp is not None else time.time()
        return sign_token(
            self.signing_key,
            verification_hash,
            site_id,
            ts,
            proof_of_human_score,
        )
