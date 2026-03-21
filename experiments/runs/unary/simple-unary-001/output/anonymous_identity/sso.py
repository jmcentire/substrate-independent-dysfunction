"""
SSO Server
==========

Property 6 – exposes three RPCs:
  register(credential_hash, encrypted_contact) → user_id
  authenticate(credential_hash) → (user_id, user_salt, session)
  route_message(routing_key, encrypted_payload) → delivered

Invariants the SSO upholds:
  • It never sees the plaintext password (only credential_hash).
  • It never learns which site_id the client targets.
  • It stores a mapping routing_key → user_id so sites can send messages
    without learning the user's contact info.
  • After delivering a routed message it discards the plaintext.
"""

import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass
class UserRecord:
    user_id: str
    user_salt: str
    credential_hash: str
    encrypted_contact: str          # opaque blob; SSO stores but need not decrypt
    routing_keys: Dict[str, bool] = field(default_factory=dict)  # routing_key → True
    delivered_messages: list = field(default_factory=list)


@dataclass
class Session:
    session_id: str
    user_id: str
    created_at: float


class SSO:
    """In-memory SSO for demonstration / testing."""

    def __init__(self):
        # credential_hash → UserRecord
        self._users: Dict[str, UserRecord] = {}
        # user_id → UserRecord (secondary index)
        self._users_by_id: Dict[str, UserRecord] = {}
        # routing_key → user_id
        self._routing_table: Dict[str, str] = {}
        # session_id → Session
        self._sessions: Dict[str, Session] = {}
        # delivery log (for testing)
        self._delivery_log: list = []

    # ------------------------------------------------------------------
    # RPC 1 – register
    # ------------------------------------------------------------------

    def register(self, credential_hash: str, encrypted_contact: str) -> str:
        """
        Register a new user.
        Returns: user_id
        Raises ValueError if credential_hash already registered.
        """
        if credential_hash in self._users:
            raise ValueError("credential_hash already registered")

        user_id = secrets.token_hex(16)          # 128-bit random id
        user_salt = secrets.token_hex(32)        # 256-bit random salt

        record = UserRecord(
            user_id=user_id,
            user_salt=user_salt,
            credential_hash=credential_hash,
            encrypted_contact=encrypted_contact,
        )
        self._users[credential_hash] = record
        self._users_by_id[user_id] = record
        return user_id

    # ------------------------------------------------------------------
    # RPC 2 – authenticate
    # ------------------------------------------------------------------

    def authenticate(self, credential_hash: str) -> Tuple[str, str, str]:
        """
        Authenticate with a credential hash.
        Returns: (user_id, user_salt, session_id)
        Raises ValueError on unknown credential.
        """
        record = self._users.get(credential_hash)
        if record is None:
            raise ValueError("unknown credential")

        session_id = secrets.token_hex(16)
        session = Session(
            session_id=session_id,
            user_id=record.user_id,
            created_at=time.time(),
        )
        self._sessions[session_id] = session

        return record.user_id, record.user_salt, session_id

    # ------------------------------------------------------------------
    # Routing key registration (called by client after token derivation)
    # ------------------------------------------------------------------

    def register_routing_key(self, session_id: str, routing_key: str) -> None:
        """
        The client (already authenticated) registers a routing key so
        that sites can later send messages addressed to it.
        The SSO stores routing_key → user_id.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError("invalid session")
        record = self._users_by_id[session.user_id]
        record.routing_keys[routing_key] = True
        self._routing_table[routing_key] = record.user_id

    # ------------------------------------------------------------------
    # RPC 3 – route_message
    # ------------------------------------------------------------------

    def route_message(self, routing_key: str, encrypted_payload: str) -> str:
        """
        A site sends an encrypted message addressed to a routing key.
        The SSO resolves the key, delivers the message to the user's
        record, and discards its own copy of the plaintext.

        Returns: "delivered"
        Raises ValueError if routing key unknown.
        """
        user_id = self._routing_table.get(routing_key)
        if user_id is None:
            raise ValueError("unknown routing key")

        record = self._users_by_id[user_id]
        # "Deliver" – in production this would push to the user's
        # encrypted contact channel.  Here we store for test inspection.
        record.delivered_messages.append(encrypted_payload)

        # Log for external test inspection, then discard the plaintext
        # reference from the SSO's own transient state.
        self._delivery_log.append({
            "routing_key": routing_key,
            "user_id": user_id,
            "status": "delivered",
        })

        return "delivered"

    # ------------------------------------------------------------------
    # Test helpers (NOT part of the public protocol)
    # ------------------------------------------------------------------

    def _get_user_record(self, credential_hash: str) -> Optional[UserRecord]:
        return self._users.get(credential_hash)

    def _get_delivered_messages(self, credential_hash: str) -> list:
        record = self._users.get(credential_hash)
        if record is None:
            return []
        return list(record.delivered_messages)

    @property
    def _all_routing_keys(self) -> Dict[str, str]:
        """Expose for testing – mapping routing_key → user_id."""
        return dict(self._routing_table)
