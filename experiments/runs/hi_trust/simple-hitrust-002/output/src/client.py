"""
Client-side library for the Anonymous Identity system.
======================================================

Orchestrates the full anonymous identity flow entirely on the client side,
ensuring that:

- The SSO never sees the user's plaintext password (only a credential hash).
- The SSO never learns which sites the user visits (site_id never leaves the client).
- Two sites cannot correlate that they are serving the same user (tokens are unlinkable).

Dependencies
------------
- ``src.crypto``: Provides ``credential_hash``, ``derive_site_token``,
  ``derive_routing_key``, ``generate_ed25519_keypair`` (see ``src/crypto.py``).
- ``src.token``: Provides ``create_token`` and the ``Token`` dataclass
  (see ``src/token.py``).
- ``src.sso``: Provides ``SSOServer`` with ``register()``, ``authenticate()``,
  ``register_routing_key()``, ``route_message()`` (see ``src/sso.py``).

Usage
-----
    >>> from src.sso import SSOServer
    >>> from src.client import AnonClient
    >>>
    >>> sso = SSOServer()
    >>> client = AnonClient("alice", "hunter2")
    >>> client.register(sso, "encrypted_alice@example.com")
    >>> client.authenticate(sso)
    >>> token = client.get_token_for_site("example.com")
    >>> routing_key = client.get_routing_key_for_site("example.com")
    >>> client.register_routing_key(sso, "example.com")
"""

from __future__ import annotations

from typing import Optional

# Crypto primitives (sibling module).
# Expected interface:
#   credential_hash(username: str, password: str) -> str (hex, 64 chars)
#   derive_site_token(username, site_id, user_id, user_salt) -> str (hex, 64 chars)
#   derive_routing_key(username, site_id, user_id, user_salt) -> str (hex, 64 chars)
#   generate_ed25519_keypair() -> (private_key_bytes: bytes, public_key_bytes: bytes)
from src.crypto import (
    credential_hash,
    derive_site_token,
    derive_routing_key,
    generate_ed25519_keypair,
)

# Token construction (sibling module).
# Expected interface:
#   create_token(username, site_id, user_id, user_salt, signing_key, proof_of_human_score,
#                *, _timestamp=None) -> Token
#   Token dataclass with fields: verification_hash, site_id, timestamp,
#                                proof_of_human_score, signature
from src.token import Token, create_token


class AnonClient:
    """Client-side orchestrator for the Anonymous Identity system.

    The client holds the user's credentials and Ed25519 keypair locally.
    All site-specific derivations (tokens, routing keys) happen entirely
    on the client — ``site_id`` is **never** transmitted to the SSO.

    Attributes:
        username:        The user's login name (stored locally, never sent to SSO).
        private_key:     32-byte raw Ed25519 private signing key.
        public_key:      32-byte raw Ed25519 public verification key.
        user_id:         Opaque identifier assigned by the SSO upon registration.
        user_salt:       Random salt assigned by the SSO (used for token derivation).
        session:         Bearer session token from the SSO (used for authenticated
                         SSO calls such as ``register_routing_key``).
    """

    def __init__(self, username: str, password: str) -> None:
        """Initialise the client with credentials and a fresh Ed25519 keypair.

        The plaintext password is stored only long enough to compute the
        credential hash when needed.  The ``site_id`` is never stored here —
        it is always provided per-call.

        Args:
            username: The user's login name.
            password: The user's plaintext password (never transmitted).
        """
        # Credentials — stored locally, never sent in the clear.
        self._username: str = username
        self._password: str = password

        # Ed25519 keypair for signing tokens.
        self._private_key: bytes
        self._public_key: bytes
        self._private_key, self._public_key = generate_ed25519_keypair()

        # SSO-issued values — populated by register() / authenticate().
        self._user_id: Optional[str] = None
        self._user_salt: Optional[str] = None
        self._session: Optional[str] = None

    # ------------------------------------------------------------------
    # Public properties (read-only)
    # ------------------------------------------------------------------

    @property
    def username(self) -> str:
        """The user's login name (client-local only)."""
        return self._username

    @property
    def private_key(self) -> bytes:
        """32-byte raw Ed25519 private key."""
        return self._private_key

    @property
    def public_key(self) -> bytes:
        """32-byte raw Ed25519 public key."""
        return self._public_key

    @property
    def user_id(self) -> Optional[str]:
        """Opaque user identifier assigned by the SSO, or ``None``."""
        return self._user_id

    @property
    def user_salt(self) -> Optional[str]:
        """Random salt assigned by the SSO, or ``None``."""
        return self._user_salt

    @property
    def session(self) -> Optional[str]:
        """Active SSO session token, or ``None``."""
        return self._session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _credential_hash(self) -> str:
        """Compute the credential hash locally.

        Returns:
            ``SHA-256(username || password)`` as a 64-char hex string.
        """
        return credential_hash(self._username, self._password)

    def _require_authenticated(self) -> None:
        """Raise if the client has not yet authenticated / registered."""
        if self._user_id is None or self._user_salt is None:
            raise RuntimeError(
                "Client is not authenticated. Call register() or authenticate() first."
            )

    # ------------------------------------------------------------------
    # (2) Registration
    # ------------------------------------------------------------------

    def register(self, sso: "SSOServer", encrypted_contact: str) -> str:  # noqa: F821
        """Register a new account with the SSO.

        Computes the credential hash locally and transmits only the hash
        (never the plaintext password).  Stores the returned ``user_id``
        and immediately authenticates to obtain ``user_salt`` and a
        session token.

        Args:
            sso:               An ``SSOServer`` instance.
            encrypted_contact: Encrypted contact information (e.g. email).
                               The SSO stores this opaquely for message
                               delivery.

        Returns:
            The ``user_id`` assigned by the SSO.

        Raises:
            ValueError: If the credential hash is already registered.
        """
        cred_hash = self._credential_hash()

        # Register — the SSO returns user_id.
        user_id = sso.register(cred_hash, encrypted_contact)

        # Authenticate to obtain user_salt and a session token.
        user_id_auth, user_salt, session = sso.authenticate(cred_hash)
        assert user_id_auth == user_id, "user_id mismatch between register and authenticate"

        self._user_id = user_id
        self._user_salt = user_salt
        self._session = session

        return user_id

    # ------------------------------------------------------------------
    # (3) Authentication
    # ------------------------------------------------------------------

    def authenticate(self, sso: "SSOServer") -> str:  # noqa: F821
        """Authenticate with the SSO using the locally-computed credential hash.

        Stores ``user_id``, ``user_salt``, and ``session`` for subsequent
        client-side derivations and SSO calls.

        Args:
            sso: An ``SSOServer`` instance.

        Returns:
            The session token string.

        Raises:
            AuthError: If the credential hash is unknown to the SSO.
        """
        cred_hash = self._credential_hash()
        user_id, user_salt, session = sso.authenticate(cred_hash)

        self._user_id = user_id
        self._user_salt = user_salt
        self._session = session

        return session

    # ------------------------------------------------------------------
    # (4) Site-specific token derivation
    # ------------------------------------------------------------------

    def get_token_for_site(
        self,
        site_id: str,
        proof_of_human_score: float = 1.0,
        *,
        _timestamp: Optional[float] = None,
    ) -> Token:
        """Derive and sign a site-specific anonymous identity token.

        The entire derivation happens **client-side**.  ``site_id`` is
        mixed into the hash but is never sent to the SSO.

        The returned ``Token`` contains:
          - ``verification_hash``: persistent site-local identifier.
          - ``site_id``: the target site.
          - ``timestamp``: token creation time (UTC).
          - ``proof_of_human_score``: CAPTCHA / proof-of-human confidence.
          - ``signature``: Ed25519 signature over the above fields.

        Args:
            site_id:              Unique identifier of the target site.
            proof_of_human_score: Float in ``[0, 1]``, default ``1.0``.
            _timestamp:           *Testing only* — override the UTC timestamp.

        Returns:
            A signed :class:`Token`.

        Raises:
            RuntimeError: If the client is not yet authenticated.
            ValueError:   If ``proof_of_human_score`` is out of range.
        """
        self._require_authenticated()

        return create_token(
            username=self._username,
            site_id=site_id,
            user_id=self._user_id,
            user_salt=self._user_salt,
            signing_key=self._private_key,
            proof_of_human_score=proof_of_human_score,
            _timestamp=_timestamp,
        )

    # ------------------------------------------------------------------
    # (5) Routing key derivation
    # ------------------------------------------------------------------

    def get_routing_key_for_site(self, site_id: str) -> str:
        """Derive the routing key for a specific site (client-side only).

        ``routing_key = SHA-256(username || "routing" || site_id || user_id || user_salt)``

        The ``site_id`` is used in the derivation but is **never**
        transmitted to the SSO.

        Args:
            site_id: Unique identifier of the target site.

        Returns:
            Hex-encoded routing key (64 characters).

        Raises:
            RuntimeError: If the client is not yet authenticated.
        """
        self._require_authenticated()

        return derive_routing_key(
            username=self._username,
            site_id=site_id,
            user_id=self._user_id,
            user_salt=self._user_salt,
        )

    # ------------------------------------------------------------------
    # (6) Register routing key with SSO
    # ------------------------------------------------------------------

    def register_routing_key(self, sso: "SSOServer", site_id: str) -> str:  # noqa: F821
        """Derive a routing key client-side and register it with the SSO.

        The SSO receives only the **opaque** routing key hash — it never
        learns the ``site_id`` that was mixed into the derivation.

        Args:
            sso:     An ``SSOServer`` instance.
            site_id: Unique identifier of the target site.

        Returns:
            The routing key that was registered (hex string).

        Raises:
            RuntimeError: If the client is not yet authenticated.
            AuthError:    If the SSO session is invalid or expired.
        """
        self._require_authenticated()

        if self._session is None:
            raise RuntimeError(
                "No active session. Call register() or authenticate() first."
            )

        routing_key = self.get_routing_key_for_site(site_id)
        sso.register_routing_key(self._session, routing_key)

        return routing_key
