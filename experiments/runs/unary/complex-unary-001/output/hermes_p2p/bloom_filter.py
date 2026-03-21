"""
Bounded Bloom filter with TTL-expiring entries for message deduplication.

Uses a segmented/rotating Bloom filter approach: we maintain multiple
time-bucketed filters and expire old buckets, keeping memory bounded.
"""

import hashlib
import math
import time
from typing import Optional


class BloomFilter:
    """
    A single fixed-size Bloom filter backed by a bit array.
    """

    def __init__(self, capacity: int = 10000, fp_rate: float = 0.01):
        """
        Args:
            capacity: Expected number of items.
            fp_rate: Desired false positive rate.
        """
        self.capacity = capacity
        self.fp_rate = fp_rate
        # Calculate optimal size and number of hash functions
        self.size = self._optimal_size(capacity, fp_rate)
        self.num_hashes = self._optimal_hashes(self.size, capacity)
        self._bit_array = bytearray(math.ceil(self.size / 8))
        self._count = 0

    @staticmethod
    def _optimal_size(n: int, p: float) -> int:
        """Optimal bit array size for n items and false positive rate p."""
        if n <= 0:
            n = 1
        m = -(n * math.log(p)) / (math.log(2) ** 2)
        return max(int(math.ceil(m)), 64)

    @staticmethod
    def _optimal_hashes(m: int, n: int) -> int:
        """Optimal number of hash functions."""
        if n <= 0:
            n = 1
        k = (m / n) * math.log(2)
        return max(int(math.ceil(k)), 1)

    def _get_hashes(self, item: str) -> list:
        """Generate hash positions using double hashing with SHA-256."""
        data = item.encode("utf-8") if isinstance(item, str) else item
        h1 = int(hashlib.sha256(data).hexdigest(), 16)
        h2 = int(hashlib.md5(data).hexdigest(), 16)
        positions = []
        for i in range(self.num_hashes):
            pos = (h1 + i * h2) % self.size
            positions.append(pos)
        return positions

    def _set_bit(self, pos: int):
        byte_idx = pos // 8
        bit_idx = pos % 8
        self._bit_array[byte_idx] |= (1 << bit_idx)

    def _get_bit(self, pos: int) -> bool:
        byte_idx = pos // 8
        bit_idx = pos % 8
        return bool(self._bit_array[byte_idx] & (1 << bit_idx))

    def add(self, item: str):
        """Add an item to the filter."""
        for pos in self._get_hashes(item):
            self._set_bit(pos)
        self._count += 1

    def __contains__(self, item: str) -> bool:
        """Check if item might be in the filter (may have false positives)."""
        return all(self._get_bit(pos) for pos in self._get_hashes(item))

    @property
    def count(self) -> int:
        return self._count

    def memory_bytes(self) -> int:
        """Return memory used by the bit array."""
        return len(self._bit_array)


class TTLBloomFilter:
    """
    A time-bucketed rotating Bloom filter that automatically expires old entries.
    
    Maintains several BloomFilter segments, each covering a time window.
    Old segments are discarded, bounding memory usage.
    """

    def __init__(
        self,
        segment_capacity: int = 5000,
        fp_rate: float = 0.01,
        segment_ttl_seconds: float = 60.0,
        max_segments: int = 5,
    ):
        """
        Args:
            segment_capacity: Capacity per segment.
            fp_rate: False positive rate per segment.
            segment_ttl_seconds: Duration of each time segment.
            max_segments: Maximum number of segments to retain.
        """
        self.segment_capacity = segment_capacity
        self.fp_rate = fp_rate
        self.segment_ttl = segment_ttl_seconds
        self.max_segments = max_segments
        # Each segment: (creation_time, BloomFilter)
        self._segments: list = []
        self._ensure_current_segment()

    def _now(self) -> float:
        return time.time()

    def _ensure_current_segment(self):
        """Create a new segment if none exists or the current one is expired."""
        now = self._now()
        if not self._segments or (now - self._segments[-1][0]) >= self.segment_ttl:
            new_seg = BloomFilter(self.segment_capacity, self.fp_rate)
            self._segments.append((now, new_seg))
        self._prune()

    def _prune(self):
        """Remove segments older than max_segments * segment_ttl."""
        now = self._now()
        cutoff = now - (self.max_segments * self.segment_ttl)
        self._segments = [
            (ts, bf) for ts, bf in self._segments if ts >= cutoff
        ]
        # Also cap at max_segments
        if len(self._segments) > self.max_segments:
            self._segments = self._segments[-self.max_segments:]

    def add(self, item: str):
        """Add an item to the current segment."""
        self._ensure_current_segment()
        self._segments[-1][1].add(item)

    def __contains__(self, item: str) -> bool:
        """Check all active segments."""
        self._ensure_current_segment()
        return any(item in bf for _, bf in self._segments)

    def memory_bytes(self) -> int:
        """Total memory across all segments."""
        return sum(bf.memory_bytes() for _, bf in self._segments)

    @property
    def num_segments(self) -> int:
        return len(self._segments)

    @property
    def total_count(self) -> int:
        return sum(bf.count for _, bf in self._segments)
