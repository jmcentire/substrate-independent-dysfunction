"""
Simulated network for HermesP2P testing.

Provides in-process message delivery between nodes with configurable
latency and packet loss. No real sockets required.
"""

import random
import threading
import time
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Set, Tuple

from hermes_p2p.node import Node


class SimulatedNetwork:
    """
    Simulates a network of HermesP2P nodes.
    
    Features:
    - Configurable latency (uniform random in [min_latency, max_latency])
    - Configurable packet loss rate
    - Network partitioning support
    - Synchronous delivery mode for deterministic testing
    """

    def __init__(
        self,
        min_latency: float = 0.0,
        max_latency: float = 0.0,
        packet_loss_rate: float = 0.0,
        async_delivery: bool = False,
    ):
        self.min_latency = min_latency
        self.max_latency = max_latency
        self.packet_loss_rate = packet_loss_rate
        self.async_delivery = async_delivery

        # Registered nodes
        self.nodes: Dict[str, Node] = {}
        self._lock = threading.Lock()

        # Network partitions: sets of node_ids that can communicate
        # If empty, all nodes can communicate
        self._partitions: List[Set[str]] = []

        # Message log for debugging
        self.message_log: List[Tuple[str, str, int]] = []  # (from, to, size)

        # For async delivery
        self._delivery_threads: List[threading.Thread] = []
        self._running = True

    def add_node(self, node: Node):
        """Register a node in the network."""
        with self._lock:
            self.nodes[node.node_id] = node
            node.set_send_fn(self._send)

    def remove_node(self, node_id: str):
        """Remove a node from the network."""
        with self._lock:
            self.nodes.pop(node_id, None)

    def _can_communicate(self, from_id: str, to_id: str) -> bool:
        """Check if two nodes can communicate given current partitions."""
        if not self._partitions:
            return True
        for partition in self._partitions:
            if from_id in partition and to_id in partition:
                return True
        return False

    def _send(self, from_id: str, to_id: str, data: bytes):
        """
        Send function injected into each node.
        Handles latency simulation, packet loss, and partitions.
        """
        # Check partition
        if not self._can_communicate(from_id, to_id):
            return  # Silently drop

        # Packet loss
        if random.random() < self.packet_loss_rate:
            return  # Dropped

        # Log
        self.message_log.append((from_id, to_id, len(data)))

        # Deliver
        if self.async_delivery:
            latency = random.uniform(self.min_latency, self.max_latency)
            t = threading.Thread(
                target=self._delayed_deliver,
                args=(to_id, data, from_id, latency),
                daemon=True,
            )
            t.start()
            self._delivery_threads.append(t)
        else:
            self._deliver(to_id, data, from_id)

    def _delayed_deliver(self, to_id: str, data: bytes, from_id: str, delay: float):
        """Deliver a message after a delay."""
        if delay > 0:
            time.sleep(delay)
        self._deliver(to_id, data, from_id)

    def _deliver(self, to_id: str, data: bytes, from_id: str):
        """Actually deliver data to a node."""
        with self._lock:
            node = self.nodes.get(to_id)
        if node is not None:
            node.receive_raw(data, from_node_id=from_id)

    # ─── Partition Management ────────────────────────────────────────

    def set_partitions(self, partitions: List[Set[str]]):
        """
        Set network partitions. Each set is a group of nodes that
        can communicate with each other. Nodes not in any set are isolated.
        """
        self._partitions = partitions

    def heal_partitions(self):
        """Remove all partitions — all nodes can communicate."""
        self._partitions = []

    # ─── Convenience ─────────────────────────────────────────────────

    def create_node(self, node_id: str, **kwargs) -> Node:
        """Create a node and add it to the network."""
        node = Node(node_id=node_id, **kwargs)
        self.add_node(node)
        return node

    def connect_all(self):
        """Make all nodes aware of each other."""
        with self._lock:
            node_list = list(self.nodes.values())
        for node in node_list:
            for other in node_list:
                if node.node_id != other.node_id:
                    node.add_peer(other.get_peer_info())

    def wait_for_delivery(self, timeout: float = 5.0):
        """Wait for all async deliveries to complete."""
        deadline = time.time() + timeout
        for t in self._delivery_threads:
            remaining = deadline - time.time()
            if remaining > 0:
                t.join(timeout=remaining)
        self._delivery_threads.clear()

    def broadcast(self, env, from_node_id: Optional[str] = None):
        """Have a specific node send a message to the channel."""
        with self._lock:
            node = self.nodes.get(from_node_id)
        if node:
            node.send_to_channel(env)

    def clear_logs(self):
        """Clear message delivery logs."""
        self.message_log.clear()
