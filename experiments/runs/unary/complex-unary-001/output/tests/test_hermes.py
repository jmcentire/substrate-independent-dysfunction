"""
Comprehensive test suite for HermesP2P.

Tests:
(a) Message reaches all subscribers within TTL.
(b) Expired messages are dropped.
(c) Duplicate messages are deduplicated.
(d) Onion routing: intermediate relays cannot read the payload.
(e) Bloom filter bounds memory usage.
(f) Public channel messages are verified by signature.
(g) Private channel messages are unreadable without channel key.
(h) New node successfully bootstraps and receives messages.
(i) Network partition and recovery: messages sent during partition don't leak.
"""

import os
import time
import uuid
import pytest

from hermes_p2p.bloom_filter import BloomFilter, TTLBloomFilter
from hermes_p2p.crypto import (
    KeyManager,
    OnionRouter,
    OnionPacket,
    PACKET_SIZE,
    symmetric_encrypt,
    symmetric_decrypt,
)
from hermes_p2p.message import (
    MessageEnvelope,
    ChannelType,
    create_public_message,
    create_private_message,
    create_direct_message,
    verify_public_message,
    decrypt_private_message,
)
from hermes_p2p.node import Node, PeerInfo
from hermes_p2p.network import SimulatedNetwork


# ═══════════════════════════════════════════════════════════════════
# Test (a): Message reaches all subscribers within TTL
# ═══════════════════════════════════════════════════════════════════

class TestMessageReachesSubscribers:
    """Messages propagate via gossip and reach all subscribed nodes."""

    def test_public_message_reaches_all_subscribers(self):
        """A public channel message should reach every subscribed node."""
        net = SimulatedNetwork()
        nodes = [net.create_node(f"node_{i}", fanout=3) for i in range(6)]
        net.connect_all()

        channel_id = "general"
        for node in nodes:
            node.subscribe(channel_id, ChannelType.PUBLIC)

        sender = nodes[0]
        env = create_public_message(
            channel_id=channel_id,
            plaintext="Hello, world!",
            key_manager=sender.key_manager,
            ttl_seconds=60.0,
            hop_count=10,
        )
        sender.send_to_channel(env)

        # All nodes should have received the message
        for node in nodes:
            texts = [t for t, _ in node.received_messages]
            assert "Hello, world!" in texts, (
                f"Node {node.node_id} did not receive the message"
            )

    def test_message_reaches_distant_nodes_in_chain(self):
        """In a chain topology, message should propagate through all hops."""
        net = SimulatedNetwork()
        # Chain: node_0 -> node_1 -> node_2 -> node_3 -> node_4
        nodes = [net.create_node(f"node_{i}", fanout=3) for i in range(5)]
        for i in range(len(nodes) - 1):
            nodes[i].add_peer(nodes[i + 1].get_peer_info())
            nodes[i + 1].add_peer(nodes[i].get_peer_info())

        channel_id = "chain"
        for node in nodes:
            node.subscribe(channel_id, ChannelType.PUBLIC)

        env = create_public_message(
            channel_id=channel_id,
            plaintext="Chain message",
            key_manager=nodes[0].key_manager,
            ttl_seconds=60.0,
            hop_count=10,
        )
        nodes[0].send_to_channel(env)

        for node in nodes:
            texts = [t for t, _ in node.received_messages]
            assert "Chain message" in texts, (
                f"Node {node.node_id} did not receive chain message"
            )

    def test_non_subscriber_does_not_receive(self):
        """A node not subscribed to the channel should not deliver the message."""
        net = SimulatedNetwork()
        nodes = [net.create_node(f"node_{i}") for i in range(3)]
        net.connect_all()

        nodes[0].subscribe("ch1", ChannelType.PUBLIC)
        nodes[1].subscribe("ch1", ChannelType.PUBLIC)
        # nodes[2] does NOT subscribe

        env = create_public_message(
            channel_id="ch1",
            plaintext="Subscribers only",
            key_manager=nodes[0].key_manager,
            hop_count=5,
        )
        nodes[0].send_to_channel(env)

        assert len(nodes[2].received_messages) == 0


# ═══════════════════════════════════════════════════════════════════
# Test (b): Expired messages are dropped
# ═══════════════════════════════════════════════════════════════════

class TestTTLEnforcement:
    """Nodes discard messages past their TTL."""

    def test_expired_message_is_dropped(self):
        """A message with created_ts in the past beyond TTL should be dropped."""
        net = SimulatedNetwork()
        node_a = net.create_node("A")
        node_b = net.create_node("B")
        net.connect_all()

        node_b.subscribe("ch", ChannelType.PUBLIC)

        km = KeyManager()
        env = create_public_message(
            channel_id="ch",
            plaintext="Old message",
            key_manager=km,
            ttl_seconds=5.0,
            hop_count=5,
        )
        # Backdate the message so it's expired (beyond TTL + 30s tolerance)
        env.created_ts = time.time() - 100  # 100 seconds ago, TTL is 5

        # Send raw to node_b
        raw = env.serialize()
        node_b.receive_raw(raw, from_node_id="A")

        assert len(node_b.received_messages) == 0

    def test_clock_skew_tolerance(self):
        """Message within 30s clock skew tolerance should still be accepted."""
        net = SimulatedNetwork()
        node = net.create_node("X")
        node.subscribe("ch", ChannelType.PUBLIC)

        km = KeyManager()
        env = create_public_message(
            channel_id="ch",
            plaintext="Slightly old",
            key_manager=km,
            ttl_seconds=10.0,
            hop_count=5,
        )
        # Set created_ts so: now - created_ts = 35, ttl=10
        # Expiry = created_ts + ttl + tolerance = created_ts + 10 + 30 = created_ts + 40
        # Since now = created_ts + 35, and 35 < 40, message is valid
        env.created_ts = time.time() - 35

        raw = env.serialize()
        node.receive_raw(raw)

        texts = [t for t, _ in node.received_messages]
        assert "Slightly old" in texts

    def test_beyond_clock_skew_tolerance(self):
        """Message beyond TTL + 30s tolerance should be dropped."""
        net = SimulatedNetwork()
        node = net.create_node("Y")
        node.subscribe("ch", ChannelType.PUBLIC)

        km = KeyManager()
        env = create_public_message(
            channel_id="ch",
            plaintext="Very old",
            key_manager=km,
            ttl_seconds=10.0,
            hop_count=5,
        )
        # created_ts + ttl + 30 < now => dropped
        # now - created_ts = 50, ttl=10, tolerance=30 => 50 > 40 => dropped
        env.created_ts = time.time() - 50

        raw = env.serialize()
        node.receive_raw(raw)

        assert len(node.received_messages) == 0


# ═══════════════════════════════════════════════════════════════════
# Test (c): Duplicate messages are deduplicated
# ═══════════════════════════════════════════════════════════════════

class TestDeduplication:
    """Nodes track seen message_ids to prevent infinite circulation."""

    def test_duplicate_message_received_only_once(self):
        """Sending the same message multiple times should only deliver once."""
        net = SimulatedNetwork()
        node = net.create_node("dedup")
        node.subscribe("ch", ChannelType.PUBLIC)

        km = KeyManager()
        env = create_public_message(
            channel_id="ch",
            plaintext="Unique message",
            key_manager=km,
            hop_count=5,
        )

        raw = env.serialize()
        # Send the same message 5 times
        for _ in range(5):
            node.receive_raw(raw)

        assert len(node.received_messages) == 1

    def test_dedup_in_gossip_network(self):
        """In a fully connected network, gossip shouldn't cause duplicates."""
        net = SimulatedNetwork()
        nodes = [net.create_node(f"n{i}", fanout=5) for i in range(5)]
        net.connect_all()

        for n in nodes:
            n.subscribe("gossip_ch", ChannelType.PUBLIC)

        env = create_public_message(
            channel_id="gossip_ch",
            plaintext="Gossip test",
            key_manager=nodes[0].key_manager,
            hop_count=10,
        )
        nodes[0].send_to_channel(env)

        for n in nodes:
            count = sum(1 for t, _ in n.received_messages if t == "Gossip test")
            assert count == 1, (
                f"Node {n.node_id} received message {count} times"
            )

    def test_different_message_ids_not_deduped(self):
        """Messages with different IDs should both be delivered."""
        net = SimulatedNetwork()
        node = net.create_node("multi")
        node.subscribe("ch", ChannelType.PUBLIC)

        km = KeyManager()
        env1 = create_public_message("ch", "msg1", km)
        env2 = create_public_message("ch", "msg2", km)

        node.receive_raw(env1.serialize())
        node.receive_raw(env2.serialize())

        texts = [t for t, _ in node.received_messages]
        assert "msg1" in texts
        assert "msg2" in texts


# ═══════════════════════════════════════════════════════════════════
# Test (d): Onion routing — relays cannot read the payload
# ═══════════════════════════════════════════════════════════════════

class TestOnionRouting:
    """Intermediate relays cannot read the payload; only the recipient can."""

    def test_basic_onion_routing(self):
        """Build and peel a 3-hop onion; only recipient gets payload."""
        r1_keys = KeyManager()
        r2_keys = KeyManager()
        r3_keys = KeyManager()
        recipient_keys = KeyManager()

        payload = b"Secret message for recipient"

        route = [
            ("relay1", r1_keys.encryption_public_key_bytes()),
            ("relay2", r2_keys.encryption_public_key_bytes()),
            ("relay3", r3_keys.encryption_public_key_bytes()),
        ]

        onion = OnionRouter.build_onion(
            payload=payload,
            route=route,
            recipient_node_id="recipient",
            recipient_encryption_pubkey=recipient_keys.encryption_public_key_bytes(),
        )

        # Onion packet must be constant size
        assert len(onion.to_bytes()) == PACKET_SIZE

        # Relay 1 peels → sees next_hop = relay2
        next_hop, data, is_final = OnionRouter.peel_layer(
            onion.to_bytes(), r1_keys.encryption_private_key
        )
        assert not is_final
        assert next_hop == "relay2"
        assert len(data) == PACKET_SIZE  # Constant size after peeling!
        # Relay 1 cannot read the payload
        assert payload not in data

        # Relay 2 peels → sees next_hop = relay3
        next_hop2, data2, is_final2 = OnionRouter.peel_layer(
            data, r2_keys.encryption_private_key
        )
        assert not is_final2
        assert next_hop2 == "relay3"
        assert len(data2) == PACKET_SIZE
        assert payload not in data2

        # Relay 3 peels → sees next_hop = recipient
        next_hop3, data3, is_final3 = OnionRouter.peel_layer(
            data2, r3_keys.encryption_private_key
        )
        assert not is_final3
        assert next_hop3 == "recipient"
        assert len(data3) == PACKET_SIZE
        assert payload not in data3

        # Recipient peels final layer
        _, final_data, is_final_r = OnionRouter.peel_layer(
            data3, recipient_keys.encryption_private_key
        )
        assert is_final_r
        assert final_data == payload

    def test_constant_size_packets(self):
        """Packet size should be constant regardless of route position."""
        keys = [KeyManager() for _ in range(4)]
        recipient = KeyManager()

        route = [
            (f"relay{i}", keys[i].encryption_public_key_bytes())
            for i in range(3)
        ]

        onion = OnionRouter.build_onion(
            payload=b"Test payload",
            route=route,
            recipient_node_id="recipient",
            recipient_encryption_pubkey=recipient.encryption_public_key_bytes(),
        )

        sizes = [len(onion.to_bytes())]

        # Peel each layer and check size
        current = onion.to_bytes()
        for i in range(3):
            _, current, _ = OnionRouter.peel_layer(
                current, keys[i].encryption_private_key
            )
            sizes.append(len(current))

        # All sizes should be PACKET_SIZE
        assert all(s == PACKET_SIZE for s in sizes), f"Sizes varied: {sizes}"

    def test_relay_cannot_see_sender_or_recipient(self):
        """
        A relay in the middle of a route cannot determine both the sender
        and the final recipient. The middle relay (relay2) only sees relay1
        as the sender of the packet to it, and relay3 as the next hop.
        It never sees "sender" or "recipient" identities.
        """
        net = SimulatedNetwork()
        sender = net.create_node("sender")
        r1 = net.create_node("relay1")
        r2 = net.create_node("relay2")
        r3 = net.create_node("relay3")
        recipient = net.create_node("recipient")
        net.connect_all()

        # Track what relay2 sees at the network level
        relay2_from_ids = []
        original_receive = r2.receive_raw

        def spy_receive(raw, from_node_id=None):
            relay2_from_ids.append(from_node_id)
            original_receive(raw, from_node_id)

        r2.receive_raw = spy_receive

        dm_received = []
        recipient.on_direct_message(lambda nid, text, env: dm_received.append(text))

        sender.send_direct(
            plaintext="Top secret",
            recipient_node_id="recipient",
            route_node_ids=["relay1", "relay2", "relay3"],
        )

        # Recipient should get the message
        assert len(dm_received) == 1
        assert dm_received[0] == "Top secret"

        # relay2 should have seen traffic from relay1 (not sender, not recipient)
        assert len(relay2_from_ids) > 0
        for fid in relay2_from_ids:
            assert fid != "sender", "Relay2 should not see sender as origin"
            assert fid != "recipient", "Relay2 should not see recipient as origin"

    def test_direct_message_end_to_end(self):
        """Full end-to-end direct message through simulated network."""
        net = SimulatedNetwork()
        sender = net.create_node("alice")
        relay1 = net.create_node("r1")
        relay2 = net.create_node("r2")
        recipient = net.create_node("bob")
        net.connect_all()

        received = []
        recipient.on_direct_message(lambda nid, text, env: received.append(text))

        sender.send_direct(
            plaintext="Hello Bob from Alice!",
            recipient_node_id="bob",
            route_node_ids=["r1", "r2"],
        )

        assert len(received) == 1
        assert received[0] == "Hello Bob from Alice!"

    def test_no_relays_direct(self):
        """Direct message with no relays (sender to recipient directly)."""
        net = SimulatedNetwork()
        sender = net.create_node("s")
        recipient = net.create_node("r")
        net.connect_all()

        received = []
        recipient.on_direct_message(lambda nid, text, env: received.append(text))

        sender.send_direct(
            plaintext="Direct no relay",
            recipient_node_id="r",
            route_node_ids=[],
        )

        assert len(received) == 1
        assert received[0] == "Direct no relay"

    def test_wrong_relay_cannot_decrypt(self):
        """A node not on the route cannot decrypt any layer."""
        r1_keys = KeyManager()
        recipient_keys = KeyManager()
        intruder_keys = KeyManager()

        onion = OnionRouter.build_onion(
            payload=b"Secret",
            route=[("relay1", r1_keys.encryption_public_key_bytes())],
            recipient_node_id="recipient",
            recipient_encryption_pubkey=recipient_keys.encryption_public_key_bytes(),
        )

        # Intruder tries to peel — should fail
        with pytest.raises(Exception):
            OnionRouter.peel_layer(
                onion.to_bytes(),
                intruder_keys.encryption_private_key,
            )


# ═══════════════════════════════════════════════════════════════════
# Test (e): Bloom filter bounds memory usage
# ═══════════════════════════════════════════════════════════════════

class TestBloomFilter:
    """Bloom filter correctly bounds memory regardless of item count."""

    def test_basic_bloom_membership(self):
        """Items added to bloom filter should be found; absent items usually not."""
        bf = BloomFilter(capacity=1000, fp_rate=0.01)
        for i in range(100):
            bf.add(f"item_{i}")

        # All added items must be found (no false negatives)
        for i in range(100):
            assert f"item_{i}" in bf

        # False positives should be rare
        fp_count = sum(1 for i in range(10000, 11000) if f"item_{i}" in bf)
        assert fp_count < 50  # Should be ~1% of 1000 = ~10

    def test_bloom_memory_bounded(self):
        """Memory should be fixed based on capacity, not on items added."""
        bf = BloomFilter(capacity=1000, fp_rate=0.01)
        initial_memory = bf.memory_bytes()

        for i in range(5000):  # Add 5x the capacity
            bf.add(f"item_{i}")

        # Memory should not grow
        assert bf.memory_bytes() == initial_memory

    def test_ttl_bloom_segments_bounded(self):
        """TTL Bloom filter should never exceed max_segments."""
        tbf = TTLBloomFilter(
            segment_capacity=100,
            fp_rate=0.01,
            segment_ttl_seconds=0.01,  # Very short for testing
            max_segments=3,
        )

        # Add items across many time segments
        for i in range(50):
            tbf.add(f"item_{i}")
            if i % 10 == 0:
                time.sleep(0.02)  # Force new segment creation

        assert tbf.num_segments <= 3

    def test_ttl_bloom_memory_cap(self):
        """Total memory across all segments should be bounded."""
        tbf = TTLBloomFilter(
            segment_capacity=1000,
            fp_rate=0.01,
            segment_ttl_seconds=60.0,
            max_segments=5,
        )

        single_segment_memory = BloomFilter(1000, 0.01).memory_bytes()
        max_expected_memory = single_segment_memory * 5

        for i in range(3000):
            tbf.add(f"item_{i}")

        assert tbf.memory_bytes() <= max_expected_memory

    def test_node_dedup_memory_bounded(self):
        """Node's dedup filter should have bounded memory."""
        node = Node("test_node", dedup_capacity=500, dedup_max_segments=3)
        initial_mem = node.dedup_memory_bytes()

        # Simulate receiving many messages
        km = KeyManager()
        node.subscribe("ch", ChannelType.PUBLIC)
        for i in range(2000):
            env = create_public_message("ch", f"msg_{i}", km, hop_count=0)
            node.receive_raw(env.serialize())

        # Memory should be bounded
        max_mem = BloomFilter(500, 0.01).memory_bytes() * 3
        assert node.dedup_memory_bytes() <= max_mem * 2  # Allow some margin


# ═══════════════════════════════════════════════════════════════════
# Test (f): Public channel messages verified by signature
# ═══════════════════════════════════════════════════════════════════

class TestPublicChannelSignatures:
    """Public channel messages must be signed and verified."""

    def test_valid_signature_accepted(self):
        """A properly signed message should be accepted."""
        km = KeyManager()
        env = create_public_message("public_ch", "Signed message", km)

        assert verify_public_message(env) is True

    def test_tampered_payload_rejected(self):
        """If the payload is modified after signing, verification fails."""
        km = KeyManager()
        env = create_public_message("public_ch", "Original", km)

        # Tamper with payload
        env.payload = b"Tampered!"

        assert verify_public_message(env) is False

    def test_wrong_key_rejected(self):
        """Signature from a different key should not verify."""
        km1 = KeyManager()
        km2 = KeyManager()
        env = create_public_message("public_ch", "Message", km1)

        # Replace sender pubkey with different key
        env.sender_pubkey = km2.public_key_bytes()

        assert verify_public_message(env) is False

    def test_node_rejects_invalid_signature(self):
        """A node should not deliver a public message with invalid signature."""
        net = SimulatedNetwork()
        node = net.create_node("verifier")
        node.subscribe("ch", ChannelType.PUBLIC)

        km = KeyManager()
        env = create_public_message("ch", "Bad sig", km)
        env.payload = b"Tampered!"  # Invalidate signature

        node.receive_raw(env.serialize())

        assert len(node.received_messages) == 0

    def test_unsigned_public_message_rejected(self):
        """A public message without a signature should be rejected."""
        env = MessageEnvelope(
            channel_id="ch",
            payload=b"No signature",
            channel_type=ChannelType.PUBLIC,
        )
        assert verify_public_message(env) is False


# ═══════════════════════════════════════════════════════════════════
# Test (g): Private channel unreadable without key
# ═══════════════════════════════════════════════════════════════════

class TestPrivateChannel:
    """Private channel messages require the symmetric channel key."""

    def test_member_can_decrypt(self):
        """A member with the channel key can read the message."""
        channel_key = os.urandom(32)
        env = create_private_message("secret_ch", "Hidden text", channel_key)

        plaintext = decrypt_private_message(env, channel_key)
        assert plaintext == "Hidden text"

    def test_non_member_cannot_decrypt(self):
        """Without the correct key, decryption fails."""
        channel_key = os.urandom(32)
        wrong_key = os.urandom(32)
        env = create_private_message("secret_ch", "Hidden text", channel_key)

        plaintext = decrypt_private_message(env, wrong_key)
        assert plaintext is None

    def test_raw_payload_is_unreadable(self):
        """The encrypted payload should not contain the plaintext."""
        channel_key = os.urandom(32)
        env = create_private_message("secret_ch", "Super secret", channel_key)

        assert b"Super secret" not in env.payload

    def test_node_with_key_receives_private_message(self):
        """Node subscribed with the correct key decrypts private messages."""
        net = SimulatedNetwork()
        channel_key = os.urandom(32)

        node_a = net.create_node("A")
        node_b = net.create_node("B")
        node_c = net.create_node("C")  # No key
        net.connect_all()

        node_a.subscribe("priv", ChannelType.PRIVATE, channel_key=channel_key)
        node_b.subscribe("priv", ChannelType.PRIVATE, channel_key=channel_key)
        node_c.subscribe("priv", ChannelType.PRIVATE)  # No key!

        env = create_private_message("priv", "Members only", channel_key, hop_count=5)
        node_a.send_to_channel(env)

        # node_a should receive (it's the sender but also subscribed)
        a_texts = [t for t, _ in node_a.received_messages]
        assert "Members only" in a_texts

        # node_b should receive
        b_texts = [t for t, _ in node_b.received_messages]
        assert "Members only" in b_texts

        # node_c should NOT receive (no key)
        assert len(node_c.received_messages) == 0

    def test_symmetric_encrypt_decrypt_roundtrip(self):
        """Basic roundtrip of symmetric encryption."""
        key = os.urandom(32)
        ct = symmetric_encrypt(key, b"test data")
        pt = symmetric_decrypt(key, ct)
        assert pt == b"test data"


# ═══════════════════════════════════════════════════════════════════
# Test (h): Bootstrap — new node discovers peers and receives messages
# ═══════════════════════════════════════════════════════════════════

class TestBootstrap:
    """New nodes can bootstrap by connecting to well-known peers."""

    def test_new_node_bootstraps(self):
        """A new node connects to a bootstrap peer and learns about the network."""
        net = SimulatedNetwork()
        # Existing network
        node_a = net.create_node("A")
        node_b = net.create_node("B")
        node_c = net.create_node("C")
        # Connect existing nodes to each other
        for n1 in [node_a, node_b, node_c]:
            for n2 in [node_a, node_b, node_c]:
                if n1.node_id != n2.node_id:
                    n1.add_peer(n2.get_peer_info())

        # New node joins
        new_node = net.create_node("new")
        new_node.set_bootstrap_peers(["A"])
        new_node.bootstrap()

        # new_node should now know about A (from reply)
        assert "A" in new_node.peers
        # B and C should be shared by A
        assert "B" in new_node.peers
        assert "C" in new_node.peers

    def test_bootstrapped_node_receives_messages(self):
        """After bootstrapping, a new node can receive gossip messages."""
        net = SimulatedNetwork()
        node_a = net.create_node("A", fanout=5)
        node_b = net.create_node("B", fanout=5)
        node_a.add_peer(node_b.get_peer_info())
        node_b.add_peer(node_a.get_peer_info())

        # New node
        new_node = net.create_node("newcomer", fanout=5)
        new_node.set_bootstrap_peers(["A"])
        new_node.bootstrap()

        # Subscribe
        for n in [node_a, node_b, new_node]:
            n.subscribe("welcome", ChannelType.PUBLIC)

        # A should now know about newcomer, and vice versa
        assert "newcomer" in node_a.peers
        assert "A" in new_node.peers

        # Send a message
        env = create_public_message(
            "welcome", "Welcome!", node_a.key_manager, hop_count=10
        )
        node_a.send_to_channel(env)

        texts = [t for t, _ in new_node.received_messages]
        assert "Welcome!" in texts

    def test_bootstrap_bidirectional(self):
        """Bootstrap should make both the new node and existing nodes aware of each other."""
        net = SimulatedNetwork()
        existing = net.create_node("existing")

        joiner = net.create_node("joiner")
        joiner.set_bootstrap_peers(["existing"])
        joiner.bootstrap()

        assert "existing" in joiner.peers
        assert "joiner" in existing.peers


# ═══════════════════════════════════════════════════════════════════
# Test (i): Network partition and recovery
# ═══════════════════════════════════════════════════════════════════

class TestNetworkPartition:
    """Messages during partition don't leak; recovery works correctly."""

    def test_partition_isolates_groups(self):
        """During a partition, messages don't cross the boundary."""
        net = SimulatedNetwork()
        nodes = [net.create_node(f"n{i}", fanout=5) for i in range(6)]
        net.connect_all()

        for n in nodes:
            n.subscribe("ch", ChannelType.PUBLIC)

        # Partition: {n0, n1, n2} and {n3, n4, n5}
        net.set_partitions([
            {"n0", "n1", "n2"},
            {"n3", "n4", "n5"},
        ])

        env = create_public_message(
            "ch", "Partition test", nodes[0].key_manager, hop_count=10
        )
        nodes[0].send_to_channel(env)

        # n0, n1, n2 should receive
        for i in range(3):
            texts = [t for t, _ in nodes[i].received_messages]
            assert "Partition test" in texts, f"n{i} should receive"

        # n3, n4, n5 should NOT receive
        for i in range(3, 6):
            texts = [t for t, _ in nodes[i].received_messages]
            assert "Partition test" not in texts, f"n{i} should NOT receive"

    def test_messages_during_partition_dont_leak_after_recovery(self):
        """
        Messages sent during a partition should NOT appear on the
        other side after the partition heals. Nodes are ephemeral —
        they don't store and forward old messages.
        """
        net = SimulatedNetwork()
        nodes = [net.create_node(f"n{i}", fanout=5) for i in range(4)]
        net.connect_all()

        for n in nodes:
            n.subscribe("ch", ChannelType.PUBLIC)

        # Partition
        net.set_partitions([{"n0", "n1"}, {"n2", "n3"}])

        # Send message with very short TTL in partition A
        env = create_public_message(
            "ch", "Partition A msg",
            nodes[0].key_manager,
            ttl_seconds=1.0,
            hop_count=10,
        )
        nodes[0].send_to_channel(env)

        # n0 and n1 get it
        assert any(t == "Partition A msg" for t, _ in nodes[0].received_messages)
        assert any(t == "Partition A msg" for t, _ in nodes[1].received_messages)
        # n2, n3 don't
        assert not any(t == "Partition A msg" for t, _ in nodes[2].received_messages)

        # Wait for TTL to expire
        time.sleep(1.5)

        # Heal partition
        net.heal_partitions()

        # Send a new message from n1 to trigger gossip
        env2 = create_public_message(
            "ch", "After heal",
            nodes[1].key_manager,
            ttl_seconds=60.0,
            hop_count=10,
        )
        nodes[1].send_to_channel(env2)

        # n2, n3 should get the new message but NOT the old one
        n2_texts = [t for t, _ in nodes[2].received_messages]
        assert "After heal" in n2_texts
        assert "Partition A msg" not in n2_texts

    def test_partition_and_recovery_new_messages_flow(self):
        """After partition heals, new messages flow freely."""
        net = SimulatedNetwork()
        nodes = [net.create_node(f"n{i}", fanout=5) for i in range(4)]
        net.connect_all()

        for n in nodes:
            n.subscribe("ch", ChannelType.PUBLIC)

        # Partition then heal
        net.set_partitions([{"n0", "n1"}, {"n2", "n3"}])
        net.heal_partitions()

        env = create_public_message(
            "ch", "Post-heal message",
            nodes[0].key_manager,
            hop_count=10,
        )
        nodes[0].send_to_channel(env)

        for n in nodes:
            texts = [t for t, _ in n.received_messages]
            assert "Post-heal message" in texts


# ═══════════════════════════════════════════════════════════════════
# Additional structural tests
# ═══════════════════════════════════════════════════════════════════

class TestMessageEnvelope:
    """Test message envelope serialization and properties."""

    def test_envelope_fields(self):
        """Envelope has all required fields."""
        env = MessageEnvelope(
            channel_id="test",
            payload=b"hello",
            ttl_seconds=30.0,
            hop_count=5,
        )
        assert env.message_id  # Not empty
        assert env.created_ts > 0
        assert env.ttl_seconds == 30.0
        assert env.channel_id == "test"
        assert env.payload == b"hello"
        assert env.hop_count == 5

    def test_serialize_deserialize_roundtrip(self):
        """Serialization and deserialization should be lossless."""
        km = KeyManager()
        env = create_public_message("ch1", "roundtrip test", km)
        raw = env.serialize()
        restored = MessageEnvelope.deserialize(raw)

        assert restored.message_id == env.message_id
        assert restored.created_ts == env.created_ts
        assert restored.ttl_seconds == env.ttl_seconds
        assert restored.channel_id == env.channel_id
        assert restored.payload == env.payload
        assert restored.hop_count == env.hop_count
        assert restored.channel_type == env.channel_type
        assert restored.sender_pubkey == env.sender_pubkey
        assert restored.signature == env.signature

    def test_message_id_uniqueness(self):
        """Each message should have a unique ID."""
        ids = set()
        km = KeyManager()
        for _ in range(1000):
            env = create_public_message("ch", "msg", km)
            ids.add(env.message_id)
        assert len(ids) == 1000


class TestHopCount:
    """Test that hop counter limits gossip propagation."""

    def test_zero_hop_not_forwarded(self):
        """A message with hop_count=0 should not be forwarded to peers."""
        net = SimulatedNetwork()
        node_a = net.create_node("A")
        node_b = net.create_node("B")
        node_c = net.create_node("C")
        net.connect_all()

        for n in [node_a, node_b, node_c]:
            n.subscribe("ch", ChannelType.PUBLIC)

        km = KeyManager()
        env = create_public_message("ch", "No forward", km, hop_count=0)

        # Directly inject into node_a
        node_a.receive_raw(env.serialize())

        # node_a should have it
        a_texts = [t for t, _ in node_a.received_messages]
        assert "No forward" in a_texts

        # node_b and node_c should NOT have it (hop=0 means no forwarding)
        assert len(node_b.received_messages) == 0
        assert len(node_c.received_messages) == 0

    def test_hop_decrements(self):
        """Hop count decreases at each hop. Message propagates hop_count hops."""
        net = SimulatedNetwork()
        # Linear chain: n0 -> n1 -> n2 -> n3 -> n4
        nodes = [net.create_node(f"n{i}", fanout=5) for i in range(5)]
        for i in range(4):
            nodes[i].add_peer(nodes[i + 1].get_peer_info())
            nodes[i + 1].add_peer(nodes[i].get_peer_info())

        for n in nodes:
            n.subscribe("ch", ChannelType.PUBLIC)

        km = KeyManager()
        # hop_count=2: sender decrements to 1, first relay decrements to 0,
        # second relay receives but cannot forward
        # n0 sends (hop_count=2 → forwards with hop=1)
        # n1 receives hop=1 → forwards with hop=0
        # n2 receives hop=0 → delivers, no forwarding
        # n3 never receives
        env = create_public_message("ch", "Limited reach", km, hop_count=2)
        nodes[0].send_to_channel(env)

        assert any(t == "Limited reach" for t, _ in nodes[0].received_messages)
        assert any(t == "Limited reach" for t, _ in nodes[1].received_messages)
        assert any(t == "Limited reach" for t, _ in nodes[2].received_messages)
        # n3 should NOT receive — only 2 hops from sender
        assert not any(t == "Limited reach" for t, _ in nodes[3].received_messages)
        assert not any(t == "Limited reach" for t, _ in nodes[4].received_messages)


class TestEdge:
    """Edge cases and robustness tests."""

    def test_node_identity_keypair(self):
        """Each node has unique Ed25519 keypairs."""
        n1 = Node("n1")
        n2 = Node("n2")
        assert n1.key_manager.public_key_bytes() != n2.key_manager.public_key_bytes()

    def test_packet_loss_simulation(self):
        """With 100% packet loss, no messages are delivered."""
        net = SimulatedNetwork(packet_loss_rate=1.0)
        node_a = net.create_node("A")
        node_b = net.create_node("B")
        net.connect_all()

        node_b.subscribe("ch", ChannelType.PUBLIC)

        km = KeyManager()
        env = create_public_message("ch", "Lost", km, hop_count=5)
        node_a.send_to_channel(env)

        assert len(node_b.received_messages) == 0

    def test_malformed_data_handled_gracefully(self):
        """Malformed data should not crash the node."""
        node = Node("safe")
        node.receive_raw(b"garbage data that is not json")
        node.receive_raw(b"")
        node.receive_raw(b"\x00\x01\x02")
        # No exceptions should be raised

    def test_self_peer_rejected(self):
        """A node should not add itself as a peer."""
        node = Node("self")
        node.add_peer(node.get_peer_info())
        assert "self" not in node.peers
