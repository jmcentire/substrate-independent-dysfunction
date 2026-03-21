"""Client-side logic for the Anonymous Identity system."""

from nacl.signing import SigningKey

from .crypto_utils import (
    compute_credential_hash,
    derive_site_token,
    derive_verification_hash,
    derive_routing_key,
    build_signed_token,
)
from .sso import SSO


class Client:
    """Represents a user's local client.

    All sensitive derivations happen here. The SSO only receives the
    credential hash and never learns site_id or site-specific tokens.
    """

    def __init__(self, username: str, password: str):
        self.username = username
        self._password = password
        self.credential_hash = compute_credential_hash(username, password)
        self.user_id: str | None = None
        self.user_salt: str | None = None
        self.session: str | None = None
        self._signing_key = SigningKey.generate()

    def register(self, sso: SSO, encrypted_contact: str) -> str:
        """Register with the SSO. Returns user_id."""
        user_id = sso.register(self.credential_hash, encrypted_contact)
        self.user_id = user_id
        return user_id

    def authenticate(self, sso: SSO) -> tuple[str, str, str]:
        """Authenticate with the SSO. Returns (user_id, user_salt, session)."""
        user_id, user_salt, session = sso.authenticate(self.credential_hash)
        self.user_id = user_id
        self.user_salt = user_salt
        self.session = session
        return (user_id, user_salt, session)

    def get_site_token(self, site_id: str) -> str:
        """Derive the site-specific token client-side."""
        assert self.user_id and self.user_salt, "Must authenticate first"
        return derive_site_token(self.username, site_id, self.user_id, self.user_salt)

    def get_verification_hash(self, site_id: str) -> str:
        """Derive the verification hash for a site."""
        assert self.user_id and self.user_salt, "Must authenticate first"
        return derive_verification_hash(self.username, site_id, self.user_id, self.user_salt)

    def get_routing_key(self, site_id: str) -> str:
        """Derive the routing key for a site."""
        assert self.user_id and self.user_salt, "Must authenticate first"
        return derive_routing_key(self.username, site_id, self.user_id, self.user_salt)

    def build_token_for_site(self, site_id: str, proof_of_human_score: float = 1.0) -> tuple[bytes, object]:
        """Build a signed token for a site. Returns (signed_bytes, verify_key)."""
        assert self.user_id and self.user_salt, "Must authenticate first"
        return build_signed_token(
            self.username, site_id, self.user_id, self.user_salt,
            proof_of_human_score=proof_of_human_score,
            signing_key=self._signing_key,
        )
