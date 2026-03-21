"""Channel implementations: PublicChannel, PrivateChannel, DirectMessage."""

import json
import time
from typing import Callable, List, Optional

from hermes_p2p.models import MessageEnvelope
from hermes_p2p.crypto import (
    sign_message, verify_signature,
    aes_gcm_encrypt, aes_gcm_decrypt,
)
from hermes_p2p.node import Node


class PublicChannel:
    """A public channel where messages are signed but not encrypted.

    Anyone can read messages, but signatures verify the sender.
    """

    def __init__(self, name: str):
        self.name = name
        self._message_log: List[MessageEnvelope] = []

    def publish(self, node: Node, payload: bytes, ttl: int = 10) -> MessageEnvelope:
        """Publish a signed message to this channel."""
        envelope = node.publish(self.name, payload, ttl=ttl)
        self._message_log.append(envelope)
        return envelope

    @staticmethod
    def verify(envelope: MessageEnvelope) -> bool:
        """Verify the signature on a public channel message."""
        if not envelope.sender_public_key or not envelope.signature:
            return False
        return verify_signature(
            envelope.sender_public_key,
            envelope.payload,
            envelope.signature,
        )

    def subscribe(self, node: Node, callback: Callable[[MessageEnvelope], None]):
        """Subscribe a node to this channel."""
        node.subscribe(self.name, callback)


class PrivateChannel:
    """A private channel with symmetric AES-GCM encryption.

    All members share a channel key. Messages are encrypted so
    non-members cannot read them.
    """

    def __init__(self, name: str, channel_key: bytes):
        self.name = name
        self._channel_key = channel_key  # 32-byte symmetric key

    def publish(self, node: Node, plaintext: bytes, ttl: int = 10) -> MessageEnvelope:
        """Encrypt and publish a message to this private channel."""
        encrypted = aes_gcm_encrypt(self._channel_key, plaintext)
        envelope = node.publish(self.name, encrypted, ttl=ttl)
        return envelope

    def decrypt(self, envelope: MessageEnvelope) -> bytes:
        """Decrypt a private channel message."""
        return aes_gcm_decrypt(self._channel_key, envelope.payload)

    @staticmethod
    def try_decrypt(channel_key: bytes, envelope: MessageEnvelope) -> Optional[bytes]:
        """Try to decrypt with a given key. Returns None on failure."""
        try:
            return aes_gcm_decrypt(channel_key, envelope.payload)
        except Exception:
            return None

    def subscribe(self, node: Node, callback: Callable[[bytes], None]):
        """Subscribe with automatic decryption."""
        def _handler(envelope: MessageEnvelope):
            try:
                plaintext = self.decrypt(envelope)
                callback(plaintext)
            except Exception:
                pass  # Can't decrypt - possibly wrong key
        node.subscribe(self.name, _handler)


class DirectMessage:
    """Direct messaging using onion routing.

    Messages are onion-encrypted through a path of relay nodes
    so intermediate relays cannot read the payload.
    """

    def __init__(self):
        pass

    @staticmethod
    def send(
        sender: Node,
        recipient: Node,
        payload: bytes,
        relay_nodes: Optional[List[Node]] = None,
        ttl: int = 10,
    ) -> MessageEnvelope:
        """Send a direct message via onion routing.

        If relay_nodes are provided, the message is onion-wrapped through them.
        Otherwise it's sent directly (still encrypted to recipient).
        """
        from hermes_p2p.onion import build_onion

        if relay_nodes is None:
            relay_nodes = []

        # Build the path: relays + recipient
        path_nodes = relay_nodes + [recipient]
        path_public_keys = [n.public_key for n in path_nodes]
        path_node_ids = [n.node_id for n in path_nodes]

        # Build onion-encrypted payload
        from hermes_p2p.onion import build_onion_for_path
        onion_payload = build_onion_for_path(payload, path_nodes)

        # Create envelope and inject into first hop
        envelope = MessageEnvelope(
            channel="__direct__",
            payload=onion_payload,
            sender_id=sender.node_id,
            timestamp=time.time(),
            ttl=ttl,
            metadata={"onion": True, "next_hop": path_node_ids[0] if path_node_ids else ""},
        )

        # Deliver to first node in path
        if path_nodes:
            path_nodes[0].receive_message(envelope)

        return envelope
