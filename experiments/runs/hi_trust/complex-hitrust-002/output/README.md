# HermesP2P — Channel Types (`src/channels.py`)

## What Was Built

`src/channels.py` provides the shared data types, cryptographic helpers, and channel implementations for the HermesP2P channel system.

### Components

#### `MessageEnvelope` (dataclass)
A flexible message container with all fields Optional, supporting three channel types:

| Field | Type | Used By |
|---|---|---|
| `channel_id` | `str` | All channels |
| `sender_id` | `str` | Public channels (hex-encoded Ed25519 pubkey) |
| `payload` | `bytes` | Public channels (plaintext) |
| `signature` | `bytes` | Public channels (Ed25519 signature) |
| `nonce` | `bytes` | Private channels (AES-GCM nonce) |
| `encrypted_payload` | `bytes` | Private channels (AES-GCM ciphertext+tag) |
| `onion_layers` | `bytes` | Direct messages (onion-encrypted blob) |

#### `NodeIdentity`
Ed25519 signing/verification key pair wrapper:
- `NodeIdentity.generate()` — create a new identity with random keys
- `sign(data) -> bytes` — produce a 64-byte Ed25519 signature
- `verify(signature, data) -> bool` — verify a signature against this identity's public key
- `verify_with_public_key(pubkey_bytes, signature, data) -> bool` — static method for verification with raw public key bytes
- `public_key_bytes` — 32-byte raw Ed25519 public key

#### `ChannelKey`
AES-256-GCM symmetric encryption helper:
- `ChannelKey.generate()` — create a new random 256-bit key
- `encrypt(plaintext) -> bytes` — returns `nonce(12) ‖ ciphertext ‖ tag(16)`
- `decrypt(blob) -> bytes` — decrypts a blob produced by `encrypt()`
- `encrypt_with_key(key, plaintext)` / `decrypt_with_key(key, blob)` — static methods accepting raw key bytes
- `key_bytes` — the raw 32-byte symmetric key

#### `PublicChannel`
A signed broadcast channel where messages carry Ed25519 signatures for authenticity:

- **Constructor**: `PublicChannel(channel_id: str, subscribers: set[str] = set())`
- **`create_message(node_identity, payload) -> MessageEnvelope`** — signs payload with Ed25519
- **`verify_message(envelope) -> bool`** — verifies embedded signature
- **`subscribe(node_id)` / `unsubscribe(node_id)`** — manage subscriber set

#### `PrivateChannel`
A symmetric-key encrypted channel using AES-256-GCM. Members share a 32-byte key; non-members cannot read messages.

- **Constructor**: `PrivateChannel(channel_id: str, key: bytes)`
  - `channel_id` — unique string identifier for the channel
  - `key` — a 32-byte AES-256-GCM symmetric key (raises `ValueError` if not exactly 32 bytes)

- **`create_message(channel_key: bytes, payload: bytes) -> MessageEnvelope`**
  Encrypts `payload` with the given AES-256-GCM key. Returns a `MessageEnvelope` with:
  - `channel_id` — this channel's ID
  - `nonce` — 12-byte random AES-GCM nonce (also stored separately for convenience)
  - `encrypted_payload` — `nonce(12) ‖ ciphertext ‖ GCM-tag(16)` (self-contained blob)

- **`decrypt_message(channel_key: bytes, envelope: MessageEnvelope) -> bytes`**
  Decrypts the `encrypted_payload` from the envelope. Returns the original plaintext bytes.
  Raises `ValueError` if:
  - The key is the wrong length
  - The envelope has no `encrypted_payload`
  - The blob is too short
  - Decryption fails (wrong key, tampered ciphertext, etc.)

## Usage

```python
import os
from src.channels import (
    MessageEnvelope, NodeIdentity, ChannelKey,
    PublicChannel, PrivateChannel,
)

# --- Public Channel (signed broadcast) ---
alice = NodeIdentity.generate()
channel = PublicChannel("announcements", {"node-1", "node-2"})
envelope = channel.create_message(alice, b"Hello subscribers!")
assert channel.verify_message(envelope) is True

# Tamper with the payload → verification fails
envelope.payload = b"TAMPERED"
assert channel.verify_message(envelope) is False

# --- Private Channel (AES-256-GCM encrypted) ---
channel_key = os.urandom(32)
priv_ch = PrivateChannel("secret-room", key=channel_key)

# Encrypt a message
envelope = priv_ch.create_message(channel_key, b"top secret payload")

# Decrypt with the correct key
plaintext = priv_ch.decrypt_message(channel_key, envelope)
assert plaintext == b"top secret payload"

# Wrong key raises ValueError
wrong_key = os.urandom(32)
try:
    priv_ch.decrypt_message(wrong_key, envelope)
except ValueError as e:
    print(f"Decryption failed (as expected): {e}")

# --- Ed25519 signing (low-level) ---
sig = alice.sign(b"hello world")
assert alice.verify(sig, b"hello world")

# --- AES-256-GCM encryption (low-level helper) ---
key = ChannelKey.generate()
ciphertext = key.encrypt(b"secret data")
assert key.decrypt(ciphertext) == b"secret data"
```

## Running Tests

```bash
python3 -m pytest tests/test_channels.py -v
```

## Dependencies

- Python 3.10+
- `cryptography` library (for Ed25519 and AES-GCM)
- `pytest` (for tests)
