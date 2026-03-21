"""
HermesP2P Cryptographic Foundation
===================================

Provides:
  - NodeIdentity: Ed25519 keypair wrapper (generate, sign, verify, serialize).
  - Onion routing: build_onion() layers encryption for a multi-hop route;
    peel_onion() removes one layer revealing the next hop.
  - Constant-size packets: every onion packet is exactly FIXED_PACKET_SIZE bytes
    after each peel, hiding the relay's position in the route.
  - Symmetric channel encryption: AES-256-GCM encrypt/decrypt for private channels.

Onion packet format (per layer)::

    [32B eph_pub][12B nonce][2B ct_len][ciphertext(ct_len)][random_padding]
    Total = FIXED_PACKET_SIZE = 4096

Decrypted plaintext inside ciphertext::

    Relay layer:  [0x00 flags][32B next_hop][inner_core_bytes]
    Final layer:  [0x01 flags][4B payload_len][payload_bytes]

The "core" of a layer is everything except the trailing random padding:
eph_pub + nonce + ct_len + ciphertext.  Cores shrink at each layer; after
peeling, the relay re-pads to FIXED_PACKET_SIZE so packet size is constant.

Uses the ``cryptography`` library:
  - Ed25519 for node identity / signing
  - X25519 for ephemeral key exchange at each onion layer
  - AES-256-GCM for symmetric encryption
"""

from __future__ import annotations

import os
import struct
from typing import Tuple, List, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIXED_PACKET_SIZE = 4096  # bytes — every onion packet on the wire is this size

_EPHEMERAL_KEY_SIZE = 32  # X25519 raw public key
_NONCE_SIZE = 12          # AES-GCM nonce
_TAG_SIZE = 16            # AES-GCM authentication tag
_CT_LEN_SIZE = 2          # big-endian uint16 — ciphertext length
_FLAG_SIZE = 1
_HOP_ADDR_SIZE = 32       # X25519 raw public key used as next-hop address
_LENGTH_PREFIX_SIZE = 4   # big-endian uint32 — payload length (final hop)

# Overhead per layer (outside the encrypted payload)
_LAYER_OUTER_OVERHEAD = _EPHEMERAL_KEY_SIZE + _NONCE_SIZE + _CT_LEN_SIZE + _TAG_SIZE  # 62

# Overhead inside the plaintext for a relay hop
_RELAY_INNER_OVERHEAD = _FLAG_SIZE + _HOP_ADDR_SIZE  # 33

# Overhead inside the plaintext for the final hop
_FINAL_INNER_OVERHEAD = _FLAG_SIZE + _LENGTH_PREFIX_SIZE  # 5

# Core header (unencrypted prefix before ciphertext)
_CORE_HEADER_SIZE = _EPHEMERAL_KEY_SIZE + _NONCE_SIZE + _CT_LEN_SIZE  # 46

# Flags
_FLAG_FINAL_HOP = 0x01


# ---------------------------------------------------------------------------
# NodeIdentity — Ed25519 + X25519 keypair wrapper
# ---------------------------------------------------------------------------

class NodeIdentity:
    """
    Each node holds an Ed25519 keypair (signing) and an X25519 keypair
    (Diffie-Hellman for onion routing).  The X25519 public key serves as the
    node's "onion address".
    """

    def __init__(
        self,
        ed_private: Optional[Ed25519PrivateKey] = None,
        x_private: Optional[X25519PrivateKey] = None,
    ):
        self._ed_private = ed_private or Ed25519PrivateKey.generate()
        self._ed_public = self._ed_private.public_key()
        self._x_private = x_private or X25519PrivateKey.generate()
        self._x_public = self._x_private.public_key()

    @classmethod
    def generate(cls) -> "NodeIdentity":
        """Create a brand-new identity with random keys."""
        return cls()

    # -- Ed25519 signing ----------------------------------------------------

    def sign(self, data: bytes) -> bytes:
        """Sign *data*; return a 64-byte Ed25519 signature."""
        return self._ed_private.sign(data)

    def verify(self, signature: bytes, data: bytes) -> bool:
        """Verify *signature* over *data* with this node's Ed25519 public key."""
        try:
            self._ed_public.verify(signature, data)
            return True
        except Exception:
            return False

    @staticmethod
    def verify_with_public_key(
        public_key_bytes: bytes, signature: bytes, data: bytes
    ) -> bool:
        """Verify using a raw 32-byte Ed25519 public key."""
        try:
            pk = Ed25519PublicKey.from_public_bytes(public_key_bytes)
            pk.verify(signature, data)
            return True
        except Exception:
            return False

    # -- serialization / properties -----------------------------------------

    @property
    def ed25519_public_bytes(self) -> bytes:
        """32-byte raw Ed25519 public key."""
        return self._ed_public.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

    @property
    def x25519_public_bytes(self) -> bytes:
        """32-byte raw X25519 public key (onion address)."""
        return self._x_public.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

    @property
    def x25519_private_key(self) -> X25519PrivateKey:
        return self._x_private

    @property
    def x25519_public_key(self) -> X25519PublicKey:
        return self._x_public

    @property
    def ed25519_private_key(self) -> Ed25519PrivateKey:
        return self._ed_private

    @property
    def ed25519_public_key(self) -> Ed25519PublicKey:
        return self._ed_public

    def __repr__(self) -> str:
        return (
            f"<NodeIdentity ed={self.ed25519_public_bytes.hex()[:16]}… "
            f"x={self.x25519_public_bytes.hex()[:16]}…>"
        )


# ---------------------------------------------------------------------------
# Low-level helpers — X25519 DH + AES-256-GCM
# ---------------------------------------------------------------------------

def _derive_shared_key(
    private_key: X25519PrivateKey,
    peer_public_key: X25519PublicKey,
) -> bytes:
    """X25519 DH → HKDF-SHA256 → 32-byte AES key."""
    shared_secret = private_key.exchange(peer_public_key)
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"hermesp2p-onion-layer",
    ).derive(shared_secret)


def _aes_gcm_encrypt(key: bytes, plaintext: bytes) -> Tuple[bytes, bytes]:
    """Encrypt → (nonce, ciphertext‖tag). The library appends the 16-byte tag."""
    nonce = os.urandom(_NONCE_SIZE)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return nonce, ct


def _aes_gcm_decrypt(key: bytes, nonce: bytes, ciphertext_with_tag: bytes) -> bytes:
    """Decrypt AES-256-GCM *ciphertext_with_tag* (tag appended)."""
    return AESGCM(key).decrypt(nonce, ciphertext_with_tag, None)


# ---------------------------------------------------------------------------
# Onion Routing — build
# ---------------------------------------------------------------------------

def max_onion_payload(num_hops: int) -> int:
    """
    Maximum plaintext payload for *num_hops* (including the final recipient).

    Each hop adds ``_LAYER_OUTER_OVERHEAD`` (62 bytes) outside the ciphertext.
    Each relay hop adds ``_RELAY_INNER_OVERHEAD`` (33 bytes) inside.
    The final hop adds ``_FINAL_INNER_OVERHEAD`` (5 bytes) inside.
    """
    total_overhead = (
        num_hops * _LAYER_OUTER_OVERHEAD
        + (num_hops - 1) * _RELAY_INNER_OVERHEAD
        + _FINAL_INNER_OVERHEAD
    )
    return FIXED_PACKET_SIZE - total_overhead


def build_onion(payload: bytes, route: List[bytes]) -> bytes:
    """
    Construct a multi-layer onion-encrypted packet.

    Parameters
    ----------
    payload : bytes
        Plaintext for the final recipient.
    route : list[bytes]
        Ordered 32-byte X25519 public keys ``[relay_1, …, recipient]``.

    Returns
    -------
    bytes
        A ``FIXED_PACKET_SIZE``-byte onion packet addressed to ``route[0]``.
    """
    if not route:
        raise ValueError("route must contain at least one public key")

    max_cap = max_onion_payload(len(route))
    if len(payload) > max_cap:
        raise ValueError(
            f"Payload too large ({len(payload)} B) for {len(route)} hops "
            f"(max {max_cap} B)."
        )

    # Innermost layer — for the final recipient
    inner_pt = (
        bytes([_FLAG_FINAL_HOP])
        + struct.pack(">I", len(payload))
        + payload
    )
    current_core = _encrypt_layer_core(route[-1], inner_pt)

    # Wrap relay layers inside-out
    for i in range(len(route) - 2, -1, -1):
        relay_pt = (
            bytes([0x00])       # flags: not final
            + route[i + 1]      # 32-byte next-hop address
            + current_core      # inner layer's core (variable size)
        )
        current_core = _encrypt_layer_core(route[i], relay_pt)

    # Pad outermost core → FIXED_PACKET_SIZE
    return _pad_to_fixed(current_core)


def _encrypt_layer_core(peer_pub_bytes: bytes, plaintext: bytes) -> bytes:
    """
    Encrypt one onion layer and return the *core* (no trailing padding)::

        [32B eph_pub][12B nonce][2B ct_len][ciphertext]
    """
    peer_pub = X25519PublicKey.from_public_bytes(peer_pub_bytes)
    eph_priv = X25519PrivateKey.generate()
    eph_pub_raw = eph_priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    aes_key = _derive_shared_key(eph_priv, peer_pub)
    nonce, ct = _aes_gcm_encrypt(aes_key, plaintext)

    if len(ct) > 0xFFFF:
        raise ValueError("Ciphertext exceeds 2-byte length field")

    return eph_pub_raw + nonce + struct.pack(">H", len(ct)) + ct


def _pad_to_fixed(data: bytes) -> bytes:
    """Pad *data* to ``FIXED_PACKET_SIZE`` with random bytes."""
    if len(data) > FIXED_PACKET_SIZE:
        raise ValueError(
            f"Core ({len(data)} B) exceeds FIXED_PACKET_SIZE ({FIXED_PACKET_SIZE})"
        )
    if len(data) == FIXED_PACKET_SIZE:
        return data
    return data + os.urandom(FIXED_PACKET_SIZE - len(data))


# ---------------------------------------------------------------------------
# Onion Routing — peel
# ---------------------------------------------------------------------------

def peel_onion(
    encrypted_blob: bytes,
    private_key: X25519PrivateKey,
) -> Tuple[Optional[bytes], bytes]:
    """
    Remove one onion layer.

    Parameters
    ----------
    encrypted_blob : bytes
        A ``FIXED_PACKET_SIZE``-byte onion packet.
    private_key : X25519PrivateKey
        This node's X25519 private key.

    Returns
    -------
    (next_hop, remaining)
        *next_hop*: 32-byte X25519 public key of the next relay, or ``None``
        if this node is the final recipient.

        *remaining*: If relay → a ``FIXED_PACKET_SIZE`` packet to forward.
        If final → the decrypted plaintext payload (variable length).
    """
    if len(encrypted_blob) != FIXED_PACKET_SIZE:
        raise ValueError(
            f"Expected {FIXED_PACKET_SIZE}-byte packet, got {len(encrypted_blob)}"
        )

    # Parse core header
    eph_pub_bytes = encrypted_blob[:_EPHEMERAL_KEY_SIZE]
    nonce = encrypted_blob[_EPHEMERAL_KEY_SIZE : _EPHEMERAL_KEY_SIZE + _NONCE_SIZE]
    ct_len_offset = _EPHEMERAL_KEY_SIZE + _NONCE_SIZE
    ct_len = struct.unpack(
        ">H", encrypted_blob[ct_len_offset : ct_len_offset + _CT_LEN_SIZE]
    )[0]
    ct_start = ct_len_offset + _CT_LEN_SIZE
    ciphertext = encrypted_blob[ct_start : ct_start + ct_len]

    # Decrypt
    eph_pub = X25519PublicKey.from_public_bytes(eph_pub_bytes)
    aes_key = _derive_shared_key(private_key, eph_pub)
    try:
        plaintext = _aes_gcm_decrypt(aes_key, nonce, ciphertext)
    except Exception as e:
        raise ValueError(f"Onion decryption failed: {e}") from e

    # Parse flags
    flags = plaintext[0]
    is_final = bool(flags & _FLAG_FINAL_HOP)

    if is_final:
        # [flags(1)][payload_len(4)][payload]
        payload_len = struct.unpack(">I", plaintext[1:5])[0]
        return None, plaintext[5 : 5 + payload_len]
    else:
        # [flags(1)][next_hop(32)][inner_core]
        next_hop = plaintext[1 : 1 + _HOP_ADDR_SIZE]
        inner_core = plaintext[1 + _HOP_ADDR_SIZE :]
        return next_hop, _pad_to_fixed(inner_core)


# ---------------------------------------------------------------------------
# Symmetric Channel Encryption (Private Channels)
# ---------------------------------------------------------------------------

def symmetric_encrypt(channel_key: bytes, plaintext: bytes) -> bytes:
    """
    Encrypt *plaintext* with a 32-byte *channel_key* (AES-256-GCM).

    Returns ``nonce(12) ‖ ciphertext‖tag``.
    """
    if len(channel_key) != 32:
        raise ValueError("channel_key must be 32 bytes")
    nonce, ct = _aes_gcm_encrypt(channel_key, plaintext)
    return nonce + ct


def symmetric_decrypt(channel_key: bytes, blob: bytes) -> bytes:
    """
    Decrypt a blob produced by :func:`symmetric_encrypt`.

    *blob* = ``nonce(12) ‖ ciphertext‖tag``.
    """
    if len(channel_key) != 32:
        raise ValueError("channel_key must be 32 bytes")
    if len(blob) < _NONCE_SIZE + _TAG_SIZE:
        raise ValueError("blob too short")
    nonce = blob[:_NONCE_SIZE]
    ct = blob[_NONCE_SIZE:]
    return _aes_gcm_decrypt(channel_key, nonce, ct)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def generate_channel_key() -> bytes:
    """Generate a random 32-byte symmetric key for a private channel."""
    return os.urandom(32)
