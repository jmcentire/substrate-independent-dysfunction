"""
Anonymous Identity System — SSO Server Module

Provides a minimal Single Sign-On server with three operations:

  register(credential_hash, encrypted_contact) → user_id
  authenticate(credential_hash)                → (user_id, user_salt, session_token)
  route_message(routing_key, encrypted_payload) → delivered (bool)

Design constraints
------------------
* The SSO **never** sees usernames or passwords — only credential hashes.
* The SSO **cannot** derive site-specific tokens because it does not know
  the username (only the credential_hash which is a one-way derivative).
* Routing keys are stored as an opaque mapping; the SSO simply delivers
  encrypted payloads to the correct user_id.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


# =========================================================================
# Internal data structures
# =========================================================================

@dataclass
class _UserRecord:
    user_id: str
    credential_hash: str
    user_salt: str
    encrypted_contact: str  # opaque blob
    # inbox: list of (routing_key, encrypted_payload) delivered messages
    inbox: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class _Session:
    session_token: str
    user_id: str


# =========================================================================
# SSO Server
# =========================================================================

class SSOServer:
    """In-memory SSO server for the Anonymous Identity system."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # credential_hash → _UserRecord
        self._users: Dict[str, _UserRecord] = {}
        # user_id → _UserRecord  (reverse index)
        self._users_by_id: Dict[str, _UserRecord] = {}
        # session_token → _Session
        self._sessions: Dict[str, _Session] = {}
        # routing_key → user_id
        self._routing_table: Dict[str, str] = {}

    # -----------------------------------------------------------------
    # 1. register
    # -----------------------------------------------------------------
    def register(
        self,
        credential_hash: str,
        encrypted_contact: str = "",
    ) -> str:
        """Register a new user.

        Parameters
        ----------
        credential_hash : str
            SHA-256 hex of (username:password).
        encrypted_contact : str
            Opaque encrypted blob for account-recovery / notifications.

        Returns
        -------
        user_id : str
            A freshly generated unique identifier for this user.

        Raises
        ------
        ValueError
            If a user with the same credential_hash already exists.
        """
        with self._lock:
            if credential_hash in self._users:
                raise ValueError("credential_hash already registered")

            user_id = secrets.token_hex(16)  # 128-bit random id
            user_salt = secrets.token_hex(32)  # 256-bit random salt

            record = _UserRecord(
                user_id=user_id,
                credential_hash=credential_hash,
                user_salt=user_salt,
                encrypted_contact=encrypted_contact,
            )
            self._users[credential_hash] = record
            self._users_by_id[user_id] = record
            return user_id

    # -----------------------------------------------------------------
    # 2. authenticate
    # -----------------------------------------------------------------
    def authenticate(
        self,
        credential_hash: str,
    ) -> Tuple[str, str, str]:
        """Authenticate with a credential hash.

        Returns
        -------
        (user_id, user_salt, session_token)

        Raises
        ------
        KeyError
            If the credential_hash is not registered.
        """
        with self._lock:
            record = self._users.get(credential_hash)
            if record is None:
                raise KeyError("unknown credential_hash")

            session_token = secrets.token_hex(32)
            self._sessions[session_token] = _Session(
                session_token=session_token,
                user_id=record.user_id,
            )
            return record.user_id, record.user_salt, session_token

    # -----------------------------------------------------------------
    # 3. register_routing_key (called by the client after auth)
    # -----------------------------------------------------------------
    def register_routing_key(
        self,
        routing_key: str,
        user_id: str,
    ) -> None:
        """Associate a routing_key with a user_id.

        This lets external parties send encrypted messages addressed
        to the routing_key, which the SSO resolves to the user.
        """
        with self._lock:
            if user_id not in self._users_by_id:
                raise KeyError("unknown user_id")
            self._routing_table[routing_key] = user_id

    # -----------------------------------------------------------------
    # 4. route_message
    # -----------------------------------------------------------------
    def route_message(
        self,
        routing_key: str,
        encrypted_payload: str,
    ) -> bool:
        """Deliver an encrypted message via routing key.

        The SSO cannot decrypt the payload — it simply stores it in
        the recipient's inbox.

        Returns
        -------
        bool
            True if the routing_key was found and the message delivered.
        """
        with self._lock:
            user_id = self._routing_table.get(routing_key)
            if user_id is None:
                return False
            record = self._users_by_id.get(user_id)
            if record is None:
                return False
            record.inbox.append((routing_key, encrypted_payload))
            return True

    # -----------------------------------------------------------------
    # 5. fetch_inbox  (helper for the user)
    # -----------------------------------------------------------------
    def fetch_inbox(self, user_id: str) -> List[Tuple[str, str]]:
        """Return and clear the inbox for *user_id*."""
        with self._lock:
            record = self._users_by_id.get(user_id)
            if record is None:
                raise KeyError("unknown user_id")
            messages = list(record.inbox)
            record.inbox.clear()
            return messages

    # -----------------------------------------------------------------
    # Introspection helpers (for testing assertions)
    # -----------------------------------------------------------------
    def _get_user_record(self, credential_hash: str) -> Optional[_UserRecord]:
        return self._users.get(credential_hash)

    def _get_user_salt(self, credential_hash: str) -> Optional[str]:
        rec = self._users.get(credential_hash)
        return rec.user_salt if rec else None

    def has_routing_key(self, routing_key: str) -> bool:
        return routing_key in self._routing_table
