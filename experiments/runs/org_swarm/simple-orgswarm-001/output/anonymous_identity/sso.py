"""SSO server implementation for the Anonymous Identity system."""

import secrets
import uuid


class SSO:
    """Single Sign-On server.

    Exposes:
      - register(credential_hash, encrypted_contact) -> user_id
      - authenticate(credential_hash) -> (user_id, user_salt, session)
      - register_routing_key(routing_key, user_id) -> None
      - route_message(routing_key, encrypted_payload) -> 'delivered'

    The SSO never learns which sites the user visits and never sees the password.
    """

    def __init__(self):
        # credential_hash -> {user_id, user_salt, encrypted_contact}
        self._users: dict[str, dict] = {}
        # routing_key -> user_id
        self._routing_keys: dict[str, str] = {}
        # session_token -> credential_hash
        self._sessions: dict[str, str] = {}
        # user_id -> list of delivered messages (for testing; real system would forward)
        self._delivered: dict[str, list] = {}

    def register(self, credential_hash: str, encrypted_contact: str) -> str:
        """Register a new user. Returns user_id."""
        if credential_hash in self._users:
            raise ValueError("User already registered")
        user_id = uuid.uuid4().hex
        user_salt = secrets.token_hex(16)
        self._users[credential_hash] = {
            "user_id": user_id,
            "user_salt": user_salt,
            "encrypted_contact": encrypted_contact,
        }
        self._delivered[user_id] = []
        return user_id

    def authenticate(self, credential_hash: str) -> tuple[str, str, str]:
        """Authenticate a user. Returns (user_id, user_salt, session_token)."""
        if credential_hash not in self._users:
            raise ValueError("Invalid credentials")
        user = self._users[credential_hash]
        session_token = secrets.token_hex(32)
        self._sessions[session_token] = credential_hash
        return (user["user_id"], user["user_salt"], session_token)

    def register_routing_key(self, routing_key: str, user_id: str) -> None:
        """Register a routing key that maps to a user_id."""
        self._routing_keys[routing_key] = user_id

    def route_message(self, routing_key: str, encrypted_payload: str) -> str:
        """Route an encrypted message via routing key. Returns 'delivered'.

        Raises KeyError if the routing key is unknown.
        """
        if routing_key not in self._routing_keys:
            raise KeyError(f"Unknown routing key: {routing_key}")
        user_id = self._routing_keys[routing_key]
        # Deliver the message (store for the user, discard plaintext association)
        if user_id not in self._delivered:
            self._delivered[user_id] = []
        self._delivered[user_id].append(encrypted_payload)
        return "delivered"

    def get_user_salt(self, credential_hash: str) -> str:
        """Get the user_salt for a given credential hash (used internally)."""
        if credential_hash not in self._users:
            raise ValueError("Invalid credentials")
        return self._users[credential_hash]["user_salt"]
