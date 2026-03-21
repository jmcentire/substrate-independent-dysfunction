"""Tests for core node functionality - gossip, TTL, deduplication."""

import time
import pytest
from hermes_p2p.node import Node
from hermes_p2p.models import MessageEnvelope, PeerInfo


class TestNodeBasics:
    def test_create_node(self):
        node = Node(node_id="test_node")
        assert node.node_id == "test_node"
        assert node.public_key is not None

    def test_add_peer(self):
        n1 = Node(node_id="n1")
        n2 = Node(node_id="n2")
        pi = PeerInfo(node_id="n2", address="sim://n2", public_key=n2.public_key)
        n1.add_peer(pi, n2)
        assert "n2" in n1.peers

    def test_dont_add_self_as_peer(self):
        n1 = Node(node_id="n1")
        pi = PeerInfo(node_id="n1", address="sim://n1")
        n1.add_peer(pi)
        assert "n1" not in n1.peers

    def test_subscribe_and_publish(self):
        node = Node(node_id="n1")
        received = []
        node.subscribe("ch1", lambda env: received.append(env))
        node.publish("ch1", b"hello")
        assert len(received) == 1
        assert received[0].payload == b"hello"


class TestGossipForwarding:
    def _make_connected_nodes(self, count=5):
        nodes = [Node(node_id=f"node_{i}") for i in range(count)]
        for i, ni in enumerate(nodes):
            for j, nj in enumerate(nodes):
                if i != j:
                    pi = PeerInfo(node_id=nj.node_id, address=f"sim://{nj.node_id}", public_key=nj.public_key)
                    ni.add_peer(pi, nj)
        return nodes

    def test_message_reaches_subscribers(self):
        """Test (a): Message reaches all subscribers within TTL."""
        nodes = self._make_connected_nodes(5)
        received = {n.node_id: [] for n in nodes}

        for node in nodes:
            node.subscribe("topic", lambda env, nid=node.node_id: received[nid].append(env))

        # Node 0 publishes
        nodes[0].publish("topic", b"broadcast_message", ttl=5)

        # All nodes should have received the message
        for node_id, msgs in received.items():
            assert len(msgs) >= 1, f"Node {node_id} didn't receive the message"

    def test_expired_messages_dropped(self):
        """Test (b): Expired messages are dropped."""
        n1 = Node(node_id="n1")
        n2 = Node(node_id="n2")
        pi = PeerInfo(node_id="n2", address="sim://n2")
        n1.add_peer(pi, n2)

        received = []
        n2.subscribe("ch", lambda env: received.append(env))

        # Send a message with timestamp far in the past (beyond max_skew)
        old_env = MessageEnvelope(
            channel="ch",
            payload=b"expired",
            sender_id="n1",
            timestamp=time.time() - 60,  # 60 seconds old
            ttl=5,
        )
        n2.receive_message(old_env)
        assert len(received) == 0, "Expired message should be dropped"

    def test_ttl_zero_dropped(self):
        """Messages with TTL=0 are dropped."""
        node = Node(node_id="n1")
        received = []
        node.subscribe("ch", lambda env: received.append(env))

        env = MessageEnvelope(
            channel="ch",
            payload=b"dead",
            sender_id="other",
            timestamp=time.time(),
            ttl=0,
        )
        node.receive_message(env)
        assert len(received) == 0

    def test_duplicate_deduplication(self):
        """Test (c): Duplicate messages are deduplicated."""
        node = Node(node_id="n1")
        received = []
        node.subscribe("ch", lambda env: received.append(env))

        env = MessageEnvelope(
            channel="ch",
            payload=b"unique",
            sender_id="sender",
            timestamp=time.time(),
            ttl=5,
        )
        node.receive_message(env)
        node.receive_message(env)  # Duplicate
        node.receive_message(env)  # Duplicate

        assert len(received) == 1, "Duplicate messages should be deduplicated"

    def test_hop_count_increases(self):
        """Hop count should increase as messages propagate."""
        nodes = self._make_connected_nodes(3)
        received_at_last = []
        nodes[2].subscribe("ch", lambda env: received_at_last.append(env))

        nodes[0].publish("ch", b"data", ttl=5)

        # The message at node 2 should have hop_count > 0
        if received_at_last:
            assert received_at_last[0].hop_count >= 1

    def test_fanout_limits_forwarding(self):
        """Node should forward to at most fanout peers."""
        # Create a node with fanout=2 connected to 10 peers
        center = Node(node_id="center", fanout=2)
        peers = [Node(node_id=f"peer_{i}") for i in range(10)]

        for p in peers:
            pi = PeerInfo(node_id=p.node_id, address=f"sim://{p.node_id}")
            center.add_peer(pi, p)

        center.publish("ch", b"limited_fanout", ttl=3)

        # Due to fanout=2, not all 10 peers should receive the message directly
        received_count = sum(1 for p in peers if len(p.received_messages) > 0)
        assert received_count <= 2, f"Fanout=2 but {received_count} peers received"
