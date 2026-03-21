# SSO Server Module (`src/sso.py`)

## Overview

Privacy-preserving Single Sign-On server for the Anonymous Identity system. The SSO authenticates users via credential hashes and facilitates anonymous message routing — **without ever learning which sites a user visits**.

## Architecture

```
Client                          SSO Server                      Site
  │                                │                              │
  │─── register(cred_hash, ───────>│  stores user_id, user_salt,  │
  │     encrypted_contact)         │  encrypted_contact           │
  │<── user_id ───────────────────│                              │
  │                                │                              │
  │─── authenticate(cred_hash) ──>│  looks up user record        │
  │<── (user_id, user_salt, ─────│                              │
  │     session_token)             │                              │
  │                                │                              │
  │  [client derives routing_key   │                              │
  │   = hash(username||"routing"   │                              │
  │   ||site_id||user_id||salt)]   │                              │
  │                                │                              │
  │─── register_routing_key ─────>│  stores opaque routing_key   │
  │     (session, routing_key)     │  → credential_hash mapping   │
  │                                │                              │
  │                                │<── route_message(routing_key,│
  │                                │     encrypted_payload) ──────│
  │                                │  resolves → contact info     │
  │                                │  delivers message            │
  │                                │  returns 'delivered' ───────>│
```

## Key Privacy Properties

1. **No raw passwords**: SSO only receives `hash(username || password)`.
2. **No site_id stored**: The SSO never sees or stores which site a routing key belongs to. Routing keys are opaque hashes.
3. **Unlinkable tokens**: Site-specific tokens are derived client-side. The SSO cannot compute them (doesn't know `username` or `site_id`).
4. **Opaque routing**: Sites send messages to routing keys. The SSO resolves them to contact info, delivers the message, and discards plaintext.

## API Reference

### `SSOServer`

#### `register(credential_hash: str, encrypted_contact: str) -> str`
Register a new user. Returns a random `user_id`. Generates a random `user_salt` stored internally.

#### `authenticate(credential_hash: str) -> (user_id, user_salt, session_token)`
Authenticate a user. Returns `user_id` and `user_salt` (for client-side token derivation) plus a random `session_token`. Raises `AuthError` on failure.

#### `register_routing_key(session_token: str, routing_key: str) -> None`
Register an opaque routing key for the authenticated user. Requires a valid session. Raises `AuthError` if session is invalid.

#### `route_message(routing_key: str, encrypted_payload: str) -> 'delivered'`
Route a message via an opaque routing key. Resolves to the user's contact info, appends to the delivery log, and returns `'delivered'`. Raises `ValueError` if the routing key is unknown.

### `AuthError`
Exception raised when authentication fails (unknown credential hash or invalid session token).

### Testing Helpers
- `get_delivery_log()` — Returns a copy of all delivery records.
- `get_user_record(credential_hash)` — Returns stored user record (test only).
- `is_session_valid(session_token)` — Check session validity.
- `revoke_session(session_token)` — Revoke a session (logout).
- `registered_user_count`, `active_session_count`, `routing_key_count` — Properties.

## Usage

```python
from src.sso import SSOServer, AuthError
import hashlib

sso = SSOServer()

# Client computes credential hash locally
cred_hash = hashlib.sha256(b"alice||mysecretpassword").hexdigest()

# Register
user_id = sso.register(cred_hash, encrypted_contact="encrypted_alice@mail.com")

# Authenticate
user_id, user_salt, session = sso.authenticate(cred_hash)

# Client derives routing key for a specific site (client-side only!)
routing_key = hashlib.sha256(
    f"alice||routing||shop.example.com||{user_id}||{user_salt}".encode()
).hexdigest()

# Register the opaque routing key with SSO
sso.register_routing_key(session, routing_key)

# Site sends a message via the routing key
result = sso.route_message(routing_key, "encrypted_order_confirmation")
assert result == "delivered"
```

## Running Tests

```bash
python3 -m pytest tests/test_sso.py -v
```
