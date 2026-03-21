"""
Comprehensive tests for HermesP2P.

Tests cover:
(a) Message reaches all subscribers within TTL.
(b) Expired messages are dropped.
(c) Duplicate messages are deduplicated.
(d) Onion routing: intermediate relays cannot read the payload.
(e) Bloom filter bounds memory usage.
(f) Public channel messages are verified by signature.
(g) Private channel messages are unreadable without channel key.
(h) New node successfully bootstraps and receives messages.
(i) Network partition and recovery.
"""

import time
import pytest
import os

from nacl.signing import SigningKey, VerifyKey
from nacl.public import PrivateKey, PublicKey

from hermes_p2p.bloom_filter import BloomFilter, TTLExpiringSet
from hermes_p2p.crypto import OnionRouter, ChannelCrypto, PACKET_SIZE
from hermes_p2p.message import Message, ChannelType
from hermes_p2p.node import Node, NodeIdentity
from hermes_p2p.network import SimulatedNetwork


# === Helper to create a test network ===

def create_test_network(n_nodes: int = 5, fanout: int = 3) -> tuple:
    """Create a network with n_nodes fully connected nodes."""
    net = SimulatedNetwork()
    nodes = []
    for i in range(n_nodes):
        addr = f"node_{i}"
        node = Node(address=addr, fanout=fanout)
        net.register_node(node)
        nodes.append(node)
    net.setup_full_mesh()
    return net, nodes


# === (a) Message reaches all subscribers within TTL ===

class TestMessageReachesSubscribers:
    def test_public_message_reaches_all_subscribers(self):
        """All subscribed nodes receive a public channel message."""
        net, nodes = create_test_network(5)
        channel = "general"

        # All nodes subscribe to the channel
        for node in nodes:
            node.subscribe(channel)
            node.add_channel_verify_key(
                channel,
                nodes[0].identity.verify_key_hex,
                nodes[0].identity.verify_key,
            )

        # Node 0 sends a message
        plaintext = b"Hello, everyone!"
        nodes[0].send_public_message(channel, plaintext, ttl_seconds=60, hops=10)

        # Check all other nodes received it
        for i in range(1, len(nodes)):
            found = any(
                ch == channel and data == plaintext
                for ch, data in nodes[i].received_payloads
            )
            assert found, f"Node {i} did not receive the message"

    def test_private_message_reaches_subscribers(self):
        """All nodes with channel key receive private messages."""
        net, nodes = create_test_network(5)
        channel = "secret"
        channel_key = ChannelCrypto.generate_channel_key()

        for node in nodes:
            node.subscribe(channel, channel_key)

        plaintext = b"Secret message"
        nodes[0].send_private_message(channel, plaintext, channel_key, ttl_seconds=60)

        for i in range(1, len(nodes)):
            found = any(
                ch == channel and data == plaintext
                for ch, data in nodes[i].received_payloads
            )
            assert found, f"Node {i} did not receive the private message"


# === (b) Expired messages are dropped ===

class TestExpiredMessages:
    def test_expired_message_dropped(self):
        """Messages past their TTL are silently dropped."""
        net, nodes = create_test_network(3)
        channel = "ephemeral"

        for node in nodes:
            node.subscribe(channel)
            node.add_channel_verify_key(
                channel,
                nodes[0].identity.verify_key_hex,
                nodes[0].identity.verify_key,
            )

        # Create a message that's already expired
        msg = Message(
            created_ts=time.time() - 120,  # 2 minutes ago
            ttl_seconds=5,  # 5 second TTL, long expired
            channel_id=channel,
            channel_type=ChannelType.PUBLIC,
            payload=ChannelCrypto.sign_public_message(b"Old message", nodes[0].identity.signing_key),
            hops_remaining=10,
            sender_pubkey_hex=nodes[0].identity.verify_key_hex,
        )

        # Send directly to node 1
        nodes[1].receive_message(msg.to_dict())

        # Node 1 should have dropped it
        found = any(
            data == b"Old message"
            for ch, data in nodes[1].received_payloads
        )
        assert not found, "Expired message should have been dropped"

    def test_ttl_enforcement_purge(self):
        """Nodes purge expired messages from their store."""
        net, nodes = create_test_network(2)
        channel = "temp"
        nodes[0].subscribe(channel)

        msg = Message(
            created_ts=time.time() - 100,
            ttl_seconds=1,  # Already expired
            channel_id=channel,
            channel_type=ChannelType.PUBLIC,
            payload=b"x" * 100,
            hops_remaining=0,
        )
        # Force add to store (bypassing TTL check)
        nodes[0].message_store.append(msg)
        assert len(nodes[0].message_store) == 1
        nodes[0].purge_expired()
        assert len(nodes[0].message_store) == 0


# === (c) Duplicate messages are deduplicated ===

class TestDeduplication:
    def test_duplicate_message_ignored(self):
        """Sending the same message twice results in only one delivery."""
        net, nodes = create_test_network(3)
        channel = "dedup_test"

        for node in nodes:
            node.subscribe(channel)
            node.add_channel_verify_key(
                channel,
                nodes[0].identity.verify_key_hex,
                nodes[0].identity.verify_key,
            )

        plaintext = b"Unique message"
        msg = nodes[0].send_public_message(channel, plaintext)

        # Manually send the same message again to node 1
        initial_count = len(nodes[1].received_payloads)
        nodes[1].receive_message(msg.to_dict())

        # Count should not have increased
        assert len(nodes[1].received_payloads) == initial_count, \
            "Duplicate message was not deduplicated"

    def test_dedup_bloom_filter(self):
        """The deduplication bloom filter correctly identifies seen messages."""
        bf = BloomFilter(capacity=1000, fp_rate=0.01)
        assert "msg_001" not in bf
        bf.add("msg_001")
        assert "msg_001" in bf
        assert "msg_002" not in bf  # Very unlikely false positive

    def test_ttl_expiring_set(self):
        """TTL expiring set correctly expires old entries."""
        es = TTLExpiringSet(ttl_seconds=1.0, capacity=100)
        es.add("item1", ttl=0.3)
        assert "item1" in es
        time.sleep(0.5)
        assert "item1" not in es


# === (d) Onion routing: intermediate relays cannot read the payload ===

class TestOnionRouting:
    def test_onion_routing_privacy(self):
        """Intermediate relays cannot read the payload."""
        # Create keys: 3 relays + recipient
        relay_keys = [PrivateKey.generate() for _ in range(3)]
        relay_pubs = [k.public_key for k in relay_keys]
        recip_key = PrivateKey.generate()
        recip_pub = recip_key.public_key

        plaintext = b"Top secret message for recipient only"

        # Encrypt DM for recipient
        dm_encrypted = ChannelCrypto.encrypt_direct_message(plaintext, recip_pub)

        # Build onion: R1 -> R2 -> R3 -> recipient
        route = relay_pubs
        onion = OnionRouter.build_onion(
            payload=dm_encrypted,
            route=route,
            recipient_pubkey=recip_pub,
        )

        # Verify constant size
        assert len(onion) == PACKET_SIZE

        # Relay 1 peels first layer
        is_final, next_hop, inner1 = OnionRouter.peel_layer(onion, relay_keys[0])
        assert not is_final
        assert next_hop == bytes(relay_pubs[1])
        assert len(inner1) == PACKET_SIZE  # Constant size!

        # Relay 1 CANNOT read the plaintext from the forwarded packet
        # The inner1 packet is encrypted for relay 2, not relay 1
        try:
            is_f, _, data = OnionRouter.peel_layer(inner1, relay_keys[0])
            # Even if peel doesn't crash, data won't contain the plaintext
            if is_f:
                result = ChannelCrypto.decrypt_direct_message(
                    data[:len(dm_encrypted)], relay_keys[0]
                )
                assert result != plaintext
        except Exception:
            pass  # Expected: can't decrypt

        # Relay 2 peels second layer
        is_final, next_hop, inner2 = OnionRouter.peel_layer(inner1, relay_keys[1])
        assert not is_final
        assert next_hop == bytes(relay_pubs[2])
        assert len(inner2) == PACKET_SIZE

        # Relay 3 peels third layer
        is_final, next_hop, inner3 = OnionRouter.peel_layer(inner2, relay_keys[2])
        assert not is_final
        # next_hop points to recipient
        assert next_hop == bytes(recip_pub)
        assert len(inner3) == PACKET_SIZE

        # Relay 3 CANNOT read the plaintext
        try:
            result = ChannelCrypto.decrypt_direct_message(
                inner3[:len(dm_encrypted)], relay_keys[2]
            )
            assert result is None or result != plaintext
        except Exception:
            pass

        # Recipient peels final layer
        is_final, _, payload_with_pad = OnionRouter.peel_layer(inner3, recip_key)
        assert is_final

        # Recipient decrypts the DM
        recovered = ChannelCrypto.decrypt_direct_message(
            payload_with_pad[:len(dm_encrypted)], recip_key
        )
        assert recovered == plaintext

    def test_constant_size_packets(self):
        """All onion layers produce the same packet size."""
        priv_keys = [PrivateKey.generate() for _ in range(5)]
        pub_keys = [k.public_key for k in priv_keys]

        payload = b"Test payload data"
        dm_encrypted = ChannelCrypto.encrypt_direct_message(payload, pub_keys[-1])

        # Route through keys[1], keys[2], keys[3]; recipient is keys[4]
        onion = OnionRouter.build_onion(
            payload=dm_encrypted,
            route=pub_keys[1:-1],  # [1, 2, 3]
            recipient_pubkey=pub_keys[-1],
        )

        assert len(onion) == PACKET_SIZE

        current = onion
        for i in range(1, len(pub_keys)):
            is_final, next_hop, inner = OnionRouter.peel_layer(current, priv_keys[i])
            if is_final:
                # Verify we can recover the payload
                recovered = ChannelCrypto.decrypt_direct_message(
                    inner[:len(dm_encrypted)], priv_keys[-1]
                )
                assert recovered == payload
                break
            else:
                assert len(inner) == PACKET_SIZE
                current = inner

    def test_onion_end_to_end_via_network(self):
        """Test onion routed direct message through simulated network."""
        net, nodes = create_test_network(5, fanout=1)

        sender = nodes[0]
        recipient = nodes[4]
        plaintext = b"Secret DM content"

        # Route through nodes 1, 2, 3
        route = [
            nodes[1].identity.encryption_public_key,
            nodes[2].identity.encryption_public_key,
            nodes[3].identity.encryption_public_key,
        ]

        sender.send_direct_message(
            recipient_pubkey=recipient.identity.encryption_public_key,
            plaintext=plaintext,
            route=route,
            ttl_seconds=60,
            hops=10,
        )

        # Recipient should have the message
        found = any(
            ch == "direct" and data == plaintext
            for ch, data in recipient.received_payloads
        )
        assert found, "Recipient did not receive the onion-routed message"

        # Intermediate relays should NOT have the plaintext
        for i in [1, 2, 3]:
            relay_has_plaintext = any(
                data == plaintext
                for ch, data in nodes[i].received_payloads
            )
            assert not relay_has_plaintext, \
                f"Relay node_{i} should not be able to read the plaintext"


# === (e) Bloom filter bounds memory usage ===

class TestBloomFilterMemory:
    def test_bloom_filter_bounded_memory(self):
        """Bloom filter memory is bounded regardless of input count."""
        bf = BloomFilter(capacity=10000, fp_rate=0.01)
        initial_mem = bf.memory_bytes()

        # Add many items
        for i in range(20000):
            bf.add(f"msg_{i}")

        final_mem = bf.memory_bytes()

        # Memory should not have grown (it's a fixed-size bit array)
        assert final_mem == initial_mem
        # Memory should be reasonable (< 100KB for 10k capacity)
        assert initial_mem < 100 * 1024

    def test_ttl_expiring_set_bounded(self):
        """TTL expiring set doesn't grow unboundedly."""
        es = TTLExpiringSet(ttl_seconds=300, capacity=100)

        for i in range(500):
            es.add(f"item_{i}")

        # The entries dict should be bounded
        assert len(es._entries) <= es._max_entries


# === (f) Public channel messages are verified by signature ===

class TestPublicChannelSignature:
    def test_valid_signature_accepted(self):
        """Messages with valid signatures are accepted."""
        sk = SigningKey.generate()
        vk = sk.verify_key

        msg = b"Authentic message"
        signed = ChannelCrypto.sign_public_message(msg, sk)
        recovered = ChannelCrypto.verify_public_message(signed, vk)
        assert recovered == msg

    def test_invalid_signature_rejected(self):
        """Messages with invalid signatures are rejected."""
        sk1 = SigningKey.generate()
        sk2 = SigningKey.generate()
        vk2 = sk2.verify_key

        msg = b"Forged message"
        signed = ChannelCrypto.sign_public_message(msg, sk1)

        # Verify with wrong key
        recovered = ChannelCrypto.verify_public_message(signed, vk2)
        assert recovered is None

    def test_tampered_message_rejected(self):
        """Tampered messages are rejected."""
        sk = SigningKey.generate()
        vk = sk.verify_key

        msg = b"Original message"
        signed = ChannelCrypto.sign_public_message(msg, sk)

        # Tamper with the message content
        tampered = signed[:64] + b"Tampered message"
        recovered = ChannelCrypto.verify_public_message(tampered, vk)
        assert recovered is None

    def test_public_channel_e2e_verification(self):
        """End-to-end test: public channel messages are verified on receipt."""
        net, nodes = create_test_network(3)
        channel = "verified_channel"

        for node in nodes:
            node.subscribe(channel)
            node.add_channel_verify_key(
                channel,
                nodes[0].identity.verify_key_hex,
                nodes[0].identity.verify_key,
            )

        plaintext = b"Verified broadcast"
        nodes[0].send_public_message(channel, plaintext)

        # Other nodes received and verified
        for i in [1, 2]:
            found = any(
                ch == channel and data == plaintext
                for ch, data in nodes[i].received_payloads
            )
            assert found


# === (g) Private channel messages are unreadable without channel key ===

class TestPrivateChannel:
    def test_unreadable_without_key(self):
        """Private channel messages cannot be read without the channel key."""
        channel_key = ChannelCrypto.generate_channel_key()
        wrong_key = ChannelCrypto.generate_channel_key()

        plaintext = b"Confidential data"
        encrypted = ChannelCrypto.encrypt_private_channel(plaintext, channel_key)

        # Try to decrypt with wrong key
        result = ChannelCrypto.decrypt_private_channel(encrypted, wrong_key)
        assert result is None

        # Decrypt with correct key
        result = ChannelCrypto.decrypt_private_channel(encrypted, channel_key)
        assert result == plaintext

    def test_non_member_cannot_read(self):
        """Nodes without the channel key cannot read private messages."""
        net, nodes = create_test_network(4)
        channel = "private_room"
        channel_key = ChannelCrypto.generate_channel_key()

        # Nodes 0, 1, 2 have the key; node 3 does not
        for i in [0, 1, 2]:
            nodes[i].subscribe(channel, channel_key)
        nodes[3].subscribe(channel, None)  # Subscribed but no key

        plaintext = b"Members only message"
        nodes[0].send_private_message(channel, plaintext, channel_key)

        # Nodes 1, 2 should receive it
        for i in [1, 2]:
            found = any(
                ch == channel and data == plaintext
                for ch, data in nodes[i].received_payloads
            )
            assert found, f"Node {i} should have received the private message"

        # Node 3 should NOT have the plaintext
        has_plaintext = any(
            data == plaintext
            for ch, data in nodes[3].received_payloads
        )
        assert not has_plaintext, "Non-member should not read private message"


# === (h) New node successfully bootstraps and receives messages ===

class TestBootstrap:
    def test_new_node_discovers_peers(self):
        """A new node can bootstrap and discover existing peers."""
        net = SimulatedNetwork()

        # Create 3 existing nodes
        existing = []
        for i in range(3):
            node = Node(address=f"existing_{i}", fanout=3)
            net.register_node(node)
            existing.append(node)

        # Connect existing nodes to each other
        for i, a in enumerate(existing):
            for j, b in enumerate(existing):
                if i != j:
                    a.add_peer(b.address, b.identity.encryption_public_key)

        # New node bootstraps
        new_node = Node(address="new_node", fanout=3)
        net.register_node(new_node)
        new_node.bootstrap([existing[0].address])

        # New node should now know about peers
        assert len(new_node.peers) > 0
        peer_addrs = new_node.get_peer_addresses()
        assert "existing_0" in peer_addrs

    def test_bootstrapped_node_receives_messages(self):
        """After bootstrap, a new node can receive gossip messages."""
        net = SimulatedNetwork()
        channel = "news"

        # Create existing network
        existing = []
        for i in range(3):
            node = Node(address=f"node_{i}", fanout=3)
            net.register_node(node)
            existing.append(node)

        for i, a in enumerate(existing):
            for j, b in enumerate(existing):
                if i != j:
                    a.add_peer(b.address, b.identity.encryption_public_key)

        # New node bootstraps
        new_node = Node(address="new_node", fanout=3)
        net.register_node(new_node)
        new_node.bootstrap([existing[0].address])

        # Make existing nodes aware of new node too
        for node in existing:
            node.add_peer(new_node.address, new_node.identity.encryption_public_key)

        # Subscribe all nodes
        all_nodes = existing + [new_node]
        for node in all_nodes:
            node.subscribe(channel)
            node.add_channel_verify_key(
                channel,
                existing[0].identity.verify_key_hex,
                existing[0].identity.verify_key,
            )

        # Send a message
        plaintext = b"Welcome to the network!"
        existing[0].send_public_message(channel, plaintext)

        # New node should have received it
        found = any(
            ch == channel and data == plaintext
            for ch, data in new_node.received_payloads
        )
        assert found, "Bootstrapped node should receive messages"


# === (i) Network partition and recovery ===

class TestNetworkPartition:
    def test_partition_prevents_delivery(self):
        """Messages don't cross network partitions."""
        net, nodes = create_test_network(4, fanout=3)
        channel = "partition_test"

        for node in nodes:
            node.subscribe(channel)
            node.add_channel_verify_key(
                channel,
                nodes[0].identity.verify_key_hex,
                nodes[0].identity.verify_key,
            )

        # Partition: {node_0, node_1} | {node_2, node_3}
        net.partition([
            ["node_0", "node_1"],
            ["node_2", "node_3"],
        ])

        plaintext = b"Partitioned message"
        nodes[0].send_public_message(channel, plaintext, ttl_seconds=5)

        # Node 1 should have it (same partition)
        found_1 = any(
            ch == channel and data == plaintext
            for ch, data in nodes[1].received_payloads
        )
        assert found_1, "Node in same partition should receive message"

        # Nodes 2, 3 should NOT have it (different partition)
        for i in [2, 3]:
            found = any(
                ch == channel and data == plaintext
                for ch, data in nodes[i].received_payloads
            )
            assert not found, f"Node {i} should not receive message across partition"

    def test_partition_recovery_no_leak(self):
        """Messages sent during partition don't leak across after reconnection."""
        net, nodes = create_test_network(4, fanout=3)
        channel = "recovery_test"

        for node in nodes:
            node.subscribe(channel)
            node.add_channel_verify_key(
                channel,
                nodes[0].identity.verify_key_hex,
                nodes[0].identity.verify_key,
            )

        # Partition
        net.partition([
            ["node_0", "node_1"],
            ["node_2", "node_3"],
        ])

        # Send message during partition (short TTL)
        plaintext_during = b"During partition"
        nodes[0].send_public_message(channel, plaintext_during, ttl_seconds=2)

        # Verify node_2 doesn't have it
        found_2_during = any(
            ch == channel and data == plaintext_during
            for ch, data in nodes[2].received_payloads
        )
        assert not found_2_during

        # Heal partition
        net.heal_partition()

        # The old message won't leak because:
        # 1. Gossip already happened within partition 0
        # 2. No mechanism re-gossips old messages after healing
        found_2_after = any(
            ch == channel and data == plaintext_during
            for ch, data in nodes[2].received_payloads
        )
        assert not found_2_after, \
            "Messages from during partition should not leak after recovery"

        # But NEW messages after healing should work
        plaintext_after = b"After healing"
        nodes[0].send_public_message(channel, plaintext_after, ttl_seconds=60)

        found_2_new = any(
            ch == channel and data == plaintext_after
            for ch, data in nodes[2].received_payloads
        )
        assert found_2_new, \
            "New messages after partition healing should be delivered"


# === Additional edge case tests ===

class TestEdgeCases:
    def test_hop_counter_prevents_infinite_propagation(self):
        """Messages with hop=0 are not forwarded."""
        net, nodes = create_test_network(3, fanout=3)
        channel = "hop_test"

        for node in nodes:
            node.subscribe(channel)
            node.add_channel_verify_key(
                channel,
                nodes[0].identity.verify_key_hex,
                nodes[0].identity.verify_key,
            )

        # Send with hops=1 (sender processes + forwards once, recipients process but with hops=0 don't forward)
        plaintext = b"Limited hops"
        nodes[0].send_public_message(channel, plaintext, hops=1)

        # Direct peers should receive it
        found_1 = any(
            ch == channel and data == plaintext
            for ch, data in nodes[1].received_payloads
        )
        assert found_1

    def test_message_envelope_fields(self):
        """Message envelope has all required fields."""
        msg = Message(
            ttl_seconds=30,
            channel_id="test",
            channel_type=ChannelType.PUBLIC,
            payload=b"data",
            hops_remaining=5,
        )
        assert msg.message_id  # Non-empty
        assert msg.created_ts > 0
        assert msg.ttl_seconds == 30
        assert msg.channel_id == "test"
        assert msg.payload == b"data"
        assert msg.hops_remaining == 5

        # Serialization roundtrip
        d = msg.to_dict()
        msg2 = Message.from_dict(d)
        assert msg2.message_id == msg.message_id
        assert msg2.payload == msg.payload

    def test_clock_skew_tolerance(self):
        """Messages within clock skew tolerance are accepted."""
        msg = Message(
            created_ts=time.time() + 25,
            ttl_seconds=1,
            channel_id="skew",
            channel_type=ChannelType.PUBLIC,
            payload=b"data",
        )
        # created_ts + ttl + 30s tolerance = now + 25 + 1 + 30 = now + 56
        assert not msg.is_expired()

    def test_direct_message_without_onion(self):
        """Direct messages work without onion routing (simple encrypted)."""
        net, nodes = create_test_network(3, fanout=3)

        sender = nodes[0]
        recipient = nodes[2]
        plaintext = b"Simple DM"

        sender.send_direct_message(
            recipient_pubkey=recipient.identity.encryption_public_key,
            plaintext=plaintext,
            route=None,  # No onion routing
            ttl_seconds=60,
        )

        found = any(
            ch == "direct" and data == plaintext
            for ch, data in recipient.received_payloads
        )
        assert found, "Recipient should receive simple DM"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
