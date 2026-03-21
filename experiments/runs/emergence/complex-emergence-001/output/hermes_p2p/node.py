"""Node implementation with gossip forwarding, TTL enforcement, and deduplication."""

import random
import time
import threading
import uuid
from typing import Callable, Dict, List, Optional, Set

from hermes_p2p.models import MessageEnvelope, NodeIdentity, PeerInfo
from hermes_p2p.bloom import TTLBloomFilter
from hermes_p2p.store import MessageStore
from hermes_p2p.crypto import generate_keypair, sign_message


class Node:
    """A node in the HermesP2P network.

    Supports:
    - Gossip forwarding with configurable fanout (default k=3)
    - TTL enforcement (±30s time skew tolerance)
    - Message deduplication via bloom filter
    - Channel subscription
    """

    DEFAULT_FANOUT = 3
    DEFAULT_MAX_SKEW = 30.0

    def __init__(
        self,
        node_id: Optional[str] = None,
        display_name: str = "",
        fanout: int = DEFAULT_FANOUT,
        max_skew: float = DEFAULT_MAX_SKEW,
    ):
        self.node_id = node_id or str(uuid.uuid4())
        self.display_name = display_name or self.node_id[:8]
        self.fanout = fanout
        self.max_skew = max_skew

        # Generate signing keypair
        self._private_key, self._public_key = generate_keypair()

        self.identity = NodeIdentity(
            node_id=self.node_id,
            public_key=self._public_key,
            display_name=self.display_name,
        )

        # Peer management
        self._peers: Dict[str, PeerInfo] = {}
        self._peer_nodes: Dict[str, 'Node'] = {}  # For simulation: direct references

        # Message handling
        self._bloom = TTLBloomFilter(capacity=50000, ttl_seconds=120.0)
        self._store = MessageStore()

        # Channel subscriptions: channel_name -> list of callbacks
        self._subscriptions: Dict[str, List[Callable[[MessageEnvelope], None]]] = {}

        # All received messages (for testing)
        self.received_messages: List[MessageEnvelope] = []

        # Network reference (set by Network.add_node)
        self._network = None

        self._running = True

    @property
    def public_key(self) -> bytes:
        return self._public_key

    @property
    def private_key(self) -> bytes:
        return self._private_key

    @property
    def peers(self) -> Dict[str, PeerInfo]:
        return dict(self._peers)

    @property
    def store(self) -> MessageStore:
        return self._store

    def add_peer(self, peer_info: PeerInfo, peer_node: Optional['Node'] = None):
        """Add a peer to this node's peer list."""
        if peer_info.node_id == self.node_id:
            return  # Don't add self
        self._peers[peer_info.node_id] = peer_info
        if peer_node is not None:
            self._peer_nodes[peer_info.node_id] = peer_node

    def remove_peer(self, node_id: str):
        """Remove a peer."""
        self._peers.pop(node_id, None)
        self._peer_nodes.pop(node_id, None)

    def subscribe(self, channel: str, callback: Callable[[MessageEnvelope], None]):
        """Subscribe to a channel."""
        if channel not in self._subscriptions:
            self._subscriptions[channel] = []
        self._subscriptions[channel].append(callback)

    def unsubscribe(self, channel: str):
        """Unsubscribe from a channel."""
        self._subscriptions.pop(channel, None)

    def is_subscribed(self, channel: str) -> bool:
        return channel in self._subscriptions

    def publish(self, channel: str, payload: bytes, ttl: int = 10) -> MessageEnvelope:
        """Create and send a message on a channel."""
        signature = sign_message(self._private_key, payload)
        envelope = MessageEnvelope(
            channel=channel,
            payload=payload,
            sender_id=self.node_id,
            timestamp=time.time(),
            ttl=ttl,
            hop_count=0,
            signature=signature,
            sender_public_key=self._public_key,
        )
        # Process locally first
        self._process_message(envelope)
        return envelope

    def receive_message(self, envelope: MessageEnvelope):
        """Receive a message from the network (called by Network or peer)."""
        self._process_message(envelope)

    def _process_message(self, envelope: MessageEnvelope):
        """Process an incoming message: dedup, TTL check, deliver, forward."""
        # Check deduplication via bloom filter
        msg_hash = envelope.content_hash
        if self._bloom.contains(msg_hash):
            return  # Duplicate

        # Check TTL
        if envelope.ttl <= 0:
            return  # Expired (hop limit)

        # Check time skew
        now = time.time()
        age = abs(now - envelope.timestamp)
        if age > self.max_skew:
            return  # Too old or too far in future

        # Mark as seen
        self._bloom.add(msg_hash)
        self._store.store(envelope)
        self.received_messages.append(envelope)

        # Deliver to local subscribers
        self._deliver_locally(envelope)

        # Forward to peers (gossip)
        self._gossip_forward(envelope)

    def _deliver_locally(self, envelope: MessageEnvelope):
        """Deliver message to local channel subscribers."""
        callbacks = self._subscriptions.get(envelope.channel, [])
        for callback in callbacks:
            try:
                callback(envelope)
            except Exception:
                pass  # Don't let subscriber errors break the node

    def _gossip_forward(self, envelope: MessageEnvelope):
        """Forward message to a random subset of peers (fanout)."""
        forwarded = envelope.decrement_ttl()
        if forwarded.ttl <= 0:
            return  # Don't forward if TTL exhausted

        # Select random peers for gossip
        available_peers = [
            pid for pid in self._peer_nodes.keys()
            if pid != envelope.sender_id
        ]
        if not available_peers:
            # If using network simulation, forward through network
            if self._network is not None:
                self._network._gossip_from(self, forwarded)
            return

        selected = random.sample(
            available_peers,
            min(self.fanout, len(available_peers))
        )

        for peer_id in selected:
            peer_node = self._peer_nodes.get(peer_id)
            if peer_node and peer_node._running:
                peer_node.receive_message(forwarded)

    def bootstrap(self, bootstrap_node: 'Node'):
        """Bootstrap this node by connecting to an existing node."""
        # Add bootstrap node as peer
        peer_info = PeerInfo(
            node_id=bootstrap_node.node_id,
            address=f"sim://{bootstrap_node.node_id}",
            public_key=bootstrap_node.public_key,
        )
        self.add_peer(peer_info, bootstrap_node)

        # Add self to bootstrap node's peers
        my_info = PeerInfo(
            node_id=self.node_id,
            address=f"sim://{self.node_id}",
            public_key=self._public_key,
        )
        bootstrap_node.add_peer(my_info, self)

        # Get bootstrap node's peers
        for pid, pinfo in bootstrap_node.peers.items():
            if pid != self.node_id:
                peer_node = bootstrap_node._peer_nodes.get(pid)
                self.add_peer(pinfo, peer_node)
                # Tell the other peer about us
                if peer_node:
                    peer_node.add_peer(my_info, self)

    def stop(self):
        """Stop this node."""
        self._running = False

    def __repr__(self):
        return f"Node({self.display_name}, peers={len(self._peers)})"
