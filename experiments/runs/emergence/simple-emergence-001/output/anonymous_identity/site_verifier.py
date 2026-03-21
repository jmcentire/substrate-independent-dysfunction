"""Site Verifier — the relying-party (website) side.

A site receives a signed token from the client and:
  1. Verifies the Ed25519 signature.
  2. Checks the site_id matches its own.
  3. Uses the verification_hash as the user's persistent site-local identifier.

The site never learns the user's real identity, username, or user_salt.
"""

import time
from typing import Optional

from .crypto_utils import verify_token_signature


class SiteVerifier:
    """Represents a site/relying party."""

    def __init__(self, site_id: str, max_token_age: float = 300.0):
        self.site_id = site_id
        self.max_token_age = max_token_age  # seconds

    def verify(self, token: dict, current_time: Optional[float] = None) -> dict:
        """Verify a signed token.

        Returns the payload on success.
        Raises on invalid signature, wrong site_id, or expired token.
        """
        # 1. Verify Ed25519 signature (raises BadSignatureError on failure)
        payload = verify_token_signature(token)

        # 2. Check site_id
        if payload["site_id"] != self.site_id:
            raise ValueError(
                f"Token site_id '{payload['site_id']}' does not match "
                f"expected '{self.site_id}'."
            )

        # 3. Check freshness
        now = current_time if current_time is not None else time.time()
        age = abs(now - payload["timestamp"])
        if age > self.max_token_age:
            raise ValueError(f"Token expired (age={age:.1f}s > max={self.max_token_age:.1f}s).")

        return payload

    def get_user_identifier(self, token: dict, current_time: Optional[float] = None) -> str:
        """Verify token and return the site-local user identifier."""
        payload = self.verify(token, current_time=current_time)
        return payload["verification_hash"]
