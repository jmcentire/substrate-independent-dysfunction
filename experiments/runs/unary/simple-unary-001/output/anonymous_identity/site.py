"""
Site (Relying Party)
====================

A site receives a signed token from the client and:
  1. Verifies the Ed25519 signature.
  2. Uses verification_hash as the user's persistent, site-local identity.
  3. Can send messages to the user via the SSO using the routing key,
     without ever learning the user's contact info.

The site never sees:
  • The user's real username or password.
  • The user_salt (so it cannot derive tokens for other sites).
"""

from .crypto import verify_token
from .sso import SSO


class Site:
    """Represents a relying-party website."""

    def __init__(self, site_id: str):
        self.site_id = site_id
        # verification_hash → stored profile (simulated)
        self._users: dict = {}

    def receive_token(self, token: dict) -> dict:
        """
        Verify the signed token presented by a client.

        Returns a result dict:
          {"ok": True/False, "verification_hash": ..., "reason": ...}
        """
        # Check site_id matches
        if token.get("site_id") != self.site_id:
            return {"ok": False, "reason": "site_id mismatch"}

        # Verify Ed25519 signature
        if not verify_token(token):
            return {"ok": False, "reason": "invalid signature"}

        vh = token["verification_hash"]
        self._users[vh] = {
            "public_key": token["public_key"],
            "last_seen": token["timestamp"],
            "proof_of_human_score": token["proof_of_human_score"],
        }
        return {"ok": True, "verification_hash": vh}

    def send_message_to_user(self, sso: SSO, routing_key: str,
                             encrypted_payload: str) -> str:
        """
        Send an encrypted message to a user via the SSO's routing
        mechanism.  The site never learns the user's contact info.
        """
        return sso.route_message(routing_key, encrypted_payload)

    def known_verification_hashes(self) -> list:
        return list(self._users.keys())
