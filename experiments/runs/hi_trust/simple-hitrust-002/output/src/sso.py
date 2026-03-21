"""
SSO Server Module for Anonymous Identity System
=================================================

Implements a privacy-preserving Single Sign-On server that:
- Authenticates users via credential hashes (never sees raw passwords)
- Issues user_id and user_salt for client-side token derivation
- Routes messages via opaque routing keys (never learns site_id)
- Maintains an in-memory store for users, sessions, routing keys, and delivery logs

Classes:
    AuthError: Raised when authentication fails.
    SSOServer: The main SSO server with register, authenticate, route_message,
               and register_routing_key methods.

Usage:
    from src.sso import SSOServer, AuthError

    sso = SSOServer()
    user_id = sso.register(credential_hash="abc123", encrypted_contact="enc_contact")
    user_id, user_salt, session = sso.authenticate("abc123")
    sso.register_routing_key(session, routing_key="opaque_key_xyz")
    result = sso.route_message("opaque_key_xyz", encrypted_payload="secret_msg")
    # result == 'delivered'
"""

import secrets
import time
from typing import Dict, Tuple, List, Any, Optional


class AuthError(Exception):
    """Raised when authentication fails (unknown credential_hash or invalid session)."""
    pass


class SSOServer:
    """
    Privacy-preserving SSO server with in-memory storage.

    The SSO server:
    - Never sees raw usernames or passwords (only credential hashes).
    - Never stores or learns site_id values — routing keys are opaque blobs.
    - Provides user_id and user_salt so clients can derive site-specific tokens locally.
    - Routes messages by resolving opaque routing keys to stored contact info.

    Internal Storage:
        _users: credential_hash -> {user_id, user_salt, encrypted_contact}
        _sessions: session_token -> credential_hash  (active sessions)
        _routing_keys: routing_key -> credential_hash (opaque mapping)
        _delivery_log: list of delivered message records (for testing/auditing)
    """

    def __init__(self) -> None:
        # credential_hash -> { "user_id": str, "user_salt": str, "encrypted_contact": str }
        self._users: Dict[str, Dict[str, str]] = {}

        # session_token -> credential_hash
        self._sessions: Dict[str, str] = {}

        # routing_key -> credential_hash  (routing keys are opaque; no site_id stored)
        self._routing_keys: Dict[str, str] = {}

        # List of delivery records for testing: each is a dict with delivery metadata
        self._delivery_log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # (1) Registration
    # ------------------------------------------------------------------

    def register(self, credential_hash: str, encrypted_contact: str) -> str:
        """
        Register a new user with the SSO.

        Args:
            credential_hash: Client-computed hash(username || password). The SSO
                             never sees the raw password.
            encrypted_contact: Encrypted contact information (e.g., email) that
                               the SSO can use for message delivery. The SSO stores
                               this opaquely.

        Returns:
            user_id: A randomly generated unique user identifier.

        Raises:
            ValueError: If the credential_hash is already registered.
        """
        if credential_hash in self._users:
            raise ValueError("Credential hash is already registered.")

        # Generate cryptographically random user_id and user_salt
        user_id = secrets.token_hex(16)    # 32-char hex string (128-bit)
        user_salt = secrets.token_hex(16)  # 32-char hex string (128-bit)

        self._users[credential_hash] = {
            "user_id": user_id,
            "user_salt": user_salt,
            "encrypted_contact": encrypted_contact,
        }

        return user_id

    # ------------------------------------------------------------------
    # (2) Authentication
    # ------------------------------------------------------------------

    def authenticate(self, credential_hash: str) -> Tuple[str, str, str]:
        """
        Authenticate a user by their credential hash.

        Args:
            credential_hash: Client-computed hash(username || password).

        Returns:
            Tuple of (user_id, user_salt, session_token).
            - user_id and user_salt are needed by the client to derive
              site-specific tokens entirely client-side.
            - session_token is a random bearer token for subsequent SSO calls
              (e.g., register_routing_key).

        Raises:
            AuthError: If credential_hash is not found (unknown user).
        """
        if credential_hash not in self._users:
            raise AuthError("Authentication failed: unknown credential hash.")

        user_record = self._users[credential_hash]
        user_id = user_record["user_id"]
        user_salt = user_record["user_salt"]

        # Issue a fresh random session token
        session_token = secrets.token_hex(32)  # 64-char hex string (256-bit)
        self._sessions[session_token] = credential_hash

        return user_id, user_salt, session_token

    # ------------------------------------------------------------------
    # (4) Register Routing Key  (listed before route_message for logical flow)
    # ------------------------------------------------------------------

    def register_routing_key(self, session_token: str, routing_key: str) -> None:
        """
        Register an opaque routing key for the authenticated user.

        The client derives: routing_key = hash(username || "routing" || site_id || user_id || user_salt)
        entirely client-side. The SSO receives only the resulting opaque key and
        **never learns site_id**.

        Args:
            session_token: A valid session token obtained from authenticate().
            routing_key: An opaque routing key string. The SSO stores it without
                         any knowledge of which site it corresponds to.

        Raises:
            AuthError: If the session_token is invalid or expired.
        """
        if session_token not in self._sessions:
            raise AuthError("Invalid or expired session token.")

        credential_hash = self._sessions[session_token]

        # Map the opaque routing key to the user's credential_hash.
        # This allows route_message to resolve the key -> user -> contact info.
        self._routing_keys[routing_key] = credential_hash

    # ------------------------------------------------------------------
    # (3) Route Message
    # ------------------------------------------------------------------

    def route_message(self, routing_key: str, encrypted_payload: str) -> str:
        """
        Route a message to a user via an opaque routing key.

        A site sends a message addressed to a routing key. The SSO:
        1. Resolves routing_key -> credential_hash -> encrypted_contact.
        2. "Delivers" the message (appends to delivery log for testing).
        3. Discards the plaintext — the SSO only sees the encrypted payload
           and the contact info; it never learns site_id.

        Args:
            routing_key: The opaque routing key previously registered by the client.
            encrypted_payload: The encrypted message payload from the site.

        Returns:
            'delivered' on success.

        Raises:
            ValueError: If the routing key is unknown (not registered).
        """
        if routing_key not in self._routing_keys:
            raise ValueError("Unknown routing key.")

        credential_hash = self._routing_keys[routing_key]

        if credential_hash not in self._users:
            raise ValueError("User record not found for routing key.")

        user_record = self._users[credential_hash]
        encrypted_contact = user_record["encrypted_contact"]

        # "Deliver" the message: append to the delivery log for testing/auditing.
        delivery_record = {
            "routing_key": routing_key,
            "encrypted_contact": encrypted_contact,
            "encrypted_payload": encrypted_payload,
            "timestamp": time.time(),
        }
        self._delivery_log.append(delivery_record)

        # The SSO discards the plaintext after delivery — we do not retain
        # any decrypted content. The encrypted_payload in the log represents
        # what was forwarded, not decrypted.

        return "delivered"

    # ------------------------------------------------------------------
    # Helper / Introspection (for testing only)
    # ------------------------------------------------------------------

    def get_delivery_log(self) -> List[Dict[str, Any]]:
        """Return a copy of the delivery log (for testing purposes)."""
        return list(self._delivery_log)

    def get_user_record(self, credential_hash: str) -> Optional[Dict[str, str]]:
        """
        Return the stored user record for a credential_hash, or None.
        Useful for tests that need to verify internal state.
        NOTE: The SSO never exposes this externally in production.
        """
        return self._users.get(credential_hash)

    def is_session_valid(self, session_token: str) -> bool:
        """Check if a session token is currently valid."""
        return session_token in self._sessions

    def revoke_session(self, session_token: str) -> None:
        """Revoke a session token (logout)."""
        self._sessions.pop(session_token, None)

    @property
    def registered_user_count(self) -> int:
        """Number of registered users."""
        return len(self._users)

    @property
    def active_session_count(self) -> int:
        """Number of active sessions."""
        return len(self._sessions)

    @property
    def routing_key_count(self) -> int:
        """Number of registered routing keys."""
        return len(self._routing_keys)
