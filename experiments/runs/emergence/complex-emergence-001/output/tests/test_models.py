"""Tests for hermes_p2p.models."""

import time
import pytest
from hermes_p2p.models import MessageEnvelope, NodeIdentity, PeerInfo


class TestMessageEnvelope:
    def test_create_envelope(self):
        env = MessageEnvelope(
            channel="test",
            payload=b"hello",
            sender_id="node1",
        )
        assert env.channel == "test"
        assert env.payload == b"hello"
        assert env.sender_id == "node1"
        assert env.ttl == 10
        assert env.hop_count == 0
        assert env.message_id  # should be auto-generated

    def test_content_hash(self):
        env = MessageEnvelope(payload=b"test")
        h = env.content_hash
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex

    def test_content_hash_deterministic(self):
        env = MessageEnvelope(message_id="fixed", payload=b"test")
        assert env.content_hash == env.content_hash

    def test_is_expired_by_ttl(self):
        env = MessageEnvelope(ttl=0, timestamp=time.time())
        assert env.is_expired() is True

    def test_is_expired_by_time(self):
        env = MessageEnvelope(ttl=10, timestamp=time.time() - 60)
        assert env.is_expired(max_skew=30.0) is True

    def test_not_expired(self):
        env = MessageEnvelope(ttl=10, timestamp=time.time())
        assert env.is_expired() is False

    def test_decrement_ttl(self):
        env = MessageEnvelope(ttl=5, hop_count=2)
        new_env = env.decrement_ttl()
        assert new_env.ttl == 4
        assert new_env.hop_count == 3
        assert env.ttl == 5  # original unchanged


class TestNodeIdentity:
    def test_create(self):
        ni = NodeIdentity(node_id="n1", public_key=b"key")
        assert ni.node_id == "n1"

    def test_equality(self):
        a = NodeIdentity(node_id="n1", public_key=b"k1")
        b = NodeIdentity(node_id="n1", public_key=b"k2")
        assert a == b  # Same node_id

    def test_hash(self):
        a = NodeIdentity(node_id="n1", public_key=b"k1")
        b = NodeIdentity(node_id="n1", public_key=b"k2")
        assert hash(a) == hash(b)


class TestPeerInfo:
    def test_create(self):
        pi = PeerInfo(node_id="n1", address="localhost:8000")
        assert pi.node_id == "n1"
        assert pi.address == "localhost:8000"

    def test_equality(self):
        a = PeerInfo(node_id="n1", address="a:1")
        b = PeerInfo(node_id="n1", address="b:2")
        assert a == b
