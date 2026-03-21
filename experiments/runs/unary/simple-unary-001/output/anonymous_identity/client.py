"""
Client
======

The client is the *only* principal that knows the user's plaintext
credentials (username + password).  It performs all privacy-sensitive
derivations locally:

  • credential_hash   = hash(username || password)          → sent to SSO
  • site_token        = hash(username || site_id || …)      → never sent anywhere raw
  • verification_hash = hash(site_token)                    → embedded in signed token
  • routing_key       = hash(username || "routing" || …)    → given to site
  • Ed25519 signature over the token payload                → given to site
"""

import time
from typing import Optional

from .crypto import (
    compute_credential_hash,
    derive_site_token,
    derive_verification_hash,
    derive_routing_key,
    generate_signing_keypair,
    sign_token,
)
from .sso import SSO


class Client:
    """Represents one user's local agent."""

    def __init__(self, username: str, password: str):
        self.username = username
        self._password = password

        # Property 1 – credential hash computed locally
        self.credential_hash = compute_credential_hash(username, password)

        # Ed25519 keypair (per-client; could be per-session or per-site)
        self._signing_key, self.verify_key = generate_signing_keypair()

        # Populated after SSO authentication
        self.user_id: Optional[str] = None
        self.user_salt: Optional[str] = None
        self.session_id: Optional[str] = None

    # ------------------------------------------------------------------
    # SSO interactions
    # ------------------------------------------------------------------

    def register(self, sso: SSO, encrypted_contact: str = "encrypted@example.com") -> str:
        """Register with the SSO.  Only credential_hash leaves the client."""
        self.user_id = sso.register(self.credential_hash, encrypted_contact)
        return self.user_id

    def authenticate(self, sso: SSO) -> None:
        """Authenticate with the SSO.  Receives user_id, user_salt, session."""
        self.user_id, self.user_salt, self.session_id = sso.authenticate(
            self.credential_hash
        )

    # ------------------------------------------------------------------
    # Client-side derivations  (SSO never sees site_id)
    # ------------------------------------------------------------------

    def derive_token_for_site(self, site_id: str) -> str:
        """Property 2 – site-specific token, derived entirely client-side."""
        assert self.user_id and self.user_salt, "must authenticate first"
        return derive_site_token(
            self.username, site_id, self.user_id, self.user_salt
        )

    def derive_verification_hash_for_site(self, site_id: str) -> str:
        """Verification hash = hash(site_token).  The site-local identifier."""
        token = self.derive_token_for_site(site_id)
        return derive_verification_hash(token)

    def derive_routing_key_for_site(self, site_id: str) -> str:
        """Property 5 – routing key, derived entirely client-side."""
        assert self.user_id and self.user_salt, "must authenticate first"
        return derive_routing_key(
            self.username, site_id, self.user_id, self.user_salt
        )

    # ------------------------------------------------------------------
    # Token construction & presentation
    # ------------------------------------------------------------------

    def build_signed_token(self, site_id: str,
                           proof_of_human_score: float = 0.99) -> dict:
        """
        Property 4 – construct and Ed25519-sign a token T containing
        (verification_hash, site_id, timestamp, proof_of_human_score).
        """
        verification_hash = self.derive_verification_hash_for_site(site_id)
        timestamp = time.time()
        return sign_token(
            self._signing_key,
            verification_hash,
            site_id,
            timestamp,
            proof_of_human_score,
        )

    # ------------------------------------------------------------------
    # Routing key registration (tells SSO about a new routing key)
    # ------------------------------------------------------------------

    def register_routing_key(self, sso: SSO, site_id: str) -> str:
        """
        Derive the routing key for *site_id* and register it with the
        SSO so the site can later send messages.

        The SSO only sees the opaque routing key — it cannot reverse it
        to learn site_id.
        """
        rk = self.derive_routing_key_for_site(site_id)
        sso.register_routing_key(self.session_id, rk)
        return rk
