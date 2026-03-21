"""Tests for network simulation."""

import time
import pytest
from hermes_p2p.node import Node
from hermes_p2p.network import Network
from hermes_p2p.models import PeerInfo


class TestNetworkBasics:
    def test_create_network(self):
        net = Network()
        assert len(net.nodes) == 0

    def test_add_nodes(self):
        net = Network()
        n1 = Node(node_id="n1")
        n2 = Node(node_id="n2")
        net.add_node(n1)
        net.add_node(n2)
        assert len(net.nodes) == 2

    def test_connect_all(self):
        net = Network()
        nodes = [Node(node_id=f"n{i}") for i in range(4)]
        for n in nodes:
            net.add_node(n)
        net.connect_all()
        for n in nodes:
            assert len(n.peers) == 3  # Connected to all others


class TestBootstrap:
    def test_new_node_bootstraps(self):
        """Test (h): New node bootstraps and receives messages."""
        net = Network()
        # Create initial network
        n1 = Node(node_id="n1")
        n2 = Node(node_id="n2")
        n3 = Node(node_id="n3")
        net.add_node(n1)
        net.add_node(n2)
        net.add_node(n3)
        net.connect_all()

        # New node bootstraps
        new_node = Node(node_id="new_node")
        net.bootstrap_node(new_node, n1)

        # New node should have peers
        assert len(new_node.peers) >= 1

        # Subscribe new node to a channel
        received = []
        new_node.subscribe("ch", lambda env: received.append(env))

        # Publish from n2
        n2.publish("ch", b"welcome new node", ttl=5)

        # New node should receive the message (since it's now connected)
        assert len(received) >= 1, "New bootstrapped node should receive messages"
        assert received[0].payload == b"welcome new node"


class TestPartition:
    def test_partition_blocks_messages(self):
        """Test (i): Network partition and recovery."""
        net = Network(seed=42)
        n1 = Node(node_id="n1")
        n2 = Node(node_id="n2")
        n3 = Node(node_id="n3")
        n4 = Node(node_id="n4")

        for n in [n1, n2, n3, n4]:
            net.add_node(n)
        net.connect_all()

        # Create partition: {n1, n2} and {n3, n4}
        net.create_partition(["n1", "n2"], ["n3", "n4"])

        received_n3 = []
        n3.subscribe("ch", lambda env: received_n3.append(env))

        received_n2 = []
        n2.subscribe("ch", lambda env: received_n2.append(env))

        # n1 publishes - should reach n2 but NOT n3 (different partition)
        n1.publish("ch", b"partition test", ttl=5)

        assert len(received_n2) >= 1, "n2 should receive from n1 (same partition)"
        assert len(received_n3) == 0, "n3 should NOT receive from n1 (different partition)"

    def test_partition_recovery(self):
        """Test (i): After healing, messages flow again."""
        net = Network(seed=42)
        n1 = Node(node_id="n1")
        n2 = Node(node_id="n2")
        n3 = Node(node_id="n3")

        for n in [n1, n2, n3]:
            net.add_node(n)
        net.connect_all()

        # Partition
        net.create_partition(["n1"], ["n2", "n3"])

        received_n3 = []
        n3.subscribe("ch", lambda env: received_n3.append(env))

        # During partition, n1 can't reach n3
        n1.publish("ch", b"during partition", ttl=5)
        assert len(received_n3) == 0

        # Heal partition
        net.heal_partition()

        # After healing, messages should flow
        received_n3_after = []
        n3.subscribe("ch_after", lambda env: received_n3_after.append(env))
        n1.publish("ch_after", b"after recovery", ttl=5)
        assert len(received_n3_after) >= 1, "After healing, messages should flow"


class TestPacketLoss:
    def test_with_no_loss(self):
        net = Network(packet_loss=0.0, seed=42)
        n1 = Node(node_id="n1")
        n2 = Node(node_id="n2")
        net.add_node(n1)
        net.add_node(n2)
        net.connect_all()

        received = []
        n2.subscribe("ch", lambda env: received.append(env))
        n1.publish("ch", b"reliable", ttl=5)
        assert len(received) >= 1
