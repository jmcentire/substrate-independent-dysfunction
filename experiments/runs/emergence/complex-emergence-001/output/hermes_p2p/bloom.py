"""TTL-expiring Bloom filter for message deduplication."""

import hashlib
import math
import time
from typing import Optional


class TTLBloomFilter:
    """A Bloom filter with time-based expiration.

    The filter is divided into time slots. Each slot is a separate bit array.
    When a slot expires, its bits are cleared, bounding memory usage.
    """

    def __init__(
        self,
        capacity: int = 10000,
        error_rate: float = 0.01,
        ttl_seconds: float = 60.0,
        num_slots: int = 4,
    ):
        self.capacity = capacity
        self.error_rate = error_rate
        self.ttl_seconds = ttl_seconds
        self.num_slots = num_slots

        # Calculate optimal bit array size and hash count per slot
        slot_capacity = capacity // num_slots
        if slot_capacity < 1:
            slot_capacity = 1
        self.slot_bits = self._optimal_size(slot_capacity, error_rate)
        self.num_hashes = self._optimal_hashes(self.slot_bits, slot_capacity)

        # Total bits across all slots
        self.total_bits = self.slot_bits * self.num_slots

        # Initialize slots
        self.slot_duration = ttl_seconds / num_slots
        self._slots = [bytearray(self.slot_bits // 8 + 1) for _ in range(num_slots)]
        self._slot_timestamps = [time.time()] * num_slots
        self._current_slot = 0
        self._item_count = 0
        self._start_time = time.time()

    @staticmethod
    def _optimal_size(n: int, p: float) -> int:
        """Calculate optimal bit array size."""
        if n <= 0:
            n = 1
        if p <= 0:
            p = 0.001
        m = -n * math.log(p) / (math.log(2) ** 2)
        return max(int(m), 64)

    @staticmethod
    def _optimal_hashes(m: int, n: int) -> int:
        """Calculate optimal number of hash functions."""
        if n <= 0:
            n = 1
        k = (m / n) * math.log(2)
        return max(int(k), 1)

    def _get_hashes(self, item: str) -> list:
        """Get hash positions for an item."""
        positions = []
        for i in range(self.num_hashes):
            h = hashlib.sha256(f"{item}:{i}".encode()).hexdigest()
            pos = int(h, 16) % self.slot_bits
            positions.append(pos)
        return positions

    def _rotate_if_needed(self):
        """Rotate to next slot if current one has expired."""
        now = time.time()
        elapsed = now - self._slot_timestamps[self._current_slot]
        if elapsed >= self.slot_duration:
            # Move to the next slot
            self._current_slot = (self._current_slot + 1) % self.num_slots
            # Clear the new current slot
            self._slots[self._current_slot] = bytearray(self.slot_bits // 8 + 1)
            self._slot_timestamps[self._current_slot] = now

    def _set_bit(self, slot: int, pos: int):
        byte_idx = pos // 8
        bit_idx = pos % 8
        self._slots[slot][byte_idx] |= (1 << bit_idx)

    def _get_bit(self, slot: int, pos: int) -> bool:
        byte_idx = pos // 8
        bit_idx = pos % 8
        return bool(self._slots[slot][byte_idx] & (1 << bit_idx))

    def add(self, item: str):
        """Add an item to the bloom filter."""
        self._rotate_if_needed()
        positions = self._get_hashes(item)
        for pos in positions:
            self._set_bit(self._current_slot, pos)
        self._item_count += 1

    def __contains__(self, item: str) -> bool:
        """Check if an item might be in the filter."""
        self._rotate_if_needed()
        positions = self._get_hashes(item)
        now = time.time()
        # Check all non-expired slots
        for slot_idx in range(self.num_slots):
            age = now - self._slot_timestamps[slot_idx]
            if age > self.ttl_seconds:
                continue  # This slot is expired
            # Check if all bits are set in this slot
            all_set = all(self._get_bit(slot_idx, pos) for pos in positions)
            if all_set:
                return True
        return False

    def contains(self, item: str) -> bool:
        """Check if an item might be in the filter."""
        return item in self

    @property
    def memory_bytes(self) -> int:
        """Current memory usage in bytes."""
        return sum(len(s) for s in self._slots)

    @property
    def count(self) -> int:
        """Approximate number of items added."""
        return self._item_count

    def clear(self):
        """Clear all slots."""
        self._slots = [bytearray(self.slot_bits // 8 + 1) for _ in range(self.num_slots)]
        self._slot_timestamps = [time.time()] * self.num_slots
        self._current_slot = 0
        self._item_count = 0
