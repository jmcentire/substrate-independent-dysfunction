"""
sso.py – Single Sign-On server for the Anonymous Identity system.

The SSO stores:
    credential_hash → { user_id, user_salt, encrypted_contact }

It exposes three operations (Property 6):
    register(credential_hash, encrypted_contact) → user_id
    authenticate(credential_hash) → (user_id, user_salt, session)
    route_message(routing_key, encrypted_payload) → delivered (bool)

The SSO *never* sees:
    - the plaintext password (only credential_hash arrives)
    - which site_id a user visits (site_id is never sent to the SSO)

For routing, the SSO maintains a secondary index:
    routing_key → user_id
which is registered by the client after authentication (register_routing_key).
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass
class _UserRecord:
    user_id: str
    user_salt: str
    encrypted_contact: str  # opaque blob — SSO stores it but can't read plaintext


@dataclass
class SSOServer:
    """In-memory SSO server."""

    # Primary store: credential_hash → UserRecord
    _users: Dict[str, _UserRecord] = field(default_factory=dict)

    # Routing index: routing_key → user_id
    _routing_keys: Dict[str, str] = field(default_factory=dict)

    # Active sessions: session_token → user_id
    _sessions: Dict[str, str] = field(default_factory=dict)

    # Message log (for testing): list of (user_id, encrypted_payload)
    _delivered: list = field(default_factory=list)

    # ── Register (Property 6) ────────────────────────────────────────

    def register(self, credential_hash: str, encrypted_contact: str) -> str:
        """
        Register a new user.

        Args:
            credential_hash: hash(username || password), computed client-side.
            encrypted_contact: Encrypted contact info blob.

        Returns:
            The newly assigned user_id.

        Raises:
            ValueError: if the credential_hash is already registered.
        """
        if credential_hash in self._users:
            raise ValueError("Credential hash already registered")

        user_id = uuid.uuid4().hex
        user_salt = os.urandom(32).hex()

        self._users[credential_hash] = _UserRecord(
            user_id=user_id,
            user_salt=user_salt,
            encrypted_contact=encrypted_contact,
        )
        return user_id

    # ── Authenticate (Property 6) ────────────────────────────────────

    def authenticate(self, credential_hash: str) -> Tuple[str, str, str]:
        """
        Authenticate an existing user.

        Returns:
            (user_id, user_salt, session_token)

        Raises:
            KeyError: if credential_hash is not registered.
        """
        if credential_hash not in self._users:
            raise KeyError("Unknown credential hash")

        rec = self._users[credential_hash]
        session_token = uuid.uuid4().hex
        self._sessions[session_token] = rec.user_id
        return rec.user_id, rec.user_salt, session_token

    # ── Routing key registration ─────────────────────────────────────

    def register_routing_key(self, routing_key: str, credential_hash: str):
        """
        Let a client register a routing key so the SSO can resolve it later.

        In production, the client would prove ownership of the session
        rather than resending the credential_hash — kept simple here.
        """
        if credential_hash not in self._users:
            raise KeyError("Unknown credential hash")
        self._routing_keys[routing_key] = credential_hash

    # ── Route message (Property 6) ───────────────────────────────────

    def route_message(self, routing_key: str, encrypted_payload: str) -> bool:
        """
        Deliver *encrypted_payload* to the user identified by *routing_key*.

        The SSO resolves routing_key → user, delivers the payload using the
        user's stored encrypted_contact info, then discards the plaintext.

        Returns True if delivered, False if routing key unknown.
        """
        credential_hash = self._routing_keys.get(routing_key)
        if credential_hash is None:
            return False

        rec = self._users[credential_hash]
        # In production: decrypt contact, deliver via email/push, discard.
        # Here we log it for testability.
        self._delivered.append(
            {
                "user_id": rec.user_id,
                "encrypted_contact": rec.encrypted_contact,
                "encrypted_payload": encrypted_payload,
            }
        )
        return True

    # ── Introspection helpers (for tests / assertions) ───────────────

    def get_user_record(self, credential_hash: str) -> Optional[dict]:
        """Return a *copy* of the user record (no mutation)."""
        rec = self._users.get(credential_hash)
        if rec is None:
            return None
        return {
            "user_id": rec.user_id,
            "user_salt": rec.user_salt,
            "encrypted_contact": rec.encrypted_contact,
        }

    @property
    def known_site_ids(self) -> list:
        """The SSO should never know any site_id — always returns []."""
        return []  # by design
