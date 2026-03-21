"""Tests for channel implementations."""

import os
import pytest
from hermes_p2p.node import Node
from hermes_p2p.channels import PublicChannel, PrivateChannel, DirectMessage
from hermes_p2p.crypto import verify_signature, aes_gcm_decrypt


class TestPublicChannel:
    def test_publish_and_verify(self):
        """Test (f): Public channel messages verified by signature."""
        node = Node(node_id="publisher")
        channel = PublicChannel("announcements")

        received = []
        channel.subscribe(node, lambda env: received.append(env))
        envelope = channel.publish(node, b"Hello everyone!")

        # Verify signature
        assert PublicChannel.verify(envelope) is True

    def test_verify_fails_with_tampered_payload(self):
        """Signature verification should fail if payload is tampered."""
        from hermes_p2p.crypto import HAS_CRYPTOGRAPHY
        node = Node(node_id="pub")
        channel = PublicChannel("ch")
        envelope = channel.publish(node, b"original")

        # Tamper with payload
        envelope.payload = b"tampered"
        if HAS_CRYPTOGRAPHY:
            assert PublicChannel.verify(envelope) is False

    def test_verify_fails_without_signature(self):
        from hermes_p2p.models import MessageEnvelope
        env = MessageEnvelope(payload=b"no sig")
        assert PublicChannel.verify(env) is False


class TestPrivateChannel:
    def test_encrypt_and_decrypt(self):
        """Test (g): Private channel messages unreadable without channel key."""
        key = os.urandom(32)
        node = Node(node_id="member")
        channel = PrivateChannel("secret", key)

        envelope = channel.publish(node, b"secret message")

        # Decrypt with correct key
        plaintext = channel.decrypt(envelope)
        assert plaintext == b"secret message"

    def test_cannot_decrypt_without_key(self):
        """Test (g): Messages unreadable without channel key."""
        key = os.urandom(32)
        wrong_key = os.urandom(32)
        node = Node(node_id="member")
        channel = PrivateChannel("secret", key)

        envelope = channel.publish(node, b"confidential")

        # Try to decrypt with wrong key
        result = PrivateChannel.try_decrypt(wrong_key, envelope)
        assert result is None, "Should not be able to decrypt with wrong key"

    def test_subscribe_with_decryption(self):
        key = os.urandom(32)
        node = Node(node_id="sub")
        channel = PrivateChannel("private_ch", key)

        decrypted_messages = []
        channel.subscribe(node, lambda pt: decrypted_messages.append(pt))

        channel.publish(node, b"auto-decrypted")

        assert len(decrypted_messages) == 1
        assert decrypted_messages[0] == b"auto-decrypted"
