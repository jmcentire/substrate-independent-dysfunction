"""
Anonymous Identity System — SSO Server

The SSO server is deliberately limited in what it knows:
  • It stores: credential_hash → (user_id, user_salt, encrypted_contact)
  • It NEVER learns *site_id* → cannot derive site-specific tokens.
  • It facilitates asynchronous message routing via routing keys without
    being able to read the encrypted payloads.

This module provides an in-memory reference implementation suitable for
testing and embedding.  A production deployment would persist state to a
database and expose the methods via HTTP / gRPC.
"""

from __future__ import annotations

import os
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class UserRecord:
    user_id: str
    user_salt: str
    credential_hash: str
    encrypted_contact: bytes  # opaque blob; SSO cannot read it


@dataclass
class Session:
    session_id: str
    user_id: str
    created_at: float
    expires_at: float


@dataclass
class RoutedMessage:
    routing_key: str
    encrypted_payload: bytes
    created_at: float


# ---------------------------------------------------------------------------
# SSO Server
# ---------------------------------------------------------------------------

class SSOServer:
    """In-memory SSO server."""

    def __init__(self, session_ttl: float = 3600.0) -> None:
        self._session_ttl = session_ttl

        # credential_hash → UserRecord
        self._users: Dict[str, UserRecord] = {}
        # user_id → UserRecord  (secondary index)
        self._users_by_id: Dict[str, UserRecord] = {}
        # session_id → Session
        self._sessions: Dict[str, Session] = {}
        # routing_key → list of messages
        self._mailboxes: Dict[str, List[RoutedMessage]] = {}

    # ---- registration -----------------------------------------------------

    def register(
        self,
        credential_hash: str,
        encrypted_contact: bytes = b"",
    ) -> str:
        """Register a new user.

        Parameters
        ----------
        credential_hash : str
            SHA-256 hex digest of (username || password).
        encrypted_contact : bytes
            Opaque encrypted blob the SSO stores but cannot read.

        Returns
        -------
        user_id : str
            A server-generated unique identifier for the user.

        Raises
        ------
        ValueError
            If the credential_hash is already registered.
        """
        if credential_hash in self._users:
            raise ValueError("credential_hash already registered")

        user_id = secrets.token_hex(16)  # 128-bit random id
        user_salt = secrets.token_hex(16)  # 128-bit random salt

        record = UserRecord(
            user_id=user_id,
            user_salt=user_salt,
            credential_hash=credential_hash,
            encrypted_contact=encrypted_contact,
        )
        self._users[credential_hash] = record
        self._users_by_id[user_id] = record
        return user_id

    # ---- authentication ---------------------------------------------------

    def authenticate(
        self,
        credential_hash: str,
    ) -> Tuple[str, str, Session]:
        """Authenticate a user by credential hash.

        Returns
        -------
        (user_id, user_salt, session)
        """
        record = self._users.get(credential_hash)
        if record is None:
            raise ValueError("invalid credential")

        now = time.time()
        session = Session(
            session_id=secrets.token_hex(16),
            user_id=record.user_id,
            created_at=now,
            expires_at=now + self._session_ttl,
        )
        self._sessions[session.session_id] = session
        return record.user_id, record.user_salt, session

    # ---- session validation -----------------------------------------------

    def validate_session(self, session_id: str) -> Optional[Session]:
        """Return the session if it exists and has not expired, else None."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if time.time() > session.expires_at:
            del self._sessions[session_id]
            return None
        return session

    # ---- message routing --------------------------------------------------

    def route_message(
        self,
        routing_key: str,
        encrypted_payload: bytes,
    ) -> bool:
        """Deposit an encrypted message into the routing-key mailbox.

        Returns True once stored ("delivered").
        """
        msg = RoutedMessage(
            routing_key=routing_key,
            encrypted_payload=encrypted_payload,
            created_at=time.time(),
        )
        self._mailboxes.setdefault(routing_key, []).append(msg)
        return True

    def fetch_messages(self, routing_key: str) -> List[RoutedMessage]:
        """Retrieve (and drain) all messages for a routing key."""
        return self._mailboxes.pop(routing_key, [])

    # ---- introspection helpers (for tests) --------------------------------

    def _get_user_record(self, credential_hash: str) -> Optional[UserRecord]:
        return self._users.get(credential_hash)

    def _get_user_record_by_id(self, user_id: str) -> Optional[UserRecord]:
        return self._users_by_id.get(user_id)

    @property
    def user_count(self) -> int:
        return len(self._users)
