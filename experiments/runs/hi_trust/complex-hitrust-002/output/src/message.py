"""
HermesP2P Message Data Structures and Deduplication
=====================================================

This module provides:

1. **MessageEnvelope** — a dataclass representing the core message unit in
   the gossip network.  Every message carries a unique ``message_id``,
   creation timestamp, TTL, channel identifier, an opaque (possibly
   onion-encrypted) payload, and a hop counter used to limit propagation.

2. **is_expired(envelope, clock_skew_tolerance)** — checks whether a
   message has outlived its TTL (with configurable tolerance for clock skew
   between nodes).

3. **TTLBloomFilter** — a time-bucketed probabilistic set used for
   bounded-memory deduplication of ``message_id`` values.  Old buckets are
   pruned automatically so memory stays bounded.

4. **Serialization helpers** — ``envelope_to_bytes`` / ``envelope_from_bytes``
   for compact wire encoding via MessagePack.

Usage example::

    from src.message import (
        MessageEnvelope, is_expired,
        TTLBloomFilter, envelope_to_bytes, envelope_from_bytes,
    )
    import uuid, time

    env = MessageEnvelope(
        message_id=uuid.uuid4(),
        created_ts=time.time(),
        ttl_seconds=120,
        channel_id="general",
        payload=b"hello",
        hop_count=5,
    )

    data = envelope_to_bytes(env)
    env2 = envelope_from_bytes(data)
    assert env == env2

    bf = TTLBloomFilter(bucket_seconds=60, max_age_seconds=300)
    bf.add(env.message_id)
    assert bf.has(env.message_id)
"""

from __future__ import annotations

import hashlib
import math
import struct
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# 1. MessageEnvelope
# ---------------------------------------------------------------------------

@dataclass
class MessageEnvelope:
    """Core message unit propagated through the gossip network.

    Attributes:
        message_id:     Unique identifier (UUID4) used for deduplication.
        created_ts:     Unix epoch timestamp when the message was created.
        ttl_seconds:    How many seconds the message is considered valid.
        channel_id:     Identifier of the logical channel / topic.
        payload:        Opaque bytes — may be onion-encrypted.
        hop_count:      Remaining hops; decremented by each relay.
                        Messages with hop_count == 0 are not forwarded.
        sender_pubkey:  (Optional) Ed25519 public key bytes of the sender.
                        Present for public-channel messages so receivers
                        can verify the signature.
        signature:      (Optional) Ed25519 signature over the canonical
                        message content.
    """

    message_id: uuid.UUID
    created_ts: float
    ttl_seconds: int
    channel_id: str
    payload: bytes
    hop_count: int
    sender_pubkey: Optional[bytes] = None
    signature: Optional[bytes] = None


# ---------------------------------------------------------------------------
# 2. TTL / expiry check
# ---------------------------------------------------------------------------

def is_expired(envelope: MessageEnvelope, clock_skew_tolerance: float = 30.0) -> bool:
    """Return *True* if ``envelope`` has exceeded its TTL.

    We compare the current wall-clock time against
    ``envelope.created_ts + envelope.ttl_seconds``, but allow up to
    ``clock_skew_tolerance`` extra seconds to compensate for imperfect
    clock synchronisation between nodes.

    A message whose ``created_ts`` is in the future beyond the tolerance
    is also considered expired (protects against far-future timestamps).
    """
    now = time.time()
    expiry_time = envelope.created_ts + envelope.ttl_seconds + clock_skew_tolerance
    if now > expiry_time:
        return True
    # Reject messages with a created_ts unreasonably far in the future.
    if envelope.created_ts > now + clock_skew_tolerance:
        return True
    return False


# ---------------------------------------------------------------------------
# 3. TTLBloomFilter — time-bucketed Bloom filter for deduplication
# ---------------------------------------------------------------------------

class _BloomBucket:
    """A single fixed-size Bloom filter backed by a ``bytearray``."""

    __slots__ = ("bits", "size", "num_hashes")

    def __init__(self, size_bits: int = 8192, num_hashes: int = 5):
        self.size = size_bits
        self.num_hashes = num_hashes
        # Each byte stores 8 bits.
        self.bits = bytearray((size_bits + 7) // 8)

    def _hashes(self, key: bytes) -> list[int]:
        """Produce ``num_hashes`` independent bit-positions via double-hashing."""
        h = hashlib.md5(key).digest()  # 16 bytes → two 64-bit ints
        h1 = int.from_bytes(h[:8], "little")
        h2 = int.from_bytes(h[8:], "little")
        return [(h1 + i * h2) % self.size for i in range(self.num_hashes)]

    def add(self, key: bytes) -> None:
        for pos in self._hashes(key):
            byte_idx, bit_idx = divmod(pos, 8)
            self.bits[byte_idx] |= 1 << bit_idx

    def __contains__(self, key: bytes) -> bool:
        for pos in self._hashes(key):
            byte_idx, bit_idx = divmod(pos, 8)
            if not (self.bits[byte_idx] & (1 << bit_idx)):
                return False
        return True

    def memory_bytes(self) -> int:
        """Estimated memory consumed by this bucket (bit-array only)."""
        return len(self.bits)


class TTLBloomFilter:
    """Time-bucketed Bloom filter for bounded-memory message deduplication.

    Time is divided into buckets of ``bucket_seconds`` width.  Each
    ``add(message_id)`` inserts into the bucket corresponding to the
    current time.  ``has(message_id)`` checks **all** live buckets.
    Buckets older than ``max_age_seconds`` are pruned by ``cleanup()``,
    which is called automatically on every ``add`` / ``has`` call.

    Parameters:
        bucket_seconds:   Width of each time bucket (default 60 s).
        max_age_seconds:  Maximum age of a bucket before it is discarded
                          (default 300 s — 5 minutes).
        bucket_size_bits: Bloom-filter size per bucket in bits (default 8 192).
        num_hashes:       Number of hash functions per bucket (default 5).
    """

    def __init__(
        self,
        bucket_seconds: int = 60,
        max_age_seconds: int = 300,
        bucket_size_bits: int = 8192,
        num_hashes: int = 5,
    ):
        self.bucket_seconds = bucket_seconds
        self.max_age_seconds = max_age_seconds
        self.bucket_size_bits = bucket_size_bits
        self.num_hashes = num_hashes
        # Maps bucket_index (int) → _BloomBucket
        self._buckets: Dict[int, _BloomBucket] = {}

    # -- helpers ----------------------------------------------------------

    def _current_bucket_index(self) -> int:
        return int(time.time()) // self.bucket_seconds

    def _min_bucket_index(self) -> int:
        return (int(time.time()) - self.max_age_seconds) // self.bucket_seconds

    @staticmethod
    def _key_bytes(message_id: uuid.UUID) -> bytes:
        return message_id.bytes

    def _get_or_create_bucket(self, idx: int) -> _BloomBucket:
        if idx not in self._buckets:
            self._buckets[idx] = _BloomBucket(
                size_bits=self.bucket_size_bits, num_hashes=self.num_hashes
            )
        return self._buckets[idx]

    # -- public API -------------------------------------------------------

    def add(self, message_id: uuid.UUID) -> None:
        """Record ``message_id`` in the current time bucket."""
        self.cleanup()
        idx = self._current_bucket_index()
        bucket = self._get_or_create_bucket(idx)
        bucket.add(self._key_bytes(message_id))

    def has(self, message_id: uuid.UUID) -> bool:
        """Return *True* if ``message_id`` was (probably) seen before."""
        self.cleanup()
        key = self._key_bytes(message_id)
        for bucket in self._buckets.values():
            if key in bucket:
                return True
        return False

    def cleanup(self) -> None:
        """Remove buckets older than ``max_age_seconds``."""
        min_idx = self._min_bucket_index()
        expired = [idx for idx in self._buckets if idx < min_idx]
        for idx in expired:
            del self._buckets[idx]

    def memory_estimate(self) -> int:
        """Return an estimate of total memory (bytes) used by all buckets."""
        overhead_per_bucket = 64  # Python dict entry + object header estimate
        total = 0
        for bucket in self._buckets.values():
            total += bucket.memory_bytes() + overhead_per_bucket
        return total

    @property
    def max_buckets(self) -> int:
        """Upper bound on number of live buckets at any time."""
        return math.ceil(self.max_age_seconds / self.bucket_seconds) + 1

    def max_memory_estimate(self) -> int:
        """Worst-case memory bound (bytes)."""
        per_bucket = (self.bucket_size_bits + 7) // 8 + 64
        return self.max_buckets * per_bucket


# ---------------------------------------------------------------------------
# 4. Serialization  (MessagePack-like, pure-Python, no external deps)
# ---------------------------------------------------------------------------
#
# Wire format (big-endian):
#   [16 bytes message_id]
#   [8 bytes created_ts as double]
#   [4 bytes ttl_seconds as unsigned int]
#   [2 bytes channel_id length][channel_id utf-8 bytes]
#   [4 bytes payload length][payload bytes]
#   [4 bytes hop_count as signed int]
#   [1 byte flags]:  bit 0 = has sender_pubkey, bit 1 = has signature
#   If has sender_pubkey: [2 bytes len][sender_pubkey bytes]
#   If has signature:     [2 bytes len][signature bytes]
#
# This gives a deterministic, self-describing encoding with no external
# dependencies.

_HEADER_STRUCT = struct.Struct("!16s d I")
_HOP_STRUCT = struct.Struct("!i")
_U16 = struct.Struct("!H")
_U32 = struct.Struct("!I")
_FLAGS_STRUCT = struct.Struct("!B")

_FLAG_HAS_PUBKEY = 0x01
_FLAG_HAS_SIGNATURE = 0x02


def envelope_to_bytes(env: MessageEnvelope) -> bytes:
    """Serialize a *MessageEnvelope* to bytes."""
    parts: list[bytes] = []

    # message_id (16 bytes), created_ts (double), ttl_seconds (uint32)
    parts.append(_HEADER_STRUCT.pack(env.message_id.bytes, env.created_ts, env.ttl_seconds))

    # channel_id
    channel_bytes = env.channel_id.encode("utf-8")
    parts.append(_U16.pack(len(channel_bytes)))
    parts.append(channel_bytes)

    # payload
    parts.append(_U32.pack(len(env.payload)))
    parts.append(env.payload)

    # hop_count
    parts.append(_HOP_STRUCT.pack(env.hop_count))

    # flags + optional fields
    flags = 0
    if env.sender_pubkey is not None:
        flags |= _FLAG_HAS_PUBKEY
    if env.signature is not None:
        flags |= _FLAG_HAS_SIGNATURE
    parts.append(_FLAGS_STRUCT.pack(flags))

    if env.sender_pubkey is not None:
        parts.append(_U16.pack(len(env.sender_pubkey)))
        parts.append(env.sender_pubkey)
    if env.signature is not None:
        parts.append(_U16.pack(len(env.signature)))
        parts.append(env.signature)

    return b"".join(parts)


def envelope_from_bytes(data: bytes) -> MessageEnvelope:
    """Deserialize bytes produced by ``envelope_to_bytes``."""
    offset = 0

    # header
    mid_bytes, created_ts, ttl_seconds = _HEADER_STRUCT.unpack_from(data, offset)
    offset += _HEADER_STRUCT.size
    message_id = uuid.UUID(bytes=mid_bytes)

    # channel_id
    (ch_len,) = _U16.unpack_from(data, offset)
    offset += _U16.size
    channel_id = data[offset: offset + ch_len].decode("utf-8")
    offset += ch_len

    # payload
    (pl_len,) = _U32.unpack_from(data, offset)
    offset += _U32.size
    payload = bytes(data[offset: offset + pl_len])
    offset += pl_len

    # hop_count
    (hop_count,) = _HOP_STRUCT.unpack_from(data, offset)
    offset += _HOP_STRUCT.size

    # flags
    (flags,) = _FLAGS_STRUCT.unpack_from(data, offset)
    offset += _FLAGS_STRUCT.size

    sender_pubkey: Optional[bytes] = None
    signature: Optional[bytes] = None

    if flags & _FLAG_HAS_PUBKEY:
        (pk_len,) = _U16.unpack_from(data, offset)
        offset += _U16.size
        sender_pubkey = bytes(data[offset: offset + pk_len])
        offset += pk_len

    if flags & _FLAG_HAS_SIGNATURE:
        (sig_len,) = _U16.unpack_from(data, offset)
        offset += _U16.size
        signature = bytes(data[offset: offset + sig_len])
        offset += sig_len

    return MessageEnvelope(
        message_id=message_id,
        created_ts=created_ts,
        ttl_seconds=ttl_seconds,
        channel_id=channel_id,
        payload=payload,
        hop_count=hop_count,
        sender_pubkey=sender_pubkey,
        signature=signature,
    )
