"""Tests for hermes_p2p.store."""

import time
import pytest
from hermes_p2p.models import MessageEnvelope
from hermes_p2p.store import MessageStore


class TestMessageStore:
    def test_store_and_retrieve(self):
        store = MessageStore()
        env = MessageEnvelope(channel="test", payload=b"hello")
        assert store.store(env) is True
        assert store.get(env.message_id) is env

    def test_deduplication(self):
        """Test (c): Duplicate messages are deduplicated."""
        store = MessageStore()
        env = MessageEnvelope(message_id="dup1", payload=b"hello")
        assert store.store(env) is True
        assert store.store(env) is False  # Duplicate
        assert store.count == 1

    def test_has_seen(self):
        store = MessageStore()
        env = MessageEnvelope(message_id="msg1", payload=b"data")
        assert store.has_seen("msg1") is False
        store.store(env)
        assert store.has_seen("msg1") is True

    def test_channel_messages(self):
        store = MessageStore()
        store.store(MessageEnvelope(channel="ch1", payload=b"a"))
        store.store(MessageEnvelope(channel="ch2", payload=b"b"))
        store.store(MessageEnvelope(channel="ch1", payload=b"c"))
        ch1_msgs = store.get_channel_messages("ch1")
        assert len(ch1_msgs) == 2

    def test_eviction(self):
        store = MessageStore(max_messages=5)
        for i in range(10):
            store.store(MessageEnvelope(
                message_id=f"msg_{i}",
                payload=f"data_{i}".encode(),
                timestamp=time.time() + i * 0.001,
            ))
        assert store.count <= 5

    def test_cleanup_expired(self):
        store = MessageStore(ttl_seconds=0.5)
        store.store(MessageEnvelope(payload=b"old", timestamp=time.time() - 1.0))
        store.store(MessageEnvelope(payload=b"new", timestamp=time.time()))
        store.cleanup_expired()
        assert store.count == 1
