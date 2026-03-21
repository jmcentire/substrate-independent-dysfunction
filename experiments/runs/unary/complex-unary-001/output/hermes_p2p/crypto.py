"""
Cryptographic primitives for HermesP2P:
- Ed25519 keypairs for node identity
- X25519 key exchange for onion routing
- AES-GCM for symmetric encryption (private channels)
- Onion layered encryption with constant-size packets
"""

import os
import struct
from typing import List, Tuple, Optional

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


# Fixed packet size for constant-size onion routing
PACKET_SIZE = 4096
# Size of ephemeral public key (X25519 = 32 bytes)
EPHEMERAL_KEY_SIZE = 32
# AES-GCM nonce
NONCE_SIZE = 12
# AES-GCM tag
TAG_SIZE = 16
# Node address field (we use a simple node_id representation)
NODE_ID_SIZE = 32
# Per-layer overhead
LAYER_OVERHEAD = EPHEMERAL_KEY_SIZE + NONCE_SIZE + TAG_SIZE + NODE_ID_SIZE + 1 + 4
# Maximum payload after all layers of onion
MAX_PAYLOAD = PACKET_SIZE - (LAYER_OVERHEAD * 4)  # 4 layers max


class KeyManager:
    """Manages Ed25519 identity keys and X25519 encryption keys for a node."""

    def __init__(self):
        self._ed25519_private = Ed25519PrivateKey.generate()
        self._ed25519_public = self._ed25519_private.public_key()
        self._x25519_private = X25519PrivateKey.generate()
        self._x25519_public = self._x25519_private.public_key()

    @property
    def signing_key(self) -> Ed25519PrivateKey:
        return self._ed25519_private

    @property
    def verify_key(self) -> Ed25519PublicKey:
        return self._ed25519_public

    @property
    def encryption_private_key(self) -> X25519PrivateKey:
        return self._x25519_private

    @property
    def encryption_public_key(self) -> X25519PublicKey:
        return self._x25519_public

    def public_key_bytes(self) -> bytes:
        """Get Ed25519 public key as raw bytes (32 bytes)."""
        return self._ed25519_public.public_bytes_raw()

    def encryption_public_key_bytes(self) -> bytes:
        """Get X25519 public key as raw bytes (32 bytes)."""
        return self._x25519_public.public_bytes_raw()

    def sign(self, data: bytes) -> bytes:
        """Sign data with Ed25519."""
        return self._ed25519_private.sign(data)

    @staticmethod
    def verify(public_key_bytes: bytes, signature: bytes, data: bytes) -> bool:
        """Verify an Ed25519 signature."""
        try:
            pub = Ed25519PublicKey.from_public_bytes(public_key_bytes)
            pub.verify(signature, data)
            return True
        except Exception:
            return False


def _derive_shared_key(
    private: X25519PrivateKey, public: X25519PublicKey
) -> bytes:
    """Derive a 256-bit AES key from X25519 shared secret via HKDF."""
    shared = private.exchange(public)
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"hermes-p2p-onion-v1",
    ).derive(shared)
    return key


def _aes_encrypt(key: bytes, plaintext: bytes) -> Tuple[bytes, bytes]:
    """Encrypt with AES-256-GCM. Returns (nonce, ciphertext_with_tag)."""
    nonce = os.urandom(NONCE_SIZE)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, plaintext, None)
    return nonce, ct


def _aes_decrypt(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    """Decrypt AES-256-GCM."""
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)


def symmetric_encrypt(channel_key: bytes, plaintext: bytes) -> bytes:
    """Encrypt for a private channel using symmetric key. Returns nonce + ciphertext."""
    nonce, ct = _aes_encrypt(channel_key, plaintext)
    return nonce + ct


def symmetric_decrypt(channel_key: bytes, data: bytes) -> bytes:
    """Decrypt private channel data."""
    nonce = data[:NONCE_SIZE]
    ct = data[NONCE_SIZE:]
    return _aes_decrypt(channel_key, nonce, ct)


class OnionPacket:
    """Represents a constant-size onion-encrypted packet."""

    def __init__(self, data: bytes):
        if len(data) > PACKET_SIZE:
            raise ValueError(f"Packet too large: {len(data)} > {PACKET_SIZE}")
        # Pad to constant size
        self.data = data.ljust(PACKET_SIZE, b'\x00')

    def to_bytes(self) -> bytes:
        return self.data


class OnionRouter:
    """
    Constructs and peels onion-encrypted packets.

    Layering for a route sender → R1 → R2 → R3 → recipient:
    
    Build from inside out:
    1. Encrypt payload for recipient (flag=1, final)
    2. Wrap for R3 with next_hop = recipient_node_id
    3. Wrap for R2 with next_hop = R3's node_id
    4. Wrap for R1 with next_hop = R2's node_id
    
    Each layer format (encrypted blob):
        [flag: 1B][next_hop_node_id: 32B][inner_len: 4B][inner_data: ...]
    
    flag=0: relay → decrypt, extract next_hop, forward inner to next_hop
    flag=1: final → inner data is the plaintext payload
    
    Outer format: [ephemeral_pubkey: 32B][nonce: 12B][ciphertext+tag]
    """

    @staticmethod
    def build_onion(
        payload: bytes,
        route: List[Tuple[str, bytes]],
        recipient_node_id: str,
        recipient_encryption_pubkey: bytes,
    ) -> OnionPacket:
        """
        Build an onion packet.
        
        Args:
            payload: The cleartext payload (will be encrypted for recipient).
            route: List of (node_id, x25519_public_key_bytes) for relay hops.
                   route[0] is the first relay, route[-1] is the last before recipient.
            recipient_node_id: The node_id of the final recipient.
            recipient_encryption_pubkey: X25519 public key of the final recipient.
        
        Returns:
            OnionPacket of constant size.
        """
        # Step 1: Encrypt the payload for the recipient (innermost layer)
        recipient_pub = X25519PublicKey.from_public_bytes(recipient_encryption_pubkey)
        ephemeral_priv = X25519PrivateKey.generate()
        ephemeral_pub = ephemeral_priv.public_key().public_bytes_raw()
        shared_key = _derive_shared_key(ephemeral_priv, recipient_pub)

        # Inner content: flag=1 (final) + payload
        inner_content = bytes([1]) + payload
        nonce, ct = _aes_encrypt(shared_key, inner_content)
        current_layer = ephemeral_pub + nonce + ct

        # Step 2: Build the chain of next_hops for wrapping
        # After the last relay, the next hop is the recipient
        # After relay i, the next hop is relay i+1 (or recipient if last)
        # We need to wrap in reverse order
        
        # Build next_hop chain: for each relay, what's the next hop?
        # route = [R1, R2, R3], recipient
        # R1 → forward to R2
        # R2 → forward to R3
        # R3 → forward to recipient
        
        next_hops = []
        for i in range(len(route)):
            if i == len(route) - 1:
                next_hops.append(recipient_node_id)
            else:
                next_hops.append(route[i + 1][0])
        
        # Wrap in reverse: R3 first (innermost relay), then R2, then R1
        for i in range(len(route) - 1, -1, -1):
            relay_node_id, relay_pub_bytes = route[i]
            next_hop_id = next_hops[i]
            
            relay_pub = X25519PublicKey.from_public_bytes(relay_pub_bytes)
            eph_priv = X25519PrivateKey.generate()
            eph_pub = eph_priv.public_key().public_bytes_raw()
            sk = _derive_shared_key(eph_priv, relay_pub)

            nid_bytes = next_hop_id.encode("utf-8") if isinstance(next_hop_id, str) else next_hop_id
            nid_padded = nid_bytes[:NODE_ID_SIZE].ljust(NODE_ID_SIZE, b'\x00')

            relay_content = (
                bytes([0])
                + nid_padded
                + struct.pack("!I", len(current_layer))
                + current_layer
            )
            nonce, ct = _aes_encrypt(sk, relay_content)
            current_layer = eph_pub + nonce + ct

        return OnionPacket(current_layer)

    @staticmethod
    def peel_layer(
        packet_data: bytes,
        my_x25519_private: X25519PrivateKey,
    ) -> Tuple[Optional[str], Optional[bytes], bool]:
        """
        Peel one layer of the onion.
        
        Returns:
            (next_hop_node_id, remaining_data, is_final)
            - If is_final=True, remaining_data is the decrypted payload.
            - If is_final=False, remaining_data is the onion packet for next_hop
              (padded to PACKET_SIZE for constant size).
        """
        # Strip trailing zero padding
        data = packet_data.rstrip(b'\x00')
        if len(data) < EPHEMERAL_KEY_SIZE + NONCE_SIZE + TAG_SIZE + 1:
            raise ValueError("Packet too small to peel")

        eph_pub_bytes = data[:EPHEMERAL_KEY_SIZE]
        remaining = data[EPHEMERAL_KEY_SIZE:]

        eph_pub = X25519PublicKey.from_public_bytes(eph_pub_bytes)
        shared_key = _derive_shared_key(my_x25519_private, eph_pub)

        nonce = remaining[:NONCE_SIZE]
        ct = remaining[NONCE_SIZE:]

        plaintext = _aes_decrypt(shared_key, nonce, ct)

        flag = plaintext[0]
        if flag == 1:
            # Final destination
            return None, plaintext[1:], True
        elif flag == 0:
            # Relay
            node_id_bytes = plaintext[1:1 + NODE_ID_SIZE]
            node_id = node_id_bytes.rstrip(b'\x00').decode("utf-8")
            inner_len = struct.unpack("!I", plaintext[1 + NODE_ID_SIZE:1 + NODE_ID_SIZE + 4])[0]
            inner_data = plaintext[1 + NODE_ID_SIZE + 4:1 + NODE_ID_SIZE + 4 + inner_len]
            # Re-pad to constant size
            padded = inner_data.ljust(PACKET_SIZE, b'\x00')
            return node_id, padded, False
        else:
            raise ValueError(f"Unknown flag: {flag}")
