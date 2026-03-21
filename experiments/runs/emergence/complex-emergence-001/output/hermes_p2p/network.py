"""Simulated Network with configurable latency, packet loss, and partition support."""

import random
import time
import threading
from typing import Dict, List, Optional, Set, Tuple

from hermes_p2p.models import MessageEnvelope, PeerInfo
from hermes_p2p.node import Node


class Network:
    """Simulated P2P network.

    Features:
    - Configurable latency and packet loss
    - Peer discovery via bootstrap nodes
    - Gossip-based peer announcements
    - Network partition and recovery simulation
    """

    def __init__(
        self,
        latency_ms: float = 0.0,
        packet_loss: float = 0.0,
        seed: Optional[int] = None,
    ):
        self.latency_ms = latency_ms
        self.packet_loss = packet_loss
        self._nodes: Dict[str, Node] = {}
        self._partitions: List[Set[str]] = []  # Each set is a partition
        self._lock = threading.Lock()
        self._rng = random.Random(seed)

    def add_node(self, node: Node):
        """Add a node to the network."""
        with self._lock:
            self._nodes[node.node_id] = node
            node._network = self

    def remove_node(self, node_id: str):
        """Remove a node from the network."""
        with self._lock:
            node = self._nodes.pop(node_id, None)
            if node:
                node._network = None

    def get_node(self, node_id: str) -> Optional[Node]:
        return self._nodes.get(node_id)

    @property
    def nodes(self) -> List[Node]:
        return list(self._nodes.values())

    def connect_all(self):
        """Connect all nodes to each other (fully connected topology)."""
        node_list = list(self._nodes.values())
        for i, node_a in enumerate(node_list):
            for j, node_b in enumerate(node_list):
                if i != j:
                    peer_info = PeerInfo(
                        node_id=node_b.node_id,
                        address=f"sim://{node_b.node_id}",
                        public_key=node_b.public_key,
                    )
                    node_a.add_peer(peer_info, node_b)

    def bootstrap_node(self, new_node: Node, bootstrap_node: Node):
        """Bootstrap a new node into the network via a bootstrap node."""
        self.add_node(new_node)
        new_node.bootstrap(bootstrap_node)

    def _can_communicate(self, node_a_id: str, node_b_id: str) -> bool:
        """Check if two nodes can communicate (not partitioned)."""
        if not self._partitions:
            return True  # No partitions
        for partition in self._partitions:
            if node_a_id in partition and node_b_id in partition:
                return True
        # If partitions are defined but nodes aren't in the same one
        # Check if either node is not in any partition (then it can talk to anyone)
        a_in_partition = any(node_a_id in p for p in self._partitions)
        b_in_partition = any(node_b_id in p for p in self._partitions)
        if not a_in_partition or not b_in_partition:
            return True  # Nodes not assigned to partitions can communicate freely
        return False

    def _should_drop_packet(self) -> bool:
        """Determine if a packet should be dropped based on loss rate."""
        return self._rng.random() < self.packet_loss

    def _apply_latency(self):
        """Simulate network latency."""
        if self.latency_ms > 0:
            # Add some jitter
            actual_latency = self.latency_ms * (0.5 + self._rng.random())
            time.sleep(actual_latency / 1000.0)

    def _gossip_from(self, sender: Node, envelope: MessageEnvelope):
        """Handle gossip forwarding when nodes don't have direct peer references."""
        available_peers = [
            node for node_id, node in self._nodes.items()
            if node_id != sender.node_id
            and node_id != envelope.sender_id
            and self._can_communicate(sender.node_id, node_id)
            and node._running
        ]

        if not available_peers:
            return

        selected = self._rng.sample(
            available_peers,
            min(sender.fanout, len(available_peers))
        )

        for peer_node in selected:
            if not self._should_drop_packet():
                self._apply_latency()
                peer_node.receive_message(envelope)

    def send_message(self, from_node: Node, to_node_id: str, envelope: MessageEnvelope) -> bool:
        """Send a message from one node to another."""
        if not self._can_communicate(from_node.node_id, to_node_id):
            return False

        if self._should_drop_packet():
            return False

        self._apply_latency()

        target = self._nodes.get(to_node_id)
        if target and target._running:
            target.receive_message(envelope)
            return True
        return False

    def broadcast(self, sender: Node, envelope: MessageEnvelope):
        """Broadcast a message from a node to all its peers."""
        for node_id, node in self._nodes.items():
            if node_id != sender.node_id and node._running:
                if self._can_communicate(sender.node_id, node_id):
                    if not self._should_drop_packet():
                        self._apply_latency()
                        node.receive_message(envelope)

    def create_partition(self, group_a: List[str], group_b: List[str]):
        """Create a network partition between two groups of nodes."""
        with self._lock:
            self._partitions = [set(group_a), set(group_b)]
            # Also disconnect the peer references so direct gossip is blocked
            for node_id_a in group_a:
                node_a = self._nodes.get(node_id_a)
                if node_a:
                    for node_id_b in group_b:
                        node_a._peer_nodes.pop(node_id_b, None)

            for node_id_b in group_b:
                node_b = self._nodes.get(node_id_b)
                if node_b:
                    for node_id_a in group_a:
                        node_b._peer_nodes.pop(node_id_a, None)

    def heal_partition(self):
        """Heal all network partitions and reconnect nodes."""
        with self._lock:
            self._partitions = []
            # Reconnect all nodes
            self.connect_all()

    def simulate_peer_discovery(self, node: Node) -> List[PeerInfo]:
        """Simulate peer discovery: return list of known peers."""
        discovered = []
        for nid, n in self._nodes.items():
            if nid != node.node_id and self._can_communicate(node.node_id, nid):
                info = PeerInfo(
                    node_id=nid,
                    address=f"sim://{nid}",
                    public_key=n.public_key,
                )
                discovered.append(info)
        return discovered
