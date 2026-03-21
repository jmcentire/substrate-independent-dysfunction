"""
Tests for src/crypto.py — HermesP2P cryptographic foundation.

Verifies:
  1. Onion layers peel correctly through 3 hops.
  2. Final recipient recovers the original plaintext.
  3. Intermediate relays cannot read the payload.
  4. Packet size is constant (FIXED_PACKET_SIZE) after each peel.
  5. Symmetric channel encryption round-trips correctly.
  6. NodeIdentity signing and verification.
  7. Edge cases and error handling.
"""

import os
import pytest

from src.crypto import (
    NodeIdentity,
    build_onion,
    peel_onion,
    symmetric_encrypt,
    symmetric_decrypt,
    generate_channel_key,
    max_onion_payload,
    FIXED_PACKET_SIZE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_route(n: int):
    """Create *n* NodeIdentity objects and return (identities, route_pub_keys)."""
    nodes = [NodeIdentity.generate() for _ in range(n)]
    route = [node.x25519_public_bytes for node in nodes]
    return nodes, route


# ---------------------------------------------------------------------------
# NodeIdentity tests
# ---------------------------------------------------------------------------

class TestNodeIdentity:
    def test_generate(self):
        node = NodeIdentity.generate()
        assert len(node.ed25519_public_bytes) == 32
        assert len(node.x25519_public_bytes) == 32

    def test_sign_and_verify(self):
        node = NodeIdentity.generate()
        data = b"test message"
        sig = node.sign(data)
        assert isinstance(sig, bytes)
        assert len(sig) == 64
        assert node.verify(sig, data) is True

    def test_verify_wrong_data(self):
        node = NodeIdentity.generate()
        sig = node.sign(b"correct")
        assert node.verify(sig, b"wrong") is False

    def test_verify_wrong_key(self):
        node_a = NodeIdentity.generate()
        node_b = NodeIdentity.generate()
        sig = node_a.sign(b"hello")
        assert node_b.verify(sig, b"hello") is False

    def test_verify_with_public_key(self):
        node = NodeIdentity.generate()
        data = b"verify me"
        sig = node.sign(data)
        pub = node.ed25519_public_bytes
        assert NodeIdentity.verify_with_public_key(pub, sig, data) is True
        assert NodeIdentity.verify_with_public_key(pub, sig, b"tampered") is False

    def test_different_identities_have_different_keys(self):
        a = NodeIdentity.generate()
        b = NodeIdentity.generate()
        assert a.ed25519_public_bytes != b.ed25519_public_bytes
        assert a.x25519_public_bytes != b.x25519_public_bytes

    def test_repr(self):
        node = NodeIdentity.generate()
        r = repr(node)
        assert "NodeIdentity" in r


# ---------------------------------------------------------------------------
# Onion routing tests
# ---------------------------------------------------------------------------

class TestOnionRouting:
    """Core onion routing: build, peel through 3 relays + 1 recipient."""

    def test_three_hop_peel_and_recover(self):
        """Onion layers peel correctly through 3 hops; recipient gets plaintext."""
        nodes, route = _make_route(4)  # R1, R2, R3, Recipient
        payload = b"secret message for the recipient"

        packet = build_onion(payload, route)
        assert len(packet) == FIXED_PACKET_SIZE

        # Relay 1
        next_hop, blob = peel_onion(packet, nodes[0].x25519_private_key)
        assert next_hop == nodes[1].x25519_public_bytes

        # Relay 2
        next_hop, blob = peel_onion(blob, nodes[1].x25519_private_key)
        assert next_hop == nodes[2].x25519_public_bytes

        # Relay 3
        next_hop, blob = peel_onion(blob, nodes[2].x25519_private_key)
        assert next_hop == nodes[3].x25519_public_bytes

        # Recipient
        next_hop, recovered = peel_onion(blob, nodes[3].x25519_private_key)
        assert next_hop is None
        assert recovered == payload

    def test_final_recipient_recovers_plaintext(self):
        """The final recipient recovers the exact original plaintext."""
        nodes, route = _make_route(3)
        payload = b"exact plaintext recovery test \x00\x01\x02"

        packet = build_onion(payload, route)

        # Peel through relays
        _, blob = peel_onion(packet, nodes[0].x25519_private_key)
        _, blob = peel_onion(blob, nodes[1].x25519_private_key)
        _, recovered = peel_onion(blob, nodes[2].x25519_private_key)

        assert recovered == payload

    def test_packet_size_constant_after_each_peel(self):
        """After each relay peel, the forwarded packet is FIXED_PACKET_SIZE."""
        nodes, route = _make_route(5)  # 4 relays + 1 recipient
        payload = b"size constancy test"

        packet = build_onion(payload, route)
        assert len(packet) == FIXED_PACKET_SIZE

        for i in range(4):  # relays 0..3
            next_hop, packet = peel_onion(packet, nodes[i].x25519_private_key)
            assert next_hop is not None, f"Unexpected final hop at relay {i}"
            assert len(packet) == FIXED_PACKET_SIZE, (
                f"Packet size {len(packet)} != {FIXED_PACKET_SIZE} after relay {i}"
            )

        # Final recipient — payload, not a fixed-size packet
        final_hop, recovered = peel_onion(packet, nodes[4].x25519_private_key)
        assert final_hop is None
        assert recovered == payload

    def test_intermediate_relay_cannot_read_payload(self):
        """
        An intermediate relay sees only its encrypted layer.  It cannot
        recover the plaintext even if it tries decrypting with its own key
        at every position or tries to interpret the forwarded blob.
        """
        nodes, route = _make_route(4)
        payload = b"top secret: relays must not see this"

        packet = build_onion(payload, route)

        # Relay 0 peels its layer
        _, blob_for_r1 = peel_onion(packet, nodes[0].x25519_private_key)

        # Relay 0 should NOT find the payload in:
        #   - the original packet
        #   - the forwarded blob
        assert payload not in packet
        assert payload not in blob_for_r1

        # Relay 1 peels its layer
        _, blob_for_r2 = peel_onion(blob_for_r1, nodes[1].x25519_private_key)
        assert payload not in blob_for_r2

        # Relay 0 cannot peel relay 1's blob
        with pytest.raises(ValueError, match="decryption failed"):
            peel_onion(blob_for_r1, nodes[0].x25519_private_key)

        # Relay 1 cannot peel relay 2's blob
        with pytest.raises(ValueError, match="decryption failed"):
            peel_onion(blob_for_r2, nodes[1].x25519_private_key)

    def test_wrong_key_fails(self):
        """Peeling with the wrong private key raises ValueError."""
        nodes, route = _make_route(3)
        packet = build_onion(b"payload", route)

        wrong_node = NodeIdentity.generate()
        with pytest.raises(ValueError):
            peel_onion(packet, wrong_node.x25519_private_key)

    def test_single_hop_direct(self):
        """A single-hop route (sender → recipient) works."""
        nodes, route = _make_route(1)
        payload = b"direct message"

        packet = build_onion(payload, route)
        assert len(packet) == FIXED_PACKET_SIZE

        next_hop, recovered = peel_onion(packet, nodes[0].x25519_private_key)
        assert next_hop is None
        assert recovered == payload

    def test_two_hop(self):
        """Two-hop route works (1 relay + recipient)."""
        nodes, route = _make_route(2)
        payload = b"two hop message"

        packet = build_onion(payload, route)
        next_hop, blob = peel_onion(packet, nodes[0].x25519_private_key)
        assert next_hop == nodes[1].x25519_public_bytes
        assert len(blob) == FIXED_PACKET_SIZE

        final, recovered = peel_onion(blob, nodes[1].x25519_private_key)
        assert final is None
        assert recovered == payload

    def test_max_payload_capacity(self):
        """A payload at exactly max capacity works; one byte over raises."""
        nodes, route = _make_route(4)
        cap = max_onion_payload(4)
        assert cap > 0

        # Exactly at capacity
        payload = os.urandom(cap)
        packet = build_onion(payload, route)

        # Peel all layers
        blob = packet
        for i in range(3):
            _, blob = peel_onion(blob, nodes[i].x25519_private_key)
        _, recovered = peel_onion(blob, nodes[3].x25519_private_key)
        assert recovered == payload

        # One byte over capacity
        with pytest.raises(ValueError, match="too large"):
            build_onion(os.urandom(cap + 1), route)

    def test_empty_payload(self):
        """Empty payload round-trips correctly."""
        nodes, route = _make_route(3)
        packet = build_onion(b"", route)
        blob = packet
        for i in range(2):
            _, blob = peel_onion(blob, nodes[i].x25519_private_key)
        _, recovered = peel_onion(blob, nodes[2].x25519_private_key)
        assert recovered == b""

    def test_empty_route_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            build_onion(b"x", [])

    def test_invalid_packet_size_raises(self):
        node = NodeIdentity.generate()
        with pytest.raises(ValueError, match="Expected"):
            peel_onion(b"too short", node.x25519_private_key)

    def test_packets_differ_each_build(self):
        """Ephemeral keys ensure that two builds of the same payload differ."""
        nodes, route = _make_route(3)
        p1 = build_onion(b"same", route)
        p2 = build_onion(b"same", route)
        assert p1 != p2  # different ephemeral keys each time

    def test_five_hops(self):
        """Five-hop route works correctly."""
        nodes, route = _make_route(5)
        payload = b"five hops of onion"

        packet = build_onion(payload, route)
        blob = packet
        for i in range(4):
            nh, blob = peel_onion(blob, nodes[i].x25519_private_key)
            assert nh == nodes[i + 1].x25519_public_bytes
            assert len(blob) == FIXED_PACKET_SIZE

        final, recovered = peel_onion(blob, nodes[4].x25519_private_key)
        assert final is None
        assert recovered == payload

    def test_binary_payload(self):
        """Binary (non-UTF-8) payload round-trips."""
        nodes, route = _make_route(3)
        payload = bytes(range(256)) * 10  # 2560 bytes of all byte values
        packet = build_onion(payload, route)
        blob = packet
        for i in range(2):
            _, blob = peel_onion(blob, nodes[i].x25519_private_key)
        _, recovered = peel_onion(blob, nodes[2].x25519_private_key)
        assert recovered == payload


# ---------------------------------------------------------------------------
# Symmetric channel encryption tests
# ---------------------------------------------------------------------------

class TestSymmetricEncryption:
    def test_round_trip(self):
        """Encrypt then decrypt recovers original plaintext."""
        key = generate_channel_key()
        plaintext = b"private channel message"
        blob = symmetric_encrypt(key, plaintext)
        recovered = symmetric_decrypt(key, blob)
        assert recovered == plaintext

    def test_round_trip_empty(self):
        key = generate_channel_key()
        blob = symmetric_encrypt(key, b"")
        assert symmetric_decrypt(key, blob) == b""

    def test_round_trip_large(self):
        key = generate_channel_key()
        plaintext = os.urandom(100_000)
        blob = symmetric_encrypt(key, plaintext)
        assert symmetric_decrypt(key, blob) == plaintext

    def test_wrong_key_fails(self):
        key1 = generate_channel_key()
        key2 = generate_channel_key()
        blob = symmetric_encrypt(key1, b"secret")
        with pytest.raises(Exception):
            symmetric_decrypt(key2, blob)

    def test_tampered_ciphertext_fails(self):
        key = generate_channel_key()
        blob = symmetric_encrypt(key, b"integrity check")
        # Flip a bit in the ciphertext portion
        corrupted = bytearray(blob)
        corrupted[-1] ^= 0xFF
        with pytest.raises(Exception):
            symmetric_decrypt(key, bytes(corrupted))

    def test_unreadable_without_channel_key(self):
        """Without the channel key, ciphertext is unreadable (cannot recover)."""
        key = generate_channel_key()
        plaintext = b"members only"
        blob = symmetric_encrypt(key, plaintext)

        # The plaintext should not appear in the ciphertext blob
        assert plaintext not in blob

        # A random key cannot decrypt it
        wrong_key = generate_channel_key()
        with pytest.raises(Exception):
            symmetric_decrypt(wrong_key, blob)

    def test_invalid_key_length(self):
        with pytest.raises(ValueError, match="32 bytes"):
            symmetric_encrypt(b"short", b"data")
        with pytest.raises(ValueError, match="32 bytes"):
            symmetric_decrypt(b"short", b"x" * 30)

    def test_blob_too_short(self):
        key = generate_channel_key()
        with pytest.raises(ValueError, match="too short"):
            symmetric_decrypt(key, b"x")

    def test_different_nonces(self):
        """Two encryptions of the same plaintext produce different blobs."""
        key = generate_channel_key()
        b1 = symmetric_encrypt(key, b"same")
        b2 = symmetric_encrypt(key, b"same")
        assert b1 != b2  # different random nonces


# ---------------------------------------------------------------------------
# Integration: onion + symmetric channel encryption
# ---------------------------------------------------------------------------

class TestOnionWithSymmetricChannel:
    def test_private_channel_message_through_onion(self):
        """
        Simulate sending a private-channel message through onion routing:
        the payload is first encrypted with the channel key, then wrapped
        in onion layers.  Only a node with both the channel key AND being
        the final recipient can read the message.
        """
        channel_key = generate_channel_key()
        nodes, route = _make_route(4)
        plaintext = b"private channel payload"

        # Sender encrypts for the channel, then wraps in onion
        encrypted_payload = symmetric_encrypt(channel_key, plaintext)
        packet = build_onion(encrypted_payload, route)

        # Relay through 3 hops
        blob = packet
        for i in range(3):
            nh, blob = peel_onion(blob, nodes[i].x25519_private_key)
            # Relay cannot read the channel-encrypted payload
            assert plaintext not in blob

        # Recipient peels final onion layer
        final, recovered_blob = peel_onion(blob, nodes[3].x25519_private_key)
        assert final is None

        # Without channel key, still unreadable
        assert plaintext not in recovered_blob

        # With channel key, recovers plaintext
        recovered = symmetric_decrypt(channel_key, recovered_blob)
        assert recovered == plaintext


# ---------------------------------------------------------------------------
# max_onion_payload sanity
# ---------------------------------------------------------------------------

class TestMaxOnionPayload:
    def test_decreases_with_more_hops(self):
        caps = [max_onion_payload(n) for n in range(1, 10)]
        for i in range(1, len(caps)):
            assert caps[i] < caps[i - 1]

    def test_single_hop_capacity(self):
        cap = max_onion_payload(1)
        # Should be FIXED_PACKET_SIZE - 62 - 5 = 4029
        assert cap == FIXED_PACKET_SIZE - 62 - 5

    def test_positive_for_reasonable_hops(self):
        # Up to ~40 hops should still have positive capacity
        for n in range(1, 40):
            assert max_onion_payload(n) > 0, f"No capacity at {n} hops"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
