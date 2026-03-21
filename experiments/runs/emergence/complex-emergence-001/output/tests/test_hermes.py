"""Comprehensive tests for HermesP2P."""

import os
import time
import threading
import pytest

from hermes_p2p.identity import NodeIdentity
from hermes_p2p.message import Message, MessageEnvelope
from hermes_p2p.bloom_filter import BloomFilter, TTLBloomFilter
from hermes_p2p.onion import build_onion, peel_onion, PACKET_SIZE
from hermes_p2p.channels import PublicChannel, PrivateChannel, DirectMessage
from hermes_p2p.network import SimulatedNetwork
from hermes_p2p.node import Node, PeerInfo


def _make_peer_info(node: Node) -> PeerInfo:
    """Helper to create PeerInfo from a Node."""
    return PeerInfo(
        node_id=node.node_id,
        address=node.address,
        verify_key_bytes=bytes(node.identity.verify_key),
        public_key_bytes=bytes(node.identity.public_key),
    )


def _setup_mesh(network: SimulatedNetwork, nodes: list) -> None:
    """Connect all nodes to each other as peers."""
    for i, n1 in enumerate(nodes):
        for j, n2 in enumerate(nodes):
            if i != j:
                n1.add_peer(_make_peer_info(n2))


class TestMessageReachesSubscribers:
    """(a) Message reaches all subscribers within TTL."""

    def test_message_propagates_to_all_nodes(self):
        network = SimulatedNetwork()
        nodes = [Node(network=network, fanout=3) for _ in range(5)]
        _setup_mesh(network, nodes)

        received = {n.node_id: [] for n in nodes}
        for n in nodes:
            n.on_message(lambda msg, nid=n.node_id: received[nid].append(msg.message_id))

        # Node 0 sends a message
        msg = Message(channel_id="test", payload=b"hello world", ttl_seconds=60.0)
        envelope = MessageEnvelope(message=msg, hops_remaining=6)
        nodes[0].send_message(envelope)

        # All nodes should have received the message
        for n in nodes:
            assert msg.message_id in received[n.node_id], (
                f"Node {n.node_id} did not receive the message"
            )

    def test_message_propagates_in_chain(self):
        """Even in a chain topology, messages propagate."""
        network = SimulatedNetwork()
        nodes = [Node(network=network, fanout=3) for _ in range(4)]
        # Chain: 0-1-2-3
        for i in range(len(nodes) - 1):
            nodes[i].add_peer(_make_peer_info(nodes[i + 1]))
            nodes[i + 1].add_peer(_make_peer_info(nodes[i]))

        received = {n.node_id: [] for n in nodes}
        for n in nodes:
            n.on_message(lambda msg, nid=n.node_id: received[nid].append(msg.message_id))

        msg = Message(channel_id="test", payload=b"chain msg", ttl_seconds=60.0)
        envelope = MessageEnvelope(message=msg, hops_remaining=10)
        nodes[0].send_message(envelope)

        for n in nodes:
            assert msg.message_id in received[n.node_id]


class TestExpiredMessagesDropped:
    """(b) Expired messages are dropped."""

    def test_expired_message_is_dropped(self):
        network = SimulatedNetwork()
        nodes = [Node(network=network, fanout=3) for _ in range(3)]
        _setup_mesh(network, nodes)

        received = []
        nodes[1].on_message(lambda msg: received.append(msg.message_id))

        # Create an already-expired message
        msg = Message(
            channel_id="test",
            payload=b"old message",
            created_ts=time.time() - 120,
            ttl_seconds=5.0,
        )
        envelope = MessageEnvelope(message=msg, hops_remaining=6)
        nodes[0].send_message(envelope)

        # Node 1 should NOT have received the message
        assert msg.message_id not in received

    def test_message_within_ttl_is_accepted(self):
        network = SimulatedNetwork()
        nodes = [Node(network=network, fanout=3) for _ in range(3)]
        _setup_mesh(network, nodes)

        received = []
        nodes[1].on_message(lambda msg: received.append(msg.message_id))

        msg = Message(channel_id="test", payload=b"fresh", ttl_seconds=300.0)
        envelope = MessageEnvelope(message=msg, hops_remaining=6)
        nodes[0].send_message(envelope)

        assert msg.message_id in received

    def test_clock_skew_tolerance(self):
        """Messages within skew tolerance are accepted."""
        network = SimulatedNetwork()
        node = Node(network=network, clock_skew_tolerance=30.0)

        # Message created 5 seconds ago with 0 TTL but within skew tolerance
        msg = Message(
            channel_id="test",
            payload=b"skewed",
            created_ts=time.time() - 5,
            ttl_seconds=0.0,
        )
        assert not node._is_expired(msg)  # Within 30s tolerance

        # Message way past tolerance
        msg2 = Message(
            channel_id="test",
            payload=b"really old",
            created_ts=time.time() - 100,
            ttl_seconds=0.0,
        )
        assert node._is_expired(msg2)


class TestDeduplication:
    """(c) Duplicate messages are deduplicated."""

    def test_duplicate_not_forwarded(self):
        network = SimulatedNetwork()
        nodes = [Node(network=network, fanout=3) for _ in range(3)]
        _setup_mesh(network, nodes)

        receive_count = [0]
        nodes[1].on_message(lambda msg: receive_count.__setitem__(0, receive_count[0] + 1))

        msg = Message(channel_id="test", payload=b"hello", ttl_seconds=60.0)
        envelope = MessageEnvelope(message=msg, hops_remaining=6)

        # Send same message twice from node 0
        nodes[0].send_message(envelope)
        # Reset node 0's bloom so it would re-send
        nodes[0]._seen = TTLBloomFilter()
        nodes[0].send_message(envelope)

        # Node 1 should have received it only once (dedup on node 1)
        assert receive_count[0] == 1

    def test_bloom_filter_dedup(self):
        bloom = BloomFilter(capacity=1000)
        bloom.add("msg123")
        assert "msg123" in bloom
        assert "msg456" not in bloom


class TestOnionRouting:
    """(d) Intermediate relays cannot read the payload."""

    def test_relay_cannot_read_payload(self):
        # Create 4 identities: sender, relay1, relay2, recipient
        sender = NodeIdentity()
        relay1 = NodeIdentity()
        relay2 = NodeIdentity()
        recipient = NodeIdentity()

        secret_payload = b"super secret message for recipient only"

        route = [
            relay1.public_key,
            relay2.public_key,
            recipient.public_key,
        ]

        packet = build_onion(route, secret_payload)

        # Packet must be constant size
        assert len(packet) == PACKET_SIZE

        # Relay 1 peels
        is_final, next_hop, inner, fwd = peel_onion(relay1.private_key, packet)
        assert not is_final
        assert bytes(next_hop) == bytes(relay2.public_key)
        # Relay 1 cannot see payload
        assert secret_payload not in packet
        assert secret_payload not in fwd
        # Forwarding packet is constant size
        assert len(fwd) == PACKET_SIZE

        # Relay 2 peels
        is_final2, next_hop2, inner2, fwd2 = peel_onion(relay2.private_key, fwd)
        assert not is_final2
        assert bytes(next_hop2) == bytes(recipient.public_key)
        assert secret_payload not in fwd2
        assert len(fwd2) == PACKET_SIZE

        # Recipient peels
        is_final3, _, payload_out, _ = peel_onion(recipient.private_key, fwd2)
        assert is_final3
        assert payload_out == secret_payload

    def test_single_hop_onion(self):
        recipient = NodeIdentity()
        payload = b"direct message"
        route = [recipient.public_key]
        packet = build_onion(route, payload)
        assert len(packet) == PACKET_SIZE

        is_final, _, data, _ = peel_onion(recipient.private_key, packet)
        assert is_final
        assert data == payload

    def test_constant_size_at_each_hop(self):
        identities = [NodeIdentity() for _ in range(5)]
        route = [i.public_key for i in identities]
        payload = b"test payload"
        packet = build_onion(route, payload)
        assert len(packet) == PACKET_SIZE

        current = packet
        for i, ident in enumerate(identities):
            is_final, next_hop, data, fwd = peel_onion(ident.private_key, current)
            if i < len(identities) - 1:
                assert not is_final
                assert len(fwd) == PACKET_SIZE
                current = fwd
            else:
                assert is_final
                assert data == payload


class TestBloomFilterMemory:
    """(e) Bloom filter bounds memory usage."""

    def test_memory_bounded(self):
        bloom = TTLBloomFilter(capacity=1000, error_rate=0.01)
        initial_mem = bloom.memory_bytes()

        # Add many items
        for i in range(5000):
            bloom.add(f"msg_{i}")

        final_mem = bloom.memory_bytes()
        # Memory should be bounded (at most 2x a single filter due to rotation)
        single_filter_mem = BloomFilter(1000, 0.01).memory_bytes()
        assert final_mem <= 2 * single_filter_mem + 100  # small overhead tolerance

    def test_rotation_happens(self):
        bloom = TTLBloomFilter(capacity=100, error_rate=0.01)
        for i in range(200):
            bloom.add(f"item_{i}")
        # After adding 200 items with capacity 100, rotation should have happened
        # Recent items should still be found
        assert f"item_199" in bloom

    def test_bloom_false_positive_rate(self):
        """Verify the Bloom filter has a reasonable false positive rate."""
        bloom = BloomFilter(capacity=10000, error_rate=0.01)
        for i in range(10000):
            bloom.add(f"existing_{i}")

        false_positives = 0
        test_count = 10000
        for i in range(test_count):
            if f"nonexistent_{i}" in bloom:
                false_positives += 1

        fp_rate = false_positives / test_count
        # Should be close to 0.01, allow generous tolerance
        assert fp_rate < 0.05, f"False positive rate too high: {fp_rate}"


class TestPublicChannelSignature:
    """(f) Public channel messages are verified by signature."""

    def test_valid_signature(self):
        identity = NodeIdentity()
        channel = PublicChannel("pub-general")

        plaintext = b"Hello, public channel!"
        payload = channel.create_payload(identity, plaintext)

        result = channel.verify_and_read(payload)
        assert result is not None
        vk_bytes, recovered = result
        assert recovered == plaintext
        assert vk_bytes == bytes(identity.verify_key)

    def test_tampered_message_fails_verification(self):
        identity = NodeIdentity()
        channel = PublicChannel("pub-general")

        plaintext = b"Original message"
        payload = channel.create_payload(identity, plaintext)

        # Tamper with the payload
        tampered = bytearray(payload)
        tampered[-1] ^= 0xFF
        tampered = bytes(tampered)

        result = channel.verify_and_read(tampered)
        assert result is None

    def test_different_signer_fails(self):
        identity1 = NodeIdentity()
        identity2 = NodeIdentity()
        channel = PublicChannel("pub-general")

        plaintext = b"Hello"
        payload = channel.create_payload(identity1, plaintext)

        # Replace verify key with identity2's but keep identity1's signature
        tampered = bytes(identity2.verify_key) + payload[32:]
        result = channel.verify_and_read(tampered)
        assert result is None


class TestPrivateChannelEncryption:
    """(g) Private channel messages are unreadable without channel key."""

    def test_encrypt_decrypt_with_key(self):
        key = PrivateChannel.generate_key()
        channel = PrivateChannel("priv-secret", key)

        plaintext = b"Secret message for members only"
        encrypted = channel.create_payload(plaintext)

        # Decrypt with same key
        decrypted = channel.read_payload(encrypted)
        assert decrypted == plaintext

    def test_cannot_decrypt_without_key(self):
        key = PrivateChannel.generate_key()
        wrong_key = PrivateChannel.generate_key()

        channel = PrivateChannel("priv-secret", key)
        wrong_channel = PrivateChannel("priv-secret", wrong_key)

        plaintext = b"Secret message"
        encrypted = channel.create_payload(plaintext)

        # Try to decrypt with wrong key
        result = wrong_channel.read_payload(encrypted)
        assert result is None

    def test_ciphertext_differs_from_plaintext(self):
        key = PrivateChannel.generate_key()
        channel = PrivateChannel("priv-secret", key)

        plaintext = b"Secret message"
        encrypted = channel.create_payload(plaintext)

        assert plaintext not in encrypted


class TestBootstrap:
    """(h) New node successfully bootstraps and receives messages."""

    def test_bootstrap_discovers_peers(self):
        network = SimulatedNetwork()

        # Create initial network of 3 nodes
        boot_node = Node(address="boot://seed1", network=network, fanout=3)
        node_a = Node(network=network, fanout=3)
        node_b = Node(network=network, fanout=3)

        # Establish initial mesh
        _setup_mesh(network, [boot_node, node_a, node_b])

        # New node bootstraps
        new_node = Node(network=network, fanout=3)
        new_node.set_bootstrap_addresses(["boot://seed1"])
        new_node.bootstrap()

        # New node should know about existing peers
        peer_ids = {p.node_id for p in new_node.get_peers()}
        assert boot_node.node_id in peer_ids
        assert node_a.node_id in peer_ids or node_b.node_id in peer_ids

    def test_bootstrapped_node_receives_messages(self):
        network = SimulatedNetwork()

        boot_node = Node(address="boot://seed1", network=network, fanout=3)
        node_a = Node(network=network, fanout=3)
        _setup_mesh(network, [boot_node, node_a])

        # New node bootstraps
        new_node = Node(network=network, fanout=3)
        new_node.set_bootstrap_addresses(["boot://seed1"])
        new_node.bootstrap()

        received = []
        new_node.on_message(lambda msg: received.append(msg.message_id))

        # Send a message from node_a
        msg = Message(channel_id="test", payload=b"hello new node", ttl_seconds=60.0)
        envelope = MessageEnvelope(message=msg, hops_remaining=6)
        node_a.send_message(envelope)

        assert msg.message_id in received


class TestNetworkPartition:
    """(i) Network partition and recovery: messages during partition don't leak."""

    def test_partition_blocks_messages(self):
        network = SimulatedNetwork()
        nodes = [Node(network=network, fanout=3) for _ in range(4)]
        _setup_mesh(network, nodes)

        # Partition: nodes 0,1 can't talk to nodes 2,3
        for i in [0, 1]:
            for j in [2, 3]:
                network.add_partition(nodes[i].node_id, nodes[j].node_id)

        received_side_a = {nodes[0].node_id: [], nodes[1].node_id: []}
        received_side_b = {nodes[2].node_id: [], nodes[3].node_id: []}

        nodes[0].on_message(lambda msg: received_side_a[nodes[0].node_id].append(msg.message_id))
        nodes[1].on_message(lambda msg: received_side_a[nodes[1].node_id].append(msg.message_id))
        nodes[2].on_message(lambda msg: received_side_b[nodes[2].node_id].append(msg.message_id))
        nodes[3].on_message(lambda msg: received_side_b[nodes[3].node_id].append(msg.message_id))

        # Send from side A
        msg_a = Message(channel_id="test", payload=b"from side A", ttl_seconds=60.0)
        envelope_a = MessageEnvelope(message=msg_a, hops_remaining=6)
        nodes[0].send_message(envelope_a)

        # Side B should NOT receive it
        assert msg_a.message_id not in received_side_b[nodes[2].node_id]
        assert msg_a.message_id not in received_side_b[nodes[3].node_id]

        # Side A should receive it
        assert msg_a.message_id in received_side_a[nodes[0].node_id]
        assert msg_a.message_id in received_side_a[nodes[1].node_id]

    def test_partition_recovery_no_leak_of_expired(self):
        """After partition heals, expired messages don't propagate."""
        network = SimulatedNetwork()
        nodes = [Node(network=network, fanout=3) for _ in range(4)]
        _setup_mesh(network, nodes)

        # Partition
        for i in [0, 1]:
            for j in [2, 3]:
                network.add_partition(nodes[i].node_id, nodes[j].node_id)

        received_b = []
        nodes[2].on_message(lambda msg: received_b.append(msg.message_id))
        nodes[3].on_message(lambda msg: received_b.append(msg.message_id))

        # Send message with very short TTL from side A during partition
        msg = Message(
            channel_id="test",
            payload=b"ephemeral",
            ttl_seconds=1.0,
            created_ts=time.time() - 60,  # already expired
        )
        envelope = MessageEnvelope(message=msg, hops_remaining=6)
        nodes[0].send_message(envelope)

        # Heal partition
        for i in [0, 1]:
            for j in [2, 3]:
                network.remove_partition(nodes[i].node_id, nodes[j].node_id)

        # Try to forward the message again after healing (simulating gossip)
        # Since the message is expired, it should be dropped
        envelope2 = MessageEnvelope(message=msg, hops_remaining=6, sender_id=nodes[0].node_id)
        data = envelope2.serialize()
        nodes[2].receive_raw(data, nodes[0].node_id)

        assert msg.message_id not in received_b

    def test_new_messages_after_recovery_propagate(self):
        """After partition heals, new messages propagate fine."""
        network = SimulatedNetwork()
        nodes = [Node(network=network, fanout=3) for _ in range(4)]
        _setup_mesh(network, nodes)

        # Partition
        for i in [0, 1]:
            for j in [2, 3]:
                network.add_partition(nodes[i].node_id, nodes[j].node_id)

        # Heal
        for i in [0, 1]:
            for j in [2, 3]:
                network.remove_partition(nodes[i].node_id, nodes[j].node_id)

        received = []
        nodes[2].on_message(lambda msg: received.append(msg.message_id))

        msg = Message(channel_id="test", payload=b"after recovery", ttl_seconds=60.0)
        envelope = MessageEnvelope(message=msg, hops_remaining=6)
        nodes[0].send_message(envelope)

        assert msg.message_id in received


class TestOnionRoutingEndToEnd:
    """End-to-end onion routing through the simulated network."""

    def test_onion_message_delivered(self):
        network = SimulatedNetwork()
        sender = Node(network=network, fanout=3)
        relay1 = Node(network=network, fanout=3)
        relay2 = Node(network=network, fanout=3)
        recipient = Node(network=network, fanout=3)

        all_nodes = [sender, relay1, relay2, recipient]
        _setup_mesh(network, all_nodes)

        received_payloads = []
        recipient.on_onion(lambda data: received_payloads.append(data))

        payload = b"secret end-to-end message"
        sender.send_onion(
            [relay1.node_id, relay2.node_id, recipient.node_id],
            payload,
        )

        assert payload in received_payloads

    def test_relay_does_not_see_payload(self):
        """Relays should not be able to read the final payload."""
        network = SimulatedNetwork()
        sender = Node(network=network)
        relay1 = Node(network=network)
        relay2 = Node(network=network)
        recipient = Node(network=network)

        all_nodes = [sender, relay1, relay2, recipient]
        _setup_mesh(network, all_nodes)

        relay1_raw = []
        relay2_raw = []
        original_receive_1 = relay1.receive_raw
        original_receive_2 = relay2.receive_raw

        def spy_relay1(data, from_id):
            relay1_raw.append(data)
            original_receive_1(data, from_id)

        def spy_relay2(data, from_id):
            relay2_raw.append(data)
            original_receive_2(data, from_id)

        relay1.receive_raw = spy_relay1
        relay2.receive_raw = spy_relay2

        received = []
        recipient.on_onion(lambda data: received.append(data))

        payload = b"ultra secret payload xyz123"
        sender.send_onion(
            [relay1.node_id, relay2.node_id, recipient.node_id],
            payload,
        )

        # Recipient got it
        assert payload in received

        # Relays could not see the payload in what they received
        for raw in relay1_raw:
            assert payload not in raw
        for raw in relay2_raw:
            assert payload not in raw


class TestDirectMessage:
    """Test Direct Message channel."""

    def test_direct_message_creation(self):
        sender = NodeIdentity()
        recipient = NodeIdentity()
        dm = DirectMessage()

        payload = b"private DM"
        route = [recipient.public_key]
        onion = dm.create_onion_payload(sender, route, payload)

        assert len(onion) == PACKET_SIZE

        is_final, _, data, _ = peel_onion(recipient.private_key, onion)
        assert is_final
        assert data == payload


class TestMessageSerialization:
    """Test message serialization/deserialization."""

    def test_message_roundtrip(self):
        msg = Message(
            channel_id="test-chan",
            payload=b"hello\x00world",
            ttl_seconds=120.0,
        )
        data = msg.serialize()
        recovered = Message.deserialize(data)
        assert recovered.message_id == msg.message_id
        assert recovered.channel_id == msg.channel_id
        assert recovered.payload == msg.payload
        assert recovered.ttl_seconds == msg.ttl_seconds
        assert abs(recovered.created_ts - msg.created_ts) < 0.001

    def test_envelope_roundtrip(self):
        msg = Message(channel_id="ch", payload=b"data")
        env = MessageEnvelope(message=msg, hops_remaining=5, sender_id="node123")
        data = env.serialize()
        recovered = MessageEnvelope.deserialize(data)
        assert recovered.message.message_id == msg.message_id
        assert recovered.hops_remaining == 5
        assert recovered.sender_id == "node123"


class TestNodeIdentity:
    """Test node identity operations."""

    def test_sign_and_verify(self):
        identity = NodeIdentity()
        data = b"test data to sign"
        sig = identity.sign(data)
        assert identity.verify(data, sig)

    def test_verify_fails_for_wrong_data(self):
        identity = NodeIdentity()
        data = b"original"
        sig = identity.sign(data)
        assert not identity.verify(b"tampered", sig)

    def test_encrypt_decrypt(self):
        alice = NodeIdentity()
        bob = NodeIdentity()
        plaintext = b"Hello Bob!"
        ciphertext = alice.encrypt_for(bob.public_key, plaintext)
        decrypted = bob.decrypt_from(alice.public_key, ciphertext)
        assert decrypted == plaintext

    def test_unique_node_ids(self):
        ids = {NodeIdentity().node_id for _ in range(100)}
        assert len(ids) == 100
