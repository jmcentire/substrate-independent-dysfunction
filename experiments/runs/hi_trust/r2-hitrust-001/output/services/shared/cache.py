"""
Shared Redis cache abstraction.

Uses in-memory dict for testing, Redis for production.
"""

import os
import json
import time
import threading
from typing import Optional, Set


class MemoryCache:
    """In-memory cache that mimics Redis interface for testing."""

    def __init__(self):
        self._store: dict[str, tuple[any, Optional[float]]] = {}
        self._sets: dict[str, set] = {}
        self._hashes: dict[str, dict] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            if key in self._store:
                val, expires = self._store[key]
                if expires and time.time() > expires:
                    del self._store[key]
                    return None
                return val
            return None

    def set(self, key: str, value: str, ex: Optional[int] = None):
        with self._lock:
            expires = time.time() + ex if ex else None
            self._store[key] = (value, expires)

    def delete(self, key: str):
        with self._lock:
            self._store.pop(key, None)
            self._sets.pop(key, None)
            self._hashes.pop(key, None)

    def exists(self, key: str) -> bool:
        with self._lock:
            if key in self._store:
                val, expires = self._store[key]
                if expires and time.time() > expires:
                    del self._store[key]
                    return False
                return True
            return key in self._sets or key in self._hashes

    def setex(self, key: str, seconds: int, value: str):
        self.set(key, value, ex=seconds)

    def setnx(self, key: str, value: str) -> bool:
        with self._lock:
            if key in self._store:
                val, expires = self._store[key]
                if expires and time.time() > expires:
                    del self._store[key]
                else:
                    return False
            self._store[key] = (value, None)
            return True

    # Set operations
    def sadd(self, key: str, *members):
        with self._lock:
            if key not in self._sets:
                self._sets[key] = set()
            for m in members:
                self._sets[key].add(m)

    def smembers(self, key: str) -> Set[str]:
        with self._lock:
            return set(self._sets.get(key, set()))

    def srem(self, key: str, *members):
        with self._lock:
            if key in self._sets:
                for m in members:
                    self._sets[key].discard(m)

    # Hash operations
    def hset(self, key: str, mapping: dict):
        with self._lock:
            if key not in self._hashes:
                self._hashes[key] = {}
            self._hashes[key].update({k: str(v) for k, v in mapping.items()})

    def hget(self, key: str, field: str) -> Optional[str]:
        with self._lock:
            if key in self._hashes:
                return self._hashes[key].get(field)
            return None

    def hgetall(self, key: str) -> dict:
        with self._lock:
            return dict(self._hashes.get(key, {}))

    def flushall(self):
        with self._lock:
            self._store.clear()
            self._sets.clear()
            self._hashes.clear()


# Global cache instance
_cache_instance: Optional[MemoryCache] = None


def get_cache() -> MemoryCache:
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = MemoryCache()
    return _cache_instance


def reset_cache():
    global _cache_instance
    _cache_instance = MemoryCache()
