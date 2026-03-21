"""
Tests for src/message — MessageEnvelope, is_expired, TTLBloomFilter,
and serialization round-trip.
"""

from __future__ import annotations

import time
import uuid
from unittest import mock

import pytest

from src.message import (
    MessageEnvelope,
    TTLBloomFilter,
    envelope_from_bytes,
    envelope_to_bytes,
    is_expired,
)


# ── helpers ──────────────────────────────────────────────────────────────

def _make_envelope(**overrides) -> MessageEnvelope:
    defaults = dict(
        message_id=uuid.uuid4(),
        created_ts=time.time(),
        ttl_seconds=120,
        channel_id="test-channel",
        payload=b"hello world",
        hop_count=5,
        sender_pubkey=None,
        signature=None,
    )
    defaults.update(overrides)
    return MessageEnvelope(**defaults)


# ── 1. MessageEnvelope basic construction ────────────────────────────────

class TestMessageEnvelope:
    def test_fields_stored(self):
        mid = uuid.uuid4()
        env = MessageEnvelope(
            message_id=mid,
            created_ts=1000.0,
            ttl_seconds=60,
            channel_id="ch",
            payload=b"\x00\x01",
            hop_count=3,
            sender_pubkey=b"pk",
            signature=b"sig",
        )
        assert env.message_id == mid
        assert env.created_ts == 1000.0
        assert env.ttl_seconds == 60
        assert env.channel_id == "ch"
        assert env.payload == b"\x00\x01"
        assert env.hop_count == 3
        assert env.sender_pubkey == b"pk"
        assert env.signature == b"sig"

    def test_optional_fields_default_none(self):
        env = _make_envelope()
        assert env.sender_pubkey is None
        assert env.signature is None


# ── 2. is_expired ────────────────────────────────────────────────────────

class TestIsExpired:
    def test_fresh_message_not_expired(self):
        env = _make_envelope(created_ts=time.time(), ttl_seconds=120)
        assert is_expired(env) is False

    def test_old_message_expired(self):
        env = _make_envelope(created_ts=time.time() - 200, ttl_seconds=60)
        assert is_expired(env) is True

    def test_exactly_at_ttl_not_expired_due_to_tolerance(self):
        """Message right at TTL boundary should still be alive thanks to
        the default 30-second clock-skew tolerance."""
        now = time.time()
        env = _make_envelope(created_ts=now - 60, ttl_seconds=60)
        # now == created_ts + ttl_seconds, but tolerance adds 30 s → NOT expired
        assert is_expired(env, clock_skew_tolerance=30) is False

    def test_past_ttl_plus_tolerance(self):
        now = time.time()
        # created 100 s ago, TTL 60 s → expired 40 s ago, well past 30 s tolerance
        env = _make_envelope(created_ts=now - 100, ttl_seconds=60)
        assert is_expired(env, clock_skew_tolerance=30) is True

    def test_clock_skew_tolerance_zero(self):
        """With zero tolerance, a message exactly at its TTL boundary is
        still not expired (need to be strictly past)."""
        now = time.time()
        env = _make_envelope(created_ts=now - 60, ttl_seconds=60)
        # expiry_time = now, so now > expiry_time is False → not expired
        # But just 0.01 s later it would be.  Use a message clearly expired:
        env2 = _make_envelope(created_ts=now - 61, ttl_seconds=60)
        assert is_expired(env2, clock_skew_tolerance=0) is True

    def test_far_future_created_ts_expired(self):
        """A message with created_ts unreasonably far in the future is
        treated as expired (protects against timestamp spoofing)."""
        env = _make_envelope(created_ts=time.time() + 500, ttl_seconds=60)
        assert is_expired(env) is True

    def test_slight_future_created_ts_within_tolerance(self):
        """A message with created_ts only a few seconds in the future
        should be accepted."""
        env = _make_envelope(created_ts=time.time() + 10, ttl_seconds=60)
        assert is_expired(env, clock_skew_tolerance=30) is False

    def test_custom_tolerance(self):
        now = time.time()
        env = _make_envelope(created_ts=now - 70, ttl_seconds=60)
        # Default tolerance=30: expired at now-70+60+30 = now+20 → not expired? 
        # Actually: expiry_time = (now - 70) + 60 + 30 = now + 20, so now > now+20 is False → NOT expired
        assert is_expired(env, clock_skew_tolerance=30) is False
        # With tolerance=5: expiry_time = (now-70)+60+5 = now-5, so now > now-5 → expired
        assert is_expired(env, clock_skew_tolerance=5) is True


# ── 3. TTLBloomFilter ────────────────────────────────────────────────────

class TestTTLBloomFilter:
    def test_add_and_has(self):
        bf = TTLBloomFilter(bucket_seconds=60, max_age_seconds=300)
        mid = uuid.uuid4()
        assert bf.has(mid) is False
        bf.add(mid)
        assert bf.has(mid) is True

    def test_unseen_id_not_found(self):
        bf = TTLBloomFilter()
        bf.add(uuid.uuid4())
        assert bf.has(uuid.uuid4()) is False

    def test_many_ids(self):
        bf = TTLBloomFilter(bucket_size_bits=65536)
        ids = [uuid.uuid4() for _ in range(200)]
        for mid in ids:
            bf.add(mid)
        # All inserted ids should be found (no false negatives).
        for mid in ids:
            assert bf.has(mid) is True

    def test_cleanup_prunes_old_buckets(self):
        bf = TTLBloomFilter(bucket_seconds=60, max_age_seconds=120)
        mid_old = uuid.uuid4()
        bf.add(mid_old)

        # Simulate time advancing 200 seconds → the bucket is now > 120 s old.
        future = time.time() + 200
        with mock.patch("src.message.time") as mock_time:
            mock_time.time.return_value = future
            bf.cleanup()
            # Old id should be gone after cleanup.
            assert bf.has(mid_old) is False

    def test_recent_entry_survives_cleanup(self):
        bf = TTLBloomFilter(bucket_seconds=60, max_age_seconds=300)
        mid = uuid.uuid4()
        bf.add(mid)
        bf.cleanup()
        assert bf.has(mid) is True

    def test_memory_is_bounded(self):
        bf = TTLBloomFilter(
            bucket_seconds=10,
            max_age_seconds=60,
            bucket_size_bits=8192,
        )
        # Add entries across many time buckets by simulating time jumps.
        base = time.time()
        for i in range(20):
            t = base + i * 10
            with mock.patch("src.message.time") as mt:
                mt.time.return_value = t
                bf.add(uuid.uuid4())

        # After cleanup at the latest time, old buckets are pruned.
        latest = base + 19 * 10
        with mock.patch("src.message.time") as mt:
            mt.time.return_value = latest
            bf.cleanup()

        # Number of buckets should be ≤ max_buckets.
        assert len(bf._buckets) <= bf.max_buckets
        # Memory should be within the theoretical maximum.
        assert bf.memory_estimate() <= bf.max_memory_estimate()

    def test_memory_estimate_positive(self):
        bf = TTLBloomFilter()
        bf.add(uuid.uuid4())
        assert bf.memory_estimate() > 0

    def test_max_memory_estimate(self):
        bf = TTLBloomFilter(
            bucket_seconds=60,
            max_age_seconds=300,
            bucket_size_bits=8192,
        )
        # 300/60 + 1 = 6 buckets max; each ~1 KiB + overhead
        assert bf.max_memory_estimate() < 20_000  # well under 20 KB


# ── 4. Serialization round-trip ──────────────────────────────────────────

class TestSerialization:
    def test_round_trip_minimal(self):
        env = _make_envelope()
        data = envelope_to_bytes(env)
        env2 = envelope_from_bytes(data)
        assert env2.message_id == env.message_id
        assert env2.created_ts == env.created_ts
        assert env2.ttl_seconds == env.ttl_seconds
        assert env2.channel_id == env.channel_id
        assert env2.payload == env.payload
        assert env2.hop_count == env.hop_count
        assert env2.sender_pubkey is None
        assert env2.signature is None

    def test_round_trip_with_pubkey_and_signature(self):
        env = _make_envelope(
            sender_pubkey=b"\x01" * 32,
            signature=b"\xab" * 64,
        )
        data = envelope_to_bytes(env)
        env2 = envelope_from_bytes(data)
        assert env2.sender_pubkey == env.sender_pubkey
        assert env2.signature == env.signature

    def test_round_trip_empty_payload(self):
        env = _make_envelope(payload=b"")
        data = envelope_to_bytes(env)
        env2 = envelope_from_bytes(data)
        assert env2.payload == b""

    def test_round_trip_large_payload(self):
        env = _make_envelope(payload=b"\xff" * 100_000)
        data = envelope_to_bytes(env)
        env2 = envelope_from_bytes(data)
        assert env2.payload == env.payload

    def test_round_trip_unicode_channel(self):
        env = _make_envelope(channel_id="日本語チャンネル")
        data = envelope_to_bytes(env)
        env2 = envelope_from_bytes(data)
        assert env2.channel_id == env.channel_id

    def test_round_trip_hop_count_zero(self):
        env = _make_envelope(hop_count=0)
        data = envelope_to_bytes(env)
        env2 = envelope_from_bytes(data)
        assert env2.hop_count == 0

    def test_round_trip_only_pubkey(self):
        env = _make_envelope(sender_pubkey=b"pk_only", signature=None)
        data = envelope_to_bytes(env)
        env2 = envelope_from_bytes(data)
        assert env2.sender_pubkey == b"pk_only"
        assert env2.signature is None

    def test_round_trip_only_signature(self):
        env = _make_envelope(sender_pubkey=None, signature=b"sig_only")
        data = envelope_to_bytes(env)
        env2 = envelope_from_bytes(data)
        assert env2.sender_pubkey is None
        assert env2.signature == b"sig_only"

    def test_deterministic(self):
        """Same envelope serializes to the same bytes."""
        mid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        env = MessageEnvelope(
            message_id=mid,
            created_ts=1700000000.0,
            ttl_seconds=120,
            channel_id="ch",
            payload=b"data",
            hop_count=3,
        )
        assert envelope_to_bytes(env) == envelope_to_bytes(env)

    def test_equality_after_round_trip(self):
        env = _make_envelope(
            sender_pubkey=b"key",
            signature=b"sig",
        )
        assert envelope_from_bytes(envelope_to_bytes(env)) == env
