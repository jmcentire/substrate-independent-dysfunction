"""
site.py – Site-side token verification for the Anonymous Identity system.

A site receives a signed token from the client and:
    1. Verifies the Ed25519 signature.
    2. Checks that the embedded site_id matches its own identity.
    3. Extracts verification_hash as the user's persistent site-local ID.

The site never learns the user's username, password, user_id, or user_salt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, Optional

from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError


@dataclass
class SiteVerifier:
    """
    Represents a relying-party site that accepts anonymous tokens.

    Usage:
        site = SiteVerifier("example.com")
        result = site.verify_token(signed_token_dict)
        if result is not None:
            print(result["verification_hash"])  # persistent site-local user id
    """

    site_id: str

    # Local user table: verification_hash → whatever site-local profile data
    _known_users: Dict[str, dict] = field(default_factory=dict)

    def verify_token(self, token: dict) -> Optional[dict]:
        """
        Verify a signed token dict (as produced by Client.build_signed_token).

        Returns the parsed payload dict on success, or None on failure.
        """
        try:
            payload_bytes = token["payload"].encode("utf-8")
            signature = bytes.fromhex(token["signature"])
            vk_bytes = bytes.fromhex(token["public_key"])

            vk = VerifyKey(vk_bytes)
            # Raises BadSignatureError if invalid
            vk.verify(payload_bytes, signature)

            payload = json.loads(token["payload"])

            # Ensure the token is addressed to us
            if payload.get("site_id") != self.site_id:
                return None

            # Record the user locally
            vh = payload["verification_hash"]
            self._known_users[vh] = payload

            return payload

        except (BadSignatureError, KeyError, ValueError, json.JSONDecodeError):
            return None

    def has_user(self, verification_hash: str) -> bool:
        return verification_hash in self._known_users
