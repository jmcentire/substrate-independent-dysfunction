"""
Bounded deduplication structures: Bloom filter and TTL-expiring set.
"""

import hashlib
import math
import time
import threading
from typing import Optional


class BloomFilter:
    """
    A space-efficient probabilistic data structure for set membership testing.
    Used for bounded deduplication of message IDs.
    """

    def __init__(self, capacity: int = 10000, fp_rate: float = 0.01):
        """
        Args:
            capacity: Expected number of elements.
            fp_rate: Desired false positive rate.
        """
        self.capacity = capacity
        self.fp_rate = fp_rate
        # Calculate optimal size and number of hash functions
        self.size = self._optimal_size(capacity, fp_rate)
        self.num_hashes = self._optimal_hashes(self.size, capacity)
        self.bit_array = bytearray(math.ceil(self.size / 8))
        self.count = 0
        self._lock = threading.Lock()

    @staticmethod
    def _optimal_size(n: int, p: float) -> int:
        """Calculate optimal bit array size."""
        m = -(n * math.log(p)) / (math.log(2) ** 2)
        return max(int(math.ceil(m)), 64)

    @staticmethod
    def _optimal_hashes(m: int, n: int) -> int:
        """Calculate optimal number of hash functions."""
        k = (m / max(n, 1)) * math.log(2)
        return max(int(math.ceil(k)), 1)

    def _get_hashes(self, item: str) -> list:
        """Generate hash positions using double hashing."""
        h1 = int(hashlib.sha256(item.encode()).hexdigest(), 16)
        h2 = int(hashlib.md5(item.encode()).hexdigest(), 16)
        return [(h1 + i * h2) % self.size for i in range(self.num_hashes)]

    def add(self, item: str) -> bool:
        """
        Add an item. Returns True if item was already possibly present.
        """
        with self._lock:
            positions = self._get_hashes(item)
            was_present = all(
                self.bit_array[pos // 8] & (1 << (pos % 8))
                for pos in positions
            )
            for pos in positions:
                self.bit_array[pos // 8] |= (1 << (pos % 8))
            if not was_present:
                self.count += 1
            return was_present

    def __contains__(self, item: str) -> bool:
        with self._lock:
            positions = self._get_hashes(item)
            return all(
                self.bit_array[pos // 8] & (1 << (pos % 8))
                for pos in positions
            )

    def memory_bytes(self) -> int:
        """Return approximate memory usage in bytes."""
        return len(self.bit_array)

    def is_near_capacity(self) -> bool:
        """Check if the filter is getting full."""
        return self.count >= self.capacity * 0.8


class TTLExpiringSet:
    """
    A bounded set where entries expire after a configurable TTL.
    
    Uses a precise timestamp-based approach with bounded capacity.
    When capacity is exceeded, oldest entries are evicted.
    A backing bloom filter provides fast negative lookups.
    """

    def __init__(self, ttl_seconds: float = 300.0, capacity: int = 10000,
                 fp_rate: float = 0.01):
        self.ttl_seconds = ttl_seconds
        self.capacity = capacity
        self.fp_rate = fp_rate
        self._lock = threading.Lock()
        # Primary store: item -> expire_time
        self._entries: dict = {}
        self._max_entries = capacity
        # Bloom filter for fast "definitely not seen" checks
        self._bloom = BloomFilter(capacity, fp_rate)
        self._bloom_generation = 0
        self._bloom_start = time.time()

    def _cleanup(self):
        """Remove expired entries."""
        now = time.time()
        expired = [k for k, v in self._entries.items() if v <= now]
        for k in expired:
            del self._entries[k]
        
        # Rotate bloom filter if it's getting stale
        if now - self._bloom_start > self.ttl_seconds:
            self._bloom = BloomFilter(self.capacity, self.fp_rate)
            self._bloom_start = now
            # Re-add non-expired entries
            for k in self._entries:
                self._bloom.add(k)

    def add(self, item: str, ttl: Optional[float] = None) -> bool:
        """
        Add item. Returns True if it was already present (duplicate).
        """
        with self._lock:
            self._cleanup()
            effective_ttl = ttl if ttl is not None else self.ttl_seconds
            now = time.time()

            # Check if already present and not expired
            if item in self._entries:
                if self._entries[item] > now:
                    return True  # Already present
                else:
                    del self._entries[item]

            # Add to entries
            self._entries[item] = now + effective_ttl
            self._bloom.add(item)

            # Evict oldest if over capacity
            if len(self._entries) > self._max_entries:
                sorted_items = sorted(self._entries.items(), key=lambda x: x[1])
                for k, _ in sorted_items[:len(self._entries) - self._max_entries]:
                    del self._entries[k]

            return False

    def __contains__(self, item: str) -> bool:
        with self._lock:
            now = time.time()
            # Fast negative check with bloom
            if item not in self._bloom:
                return False
            # Precise check with entries
            if item in self._entries:
                if self._entries[item] > now:
                    return True
                else:
                    del self._entries[item]
                    return False
            return False

    def memory_bytes(self) -> int:
        """Approximate memory usage."""
        bloom_mem = self._bloom.memory_bytes()
        # Rough estimate for entries dict
        entries_mem = len(self._entries) * 100  # ~100 bytes per entry estimate
        return bloom_mem + entries_mem
