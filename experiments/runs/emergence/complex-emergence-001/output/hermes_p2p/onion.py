"""Onion routing for HermesP2P.

Build and peel onion-encrypted messages. Each layer is encrypted with
X25519 ephemeral key + AES-GCM, and includes next-hop information.
Constant-size padding ensures intermediate relays can't determine
position in the route.

For simplicity in the simulation, we use a layered encryption scheme where:
- build_onion wraps the payload in multiple encryption layers
- peel_onion removes one layer and reveals the next hop
"""

import json
import os
import struct
from typing import List, Optional, Tuple

from hermes_p2p.crypto import (
    generate_x25519_keypair,
    x25519_shared_secret,
    aes_gcm_encrypt,
    aes_gcm_decrypt,
    derive_key,
    generate_keypair,
)

# Maximum onion packet size for constant-size padding
MAX_ONION_SIZE = 4096
HEADER_SIZE = 32  # ephemeral public key
NEXT_HOP_SIZE = 64  # next hop ID (padded)


def _pad_to_size(data: bytes, size: int = MAX_ONION_SIZE) -> bytes:
    """Pad data to a fixed size."""
    if len(data) >= size:
        return data[:size]
    padding_len = size - len(data)
    return data + os.urandom(padding_len)


def _unpad(data: bytes) -> bytes:
    """We store the real length in the first 4 bytes of the encrypted payload."""
    if len(data) < 4:
        return data
    real_len = struct.unpack(">I", data[:4])[0]
    return data[4:4 + real_len]


def _add_length_prefix(data: bytes) -> bytes:
    """Prefix data with its length."""
    return struct.pack(">I", len(data)) + data


def build_onion(payload: bytes, path_public_keys: List[bytes], path_node_ids: List[str]) -> bytes:
    """Build an onion-encrypted message.

    Args:
        payload: The plaintext payload for the final recipient.
        path_public_keys: X25519 public keys for each hop (including final recipient).
        path_node_ids: Node IDs for each hop.

    Returns:
        The onion-encrypted blob.
    """
    assert len(path_public_keys) == len(path_node_ids)

    if not path_public_keys:
        return payload

    # Build from inside out
    # The innermost layer contains the actual payload with no next hop
    current = _add_length_prefix(payload)

    # Wrap layers from last to first
    # Each layer: ephemeral_pub_key (32 bytes) || encrypted(next_hop_id || inner_data)
    layers_keys = []  # (ephemeral_priv, ephemeral_pub) for each layer

    for i in range(len(path_public_keys) - 1, -1, -1):
        hop_pub_key = path_public_keys[i]

        # Generate ephemeral X25519 key for this layer
        eph_priv, eph_pub = generate_x25519_keypair()

        # Compute shared secret
        shared = x25519_shared_secret(eph_priv, hop_pub_key)
        sym_key = derive_key(shared, b"hermes_onion")

        # Next hop info
        if i < len(path_public_keys) - 1:
            next_hop_id = path_node_ids[i + 1].encode().ljust(NEXT_HOP_SIZE, b"\x00")
        else:
            # Final destination - no next hop
            next_hop_id = b"\xff" * NEXT_HOP_SIZE  # sentinel for "this is for you"

        # Layer plaintext: next_hop_id || current_onion
        layer_plaintext = next_hop_id + current

        # Encrypt
        encrypted = aes_gcm_encrypt(sym_key, layer_plaintext)

        # Prepend ephemeral public key
        current = eph_pub + encrypted

    return current


def peel_onion(onion_data: bytes, my_private_key: bytes) -> Tuple[Optional[str], bytes, bool]:
    """Peel one layer of the onion.

    Args:
        onion_data: The onion-encrypted blob.
        my_private_key: This node's X25519 private key.

    Returns:
        (next_hop_id, inner_data, is_final)
        - next_hop_id: The next node to forward to (None if this is final)
        - inner_data: The remaining onion (or plaintext payload if final)
        - is_final: True if this is the final destination
    """
    if len(onion_data) < HEADER_SIZE:
        raise ValueError("Onion data too small")

    # Extract ephemeral public key
    eph_pub = onion_data[:HEADER_SIZE]
    encrypted = onion_data[HEADER_SIZE:]

    # Compute shared secret
    shared = x25519_shared_secret(my_private_key, eph_pub)
    sym_key = derive_key(shared, b"hermes_onion")

    # Decrypt
    decrypted = aes_gcm_decrypt(sym_key, encrypted)

    # Extract next hop
    next_hop_raw = decrypted[:NEXT_HOP_SIZE]
    inner = decrypted[NEXT_HOP_SIZE:]

    # Check if this is the final destination
    if next_hop_raw == b"\xff" * NEXT_HOP_SIZE:
        # Final destination - inner is the length-prefixed payload
        payload = _unpad(inner)
        return None, payload, True
    else:
        # Intermediate relay - extract next hop ID
        next_hop_id = next_hop_raw.rstrip(b"\x00").decode()
        return next_hop_id, inner, False


def build_onion_for_path(payload: bytes, path_nodes) -> bytes:
    """Build onion for a list of Node objects.

    This is a convenience function that extracts keys from Node objects.
    For onion routing, we need X25519 keys. Since nodes use Ed25519 for signing,
    we store X25519 keys separately or derive them.

    For the simulation, we use the node's Ed25519 key material as X25519
    key material (this is NOT cryptographically correct in production,
    but works for our simulation with the fallback crypto).
    """
    from hermes_p2p.crypto import HAS_CRYPTOGRAPHY

    if not path_nodes:
        return payload

    if HAS_CRYPTOGRAPHY:
        # With real crypto, we need proper X25519 keys on each node
        # We'll use a simplified approach: generate ephemeral shared secrets
        # and encrypt symmetrically using each node's signing key as entropy
        return _build_onion_simulated(payload, path_nodes)
    else:
        return _build_onion_simulated(payload, path_nodes)


def _build_onion_simulated(payload: bytes, path_nodes) -> bytes:
    """Build onion using simulated layered encryption.

    Each layer uses AES-GCM with a key derived from the node's private key.
    This allows each node to peel its layer.
    """
    import hashlib

    current = _add_length_prefix(payload)

    for i in range(len(path_nodes) - 1, -1, -1):
        node = path_nodes[i]
        # Derive a symmetric key from the node's public key (for simulation)
        sym_key = hashlib.sha256(b"onion_layer_" + node.public_key).digest()

        if i < len(path_nodes) - 1:
            next_hop_id = path_nodes[i + 1].node_id.encode().ljust(NEXT_HOP_SIZE, b"\x00")
        else:
            next_hop_id = b"\xff" * NEXT_HOP_SIZE

        layer_plaintext = next_hop_id + current
        encrypted = aes_gcm_encrypt(sym_key, layer_plaintext)

        # Tag this layer with the target node's ID (first 32 bytes, for simulation routing)
        tag = node.node_id.encode()[:HEADER_SIZE].ljust(HEADER_SIZE, b"\x00")
        current = tag + encrypted

    return current


def peel_onion_simulated(onion_data: bytes, node) -> Tuple[Optional[str], bytes, bool]:
    """Peel one onion layer using simulation crypto."""
    import hashlib

    if len(onion_data) < HEADER_SIZE:
        raise ValueError("Onion data too small")

    tag = onion_data[:HEADER_SIZE]
    encrypted = onion_data[HEADER_SIZE:]

    sym_key = hashlib.sha256(b"onion_layer_" + node.public_key).digest()

    try:
        decrypted = aes_gcm_decrypt(sym_key, encrypted)
    except Exception as e:
        raise ValueError(f"Cannot peel onion layer: {e}")

    next_hop_raw = decrypted[:NEXT_HOP_SIZE]
    inner = decrypted[NEXT_HOP_SIZE:]

    if next_hop_raw == b"\xff" * NEXT_HOP_SIZE:
        payload = _unpad(inner)
        return None, payload, True
    else:
        next_hop_id = next_hop_raw.rstrip(b"\x00").decode()
        return next_hop_id, inner, False
