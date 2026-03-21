"""Core data models for HermesP2P."""

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class NodeIdentity:
    """Identity of a node in the network."""
    node_id: str
    public_key: bytes  # X25519 or Ed25519 public key bytes
    display_name: str = ""

    def __hash__(self):
        return hash(self.node_id)

    def __eq__(self, other):
        if not isinstance(other, NodeIdentity):
            return False
        return self.node_id == other.node_id


@dataclass
class PeerInfo:
    """Information about a known peer."""
    node_id: str
    address: str  # e.g., "host:port" or simulated address
    public_key: bytes = b""
    last_seen: float = field(default_factory=time.time)
    latency_ms: float = 0.0

    def __hash__(self):
        return hash(self.node_id)

    def __eq__(self, other):
        if not isinstance(other, PeerInfo):
            return False
        return self.node_id == other.node_id


@dataclass
class MessageEnvelope:
    """A message envelope for the gossip protocol."""
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    channel: str = ""  # Channel name / topic
    payload: bytes = b""  # The actual message content (possibly encrypted)
    sender_id: str = ""  # Node ID of the original sender
    timestamp: float = field(default_factory=time.time)
    ttl: int = 10  # Max number of hops remaining
    hop_count: int = 0  # Number of hops so far
    signature: bytes = b""  # Ed25519 signature of the payload
    sender_public_key: bytes = b""  # Public key to verify signature
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        """SHA-256 hash of the message content for deduplication."""
        h = hashlib.sha256()
        h.update(self.message_id.encode())
        h.update(self.payload)
        return h.hexdigest()

    def is_expired(self, max_skew: float = 30.0) -> bool:
        """Check if the message has expired based on TTL and time skew."""
        now = time.time()
        age = abs(now - self.timestamp)
        if age > max_skew:
            return True
        if self.ttl <= 0:
            return True
        return False

    def decrement_ttl(self) -> 'MessageEnvelope':
        """Return a copy with decremented TTL and incremented hop count."""
        return MessageEnvelope(
            message_id=self.message_id,
            channel=self.channel,
            payload=self.payload,
            sender_id=self.sender_id,
            timestamp=self.timestamp,
            ttl=self.ttl - 1,
            hop_count=self.hop_count + 1,
            signature=self.signature,
            sender_public_key=self.sender_public_key,
            metadata=dict(self.metadata),
        )
