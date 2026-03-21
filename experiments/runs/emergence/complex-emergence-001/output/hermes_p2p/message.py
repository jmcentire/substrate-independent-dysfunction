"""Message envelope and types for HermesP2P."""

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Message:
    """Core message content before envelope wrapping."""
    channel_id: str
    payload: bytes
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_ts: float = field(default_factory=time.time)
    ttl_seconds: float = 300.0

    def is_expired(self, clock_skew_tolerance: float = 30.0) -> bool:
        """Check if the message has expired."""
        now = time.time()
        return now > (self.created_ts + self.ttl_seconds + clock_skew_tolerance)

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "created_ts": self.created_ts,
            "ttl_seconds": self.ttl_seconds,
            "channel_id": self.channel_id,
            "payload": self.payload.hex(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(
            message_id=d["message_id"],
            created_ts=d["created_ts"],
            ttl_seconds=d["ttl_seconds"],
            channel_id=d["channel_id"],
            payload=bytes.fromhex(d["payload"]),
        )

    def serialize(self) -> bytes:
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def deserialize(cls, data: bytes) -> "Message":
        return cls.from_dict(json.loads(data.decode("utf-8")))


@dataclass
class MessageEnvelope:
    """Envelope wrapping a message for gossip propagation."""
    message: Message
    hops_remaining: int = 6
    sender_id: Optional[str] = None  # node_id of immediate sender (not origin)

    def to_dict(self) -> dict:
        return {
            "message": self.message.to_dict(),
            "hops_remaining": self.hops_remaining,
            "sender_id": self.sender_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MessageEnvelope":
        return cls(
            message=Message.from_dict(d["message"]),
            hops_remaining=d["hops_remaining"],
            sender_id=d.get("sender_id"),
        )

    def serialize(self) -> bytes:
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def deserialize(cls, data: bytes) -> "MessageEnvelope":
        return cls.from_dict(json.loads(data.decode("utf-8")))
