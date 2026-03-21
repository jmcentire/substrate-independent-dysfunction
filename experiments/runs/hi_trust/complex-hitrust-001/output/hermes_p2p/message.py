"""
Message envelope and channel types for HermesP2P.
"""

import os
import time
import json
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class ChannelType(Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    DIRECT = "direct"


@dataclass
class Message:
    """
    The message envelope that propagates through the gossip network.

    Fields:
        message_id: Random unique identifier for deduplication (hex string).
        created_ts: Unix timestamp when the message was created.
        ttl_seconds: How long the message should live.
        channel_id: Identifier for the channel (or "direct" for DMs).
        channel_type: Type of channel.
        payload: The (possibly onion-encrypted) message data.
        hops_remaining: Gossip hop counter; decremented at each relay.
        sender_pubkey_hex: For public channels, the sender's verify key (hex).
    """
    message_id: str = ""
    created_ts: float = 0.0
    ttl_seconds: float = 60.0
    channel_id: str = ""
    channel_type: ChannelType = ChannelType.PUBLIC
    payload: bytes = b""
    hops_remaining: int = 10
    sender_pubkey_hex: str = ""

    def __post_init__(self):
        if not self.message_id:
            self.message_id = os.urandom(16).hex()
        if self.created_ts == 0.0:
            self.created_ts = time.time()

    def is_expired(self, clock_skew_tolerance: float = 30.0) -> bool:
        """Check if the message has expired, with clock skew tolerance."""
        now = time.time()
        expiry_time = self.created_ts + self.ttl_seconds + clock_skew_tolerance
        return now > expiry_time

    def to_dict(self) -> dict:
        """Serialize to dict for network transport."""
        return {
            "message_id": self.message_id,
            "created_ts": self.created_ts,
            "ttl_seconds": self.ttl_seconds,
            "channel_id": self.channel_id,
            "channel_type": self.channel_type.value,
            "payload": self.payload.hex(),
            "hops_remaining": self.hops_remaining,
            "sender_pubkey_hex": self.sender_pubkey_hex,
        }

    @staticmethod
    def from_dict(d: dict) -> "Message":
        """Deserialize from dict."""
        return Message(
            message_id=d["message_id"],
            created_ts=d["created_ts"],
            ttl_seconds=d["ttl_seconds"],
            channel_id=d["channel_id"],
            channel_type=ChannelType(d["channel_type"]),
            payload=bytes.fromhex(d["payload"]),
            hops_remaining=d["hops_remaining"],
            sender_pubkey_hex=d.get("sender_pubkey_hex", ""),
        )

    def copy(self) -> "Message":
        """Create a copy of this message."""
        return Message(
            message_id=self.message_id,
            created_ts=self.created_ts,
            ttl_seconds=self.ttl_seconds,
            channel_id=self.channel_id,
            channel_type=self.channel_type,
            payload=self.payload,
            hops_remaining=self.hops_remaining,
            sender_pubkey_hex=self.sender_pubkey_hex,
        )


@dataclass
class PeerAnnouncement:
    """Announcement of a peer's identity and address."""
    public_key_hex: str
    address: str
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_dict(self) -> dict:
        return {
            "public_key_hex": self.public_key_hex,
            "address": self.address,
            "timestamp": self.timestamp,
        }

    @staticmethod
    def from_dict(d: dict) -> "PeerAnnouncement":
        return PeerAnnouncement(
            public_key_hex=d["public_key_hex"],
            address=d["address"],
            timestamp=d.get("timestamp", 0.0),
        )
