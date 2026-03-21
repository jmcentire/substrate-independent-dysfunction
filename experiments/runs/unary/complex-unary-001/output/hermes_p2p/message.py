"""
Message envelope and channel types for HermesP2P.
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from hermes_p2p.crypto import KeyManager, symmetric_encrypt, symmetric_decrypt


class ChannelType(Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    DIRECT = "direct"


@dataclass
class MessageEnvelope:
    """
    Core message envelope for gossip propagation.
    
    Fields:
        message_id: Random unique identifier for deduplication.
        created_ts: Unix timestamp when the message was created.
        ttl_seconds: Time-to-live in seconds from created_ts.
        channel_id: Identifier for the channel.
        payload: The (possibly encrypted) message content.
        hop_count: Remaining hops for gossip propagation.
        channel_type: Type of the channel (public/private/direct).
        sender_pubkey: Sender's Ed25519 public key bytes (for public channels).
        signature: Ed25519 signature of the payload (for public channels).
    """
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_ts: float = field(default_factory=time.time)
    ttl_seconds: float = 60.0
    channel_id: str = ""
    payload: bytes = b""
    hop_count: int = 6
    channel_type: ChannelType = ChannelType.PUBLIC
    sender_pubkey: Optional[bytes] = None
    signature: Optional[bytes] = None

    def is_expired(self, clock_skew_tolerance: float = 30.0) -> bool:
        """Check if this message has expired considering clock skew."""
        now = time.time()
        expiry = self.created_ts + self.ttl_seconds + clock_skew_tolerance
        return now > expiry

    def serialize(self) -> bytes:
        """Serialize envelope to bytes for network transmission."""
        data = {
            "message_id": self.message_id,
            "created_ts": self.created_ts,
            "ttl_seconds": self.ttl_seconds,
            "channel_id": self.channel_id,
            "payload": self.payload.hex(),
            "hop_count": self.hop_count,
            "channel_type": self.channel_type.value,
            "sender_pubkey": self.sender_pubkey.hex() if self.sender_pubkey else None,
            "signature": self.signature.hex() if self.signature else None,
        }
        return json.dumps(data).encode("utf-8")

    @classmethod
    def deserialize(cls, raw: bytes) -> "MessageEnvelope":
        """Deserialize from bytes."""
        data = json.loads(raw.decode("utf-8"))
        return cls(
            message_id=data["message_id"],
            created_ts=data["created_ts"],
            ttl_seconds=data["ttl_seconds"],
            channel_id=data["channel_id"],
            payload=bytes.fromhex(data["payload"]),
            hop_count=data["hop_count"],
            channel_type=ChannelType(data["channel_type"]),
            sender_pubkey=bytes.fromhex(data["sender_pubkey"]) if data.get("sender_pubkey") else None,
            signature=bytes.fromhex(data["signature"]) if data.get("signature") else None,
        )

    def signable_data(self) -> bytes:
        """Data that gets signed for public channel messages."""
        return self.channel_id.encode("utf-8") + self.payload


def create_public_message(
    channel_id: str,
    plaintext: str,
    key_manager: KeyManager,
    ttl_seconds: float = 60.0,
    hop_count: int = 6,
) -> MessageEnvelope:
    """Create a signed public channel message."""
    payload = plaintext.encode("utf-8")
    env = MessageEnvelope(
        channel_id=channel_id,
        payload=payload,
        ttl_seconds=ttl_seconds,
        hop_count=hop_count,
        channel_type=ChannelType.PUBLIC,
        sender_pubkey=key_manager.public_key_bytes(),
    )
    env.signature = key_manager.sign(env.signable_data())
    return env


def create_private_message(
    channel_id: str,
    plaintext: str,
    channel_key: bytes,
    ttl_seconds: float = 60.0,
    hop_count: int = 6,
) -> MessageEnvelope:
    """Create a private channel message encrypted with the channel's symmetric key."""
    encrypted = symmetric_encrypt(channel_key, plaintext.encode("utf-8"))
    return MessageEnvelope(
        channel_id=channel_id,
        payload=encrypted,
        ttl_seconds=ttl_seconds,
        hop_count=hop_count,
        channel_type=ChannelType.PRIVATE,
    )


def create_direct_message(
    payload: bytes,
    ttl_seconds: float = 60.0,
    hop_count: int = 6,
) -> MessageEnvelope:
    """
    Create a direct message envelope.
    The payload should already be onion-encrypted.
    """
    return MessageEnvelope(
        channel_id="__direct__",
        payload=payload,
        ttl_seconds=ttl_seconds,
        hop_count=hop_count,
        channel_type=ChannelType.DIRECT,
    )


def verify_public_message(env: MessageEnvelope) -> bool:
    """Verify signature on a public channel message."""
    if env.channel_type != ChannelType.PUBLIC:
        return False
    if env.sender_pubkey is None or env.signature is None:
        return False
    return KeyManager.verify(env.sender_pubkey, env.signature, env.signable_data())


def decrypt_private_message(env: MessageEnvelope, channel_key: bytes) -> Optional[str]:
    """Decrypt a private channel message. Returns None on failure."""
    try:
        plaintext = symmetric_decrypt(channel_key, env.payload)
        return plaintext.decode("utf-8")
    except Exception:
        return None
