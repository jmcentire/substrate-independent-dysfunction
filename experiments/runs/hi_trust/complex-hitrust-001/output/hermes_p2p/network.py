"""
Simulated network for HermesP2P testing.

Provides in-process message delivery between nodes with configurable
latency and packet loss. No real sockets required.
"""

import time
import random
import threading
from typing import Dict, Optional, List, Tuple, Set
from collections import defaultdict

from hermes_p2p.node import Node


class SimulatedNetwork:
    """
    A simulated network that connects Node instances.

    Features:
    - Configurable latency (uniform random in [min_latency, max_latency])
    - Configurable packet loss rate
    - Network partitioning support
    - Message delivery is synchronous (immediate for simplicity in testing)
    """

    def __init__(
        self,
        min_latency: float = 0.0,
        max_latency: float = 0.0,
        packet_loss_rate: float = 0.0,
    ):
        self.min_latency = min_latency
        self.max_latency = max_latency
        self.packet_loss_rate = packet_loss_rate

        # Registered nodes: address -> Node
        self.nodes: Dict[str, Node] = {}
        self._lock = threading.Lock()

        # Network partitions: set of (addr1, addr2) pairs that can communicate
        # If None, all nodes can communicate (no partition)
        self._partitions: Optional[List[Set[str]]] = None

        # Message log for debugging
        self.message_log: List[Tuple[str, str, dict]] = []

        # Delivery queue for async delivery
        self._pending: List[Tuple[float, str, str, dict]] = []

    def register_node(self, node: Node):
        """Register a node with the network."""
        with self._lock:
            self.nodes[node.address] = node
            node.set_send_function(self._make_send_fn(node.address))

    def unregister_node(self, address: str):
        """Remove a node from the network."""
        with self._lock:
            self.nodes.pop(address, None)

    def _make_send_fn(self, sender_address: str):
        """Create a send function for a specific node."""
        def send(target_address: str, data: dict):
            self._deliver(sender_address, target_address, data)
        return send

    def _can_communicate(self, addr1: str, addr2: str) -> bool:
        """Check if two nodes can communicate (no partition between them)."""
        if self._partitions is None:
            return True
        # Both nodes must be in the same partition group
        for group in self._partitions:
            if addr1 in group and addr2 in group:
                return True
        return False

    def _deliver(self, from_address: str, to_address: str, data: dict):
        """Deliver a message from one node to another."""
        # Check partition
        if not self._can_communicate(from_address, to_address):
            return  # Silently drop

        # Check packet loss
        if self.packet_loss_rate > 0 and random.random() < self.packet_loss_rate:
            return  # Dropped

        # Apply latency
        if self.max_latency > 0:
            delay = random.uniform(self.min_latency, self.max_latency)
            time.sleep(delay)

        # Log
        self.message_log.append((from_address, to_address, data))

        # Deliver
        with self._lock:
            target = self.nodes.get(to_address)

        if target:
            target.handle_network_message(data, from_address)

    def partition(self, groups: List[List[str]]):
        """
        Create a network partition.

        Args:
            groups: List of groups, where each group is a list of addresses
                   that can communicate with each other.
        """
        self._partitions = [set(g) for g in groups]

    def heal_partition(self):
        """Remove all network partitions."""
        self._partitions = None

    def get_all_addresses(self) -> List[str]:
        """Get all registered node addresses."""
        with self._lock:
            return list(self.nodes.keys())

    def setup_full_mesh(self):
        """Make all nodes aware of each other."""
        with self._lock:
            node_list = list(self.nodes.values())

        for i, node_a in enumerate(node_list):
            for j, node_b in enumerate(node_list):
                if i != j:
                    node_a.add_peer(
                        node_b.address,
                        node_b.identity.encryption_public_key,
                        node_b.identity.verify_key,
                    )
