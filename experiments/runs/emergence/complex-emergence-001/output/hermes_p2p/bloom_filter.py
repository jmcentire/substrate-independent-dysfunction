"""Bounded Bloom filter with TTL-based expiration for deduplication."""

import hashlib
import math
import time
from typing import Optional


class BloomFilter:
    """A simple Bloom filter for deduplication."""

    def __init__(self, capacity: int = 10000, error_rate: float = 0.01):
        self.capacity = capacity
        self.error_rate = error_rate
        # Calculate optimal size and number of hash functions
        self.size = self._optimal_size(capacity, error_rate)
        self.num_hashes = self._optimal_hashes(self.size, capacity)
        self.bit_array = bytearray(self.size)
        self.count = 0

    @staticmethod
    def _optimal_size(n: int, p: float) -> int:
        """Calculate optimal bit array size."""
        m = -1 * (n * math.log(p)) / (math.log(2) ** 2)
        return max(int(math.ceil(m)), 64)

    @staticmethod
    def _optimal_hashes(m: int, n: int) -> int:
        """Calculate optimal number of hash functions."""
        k = (m / n) * math.log(2)
        return max(int(math.ceil(k)), 1)

    def _get_hashes(self, item: str) -> list:
        """Generate hash positions for an item."""
        positions = []
        for i in range(self.num_hashes):
            h = hashlib.sha256(f"{item}:{i}".encode()).hexdigest()
            pos = int(h, 16) % self.size
            positions.append(pos)
        return positions

    def add(self, item: str) -> None:
        """Add an item to the filter."""
        for pos in self._get_hashes(item):
            self.bit_array[pos] = 1
        self.count += 1

    def __contains__(self, item: str) -> bool:
        """Check if an item might be in the filter."""
        return all(self.bit_array[pos] for pos in self._get_hashes(item))

    def memory_bytes(self) -> int:
        """Return approximate memory usage in bytes."""
        return len(self.bit_array)


class TTLBloomFilter:
    """A Bloom filter that rotates to bound memory usage.
    
    Uses two generations: when the current generation fills up,
    it becomes the old generation and a new empty one is created.
    This bounds memory to 2x a single filter while providing
    graceful expiration.
    """

    def __init__(self, capacity: int = 10000, error_rate: float = 0.01,
                 rotation_interval: float = 300.0):
        self.capacity = capacity
        self.error_rate = error_rate
        self.rotation_interval = rotation_interval
        self._current = BloomFilter(capacity, error_rate)
        self._previous: Optional[BloomFilter] = None
        self._last_rotation = time.time()

    def _maybe_rotate(self) -> None:
        """Rotate filters if the current one is full or interval elapsed."""
        now = time.time()
        should_rotate = (
            self._current.count >= self.capacity or
            (now - self._last_rotation) >= self.rotation_interval
        )
        if should_rotate:
            self._previous = self._current
            self._current = BloomFilter(self.capacity, self.error_rate)
            self._last_rotation = now

    def add(self, item: str) -> None:
        """Add an item to the filter."""
        self._maybe_rotate()
        self._current.add(item)

    def __contains__(self, item: str) -> bool:
        """Check if item might be in either generation."""
        if item in self._current:
            return True
        if self._previous is not None and item in self._previous:
            return True
        return False

    def memory_bytes(self) -> int:
        """Return approximate total memory usage."""
        total = self._current.memory_bytes()
        if self._previous is not None:
            total += self._previous.memory_bytes()
        return total

    def force_rotate(self) -> None:
        """Force a rotation (useful for testing)."""
        self._previous = self._current
        self._current = BloomFilter(self.capacity, self.error_rate)
        self._last_rotation = time.time()
