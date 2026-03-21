"""Message store with deduplication."""

import time
import threading
from typing import Dict, List, Optional, Set
from hermes_p2p.models import MessageEnvelope


class MessageStore:
    """Thread-safe message store with deduplication and expiration."""

    def __init__(self, max_messages: int = 10000, ttl_seconds: float = 300.0):
        self.max_messages = max_messages
        self.ttl_seconds = ttl_seconds
        self._messages: Dict[str, MessageEnvelope] = {}
        self._seen_ids: Set[str] = set()
        self._lock = threading.Lock()

    def store(self, envelope: MessageEnvelope) -> bool:
        """Store a message. Returns True if stored (not a duplicate)."""
        with self._lock:
            if envelope.message_id in self._seen_ids:
                return False
            self._seen_ids.add(envelope.message_id)
            self._messages[envelope.message_id] = envelope
            self._evict_if_needed()
            return True

    def has_seen(self, message_id: str) -> bool:
        """Check if we've seen this message ID."""
        with self._lock:
            return message_id in self._seen_ids

    def get(self, message_id: str) -> Optional[MessageEnvelope]:
        """Retrieve a message by ID."""
        with self._lock:
            return self._messages.get(message_id)

    def get_channel_messages(self, channel: str) -> List[MessageEnvelope]:
        """Get all messages for a channel."""
        with self._lock:
            return [
                m for m in self._messages.values()
                if m.channel == channel
            ]

    def _evict_if_needed(self):
        """Evict oldest messages if over capacity."""
        if len(self._messages) <= self.max_messages:
            return
        # Sort by timestamp and remove oldest
        sorted_msgs = sorted(self._messages.values(), key=lambda m: m.timestamp)
        to_remove = len(self._messages) - self.max_messages
        for msg in sorted_msgs[:to_remove]:
            del self._messages[msg.message_id]
            # Keep the ID in seen_ids to prevent re-delivery

    def cleanup_expired(self):
        """Remove messages older than TTL."""
        now = time.time()
        with self._lock:
            expired = [
                mid for mid, m in self._messages.items()
                if (now - m.timestamp) > self.ttl_seconds
            ]
            for mid in expired:
                del self._messages[mid]

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._messages)

    @property
    def seen_count(self) -> int:
        with self._lock:
            return len(self._seen_ids)
