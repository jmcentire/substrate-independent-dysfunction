"""
HermesP2P Node: manages identity, peer connections, gossip, and message handling.
"""

import json
import random
import threading
import time
from dataclasses import dataclass, field
from typing import (
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
)

from hermes_p2p.bloom_filter import TTLBloomFilter
from hermes_p2p.crypto import KeyManager, OnionRouter, OnionPacket, PACKET_SIZE
from hermes_p2p.message import (
    MessageEnvelope,
    ChannelType,
    verify_public_message,
    decrypt_private_message,
)


@dataclass
class PeerInfo:
    """Information about a known peer."""
    node_id: str
    address: str
    ed25519_pubkey: bytes
    x25519_pubkey: bytes
    last_seen: float = field(default_factory=time.time)


class Node:
    """
    A HermesP2P node that participates in the gossip network.
    
    Responsibilities:
    - Maintain identity (Ed25519 + X25519 keypairs)
    - Track known peers
    - Gossip-propagate messages with configurable fanout
    - Deduplicate messages using a bounded Bloom filter
    - Enforce TTL on messages
    - Handle onion-routed messages (peel layers or deliver)
    - Support public, private, and direct channels
    """

    def __init__(
        self,
        node_id: str,
        address: str = "",
        fanout: int = 3,
        dedup_capacity: int = 5000,
        dedup_segment_ttl: float = 120.0,
        dedup_max_segments: int = 5,
    ):
        self.node_id = node_id
        self.address = address or node_id
        self.fanout = fanout

        # Identity
        self.key_manager = KeyManager()

        # Peer table: node_id -> PeerInfo
        self.peers: Dict[str, PeerInfo] = {}
        self._peers_lock = threading.Lock()

        # Deduplication bloom filter (bounded)
        self._dedup = TTLBloomFilter(
            segment_capacity=dedup_capacity,
            fp_rate=0.01,
            segment_ttl_seconds=dedup_segment_ttl,
            max_segments=dedup_max_segments,
        )
        self._dedup_lock = threading.Lock()

        # Channel subscriptions
        self._subscriptions: Dict[str, ChannelType] = {}
        # Private channel keys
        self._channel_keys: Dict[str, bytes] = {}

        # Message delivery callbacks
        self._message_handlers: Dict[str, List[Callable]] = {}
        # Direct message handlers
        self._dm_handlers: List[Callable] = []

        # Network send function (set by SimulatedNetwork)
        self._send_fn: Optional[Callable] = None

        # Store of received messages (ephemeral, for testing)
        self.received_messages: List[Tuple[str, MessageEnvelope]] = []
        self._received_lock = threading.Lock()

        # Bootstrap peers (well-known addresses)
        self._bootstrap_peers: List[str] = []

        # Set of already-processed peer announcements (prevent infinite recursion)
        self._seen_announces: Set[str] = set()

    def get_peer_info(self) -> PeerInfo:
        """Return this node's PeerInfo for advertisement."""
        return PeerInfo(
            node_id=self.node_id,
            address=self.address,
            ed25519_pubkey=self.key_manager.public_key_bytes(),
            x25519_pubkey=self.key_manager.encryption_public_key_bytes(),
        )

    # ─── Peer Management ─────────────────────────────────────────────

    def add_peer(self, peer: PeerInfo):
        """Register a known peer."""
        if peer.node_id == self.node_id:
            return
        with self._peers_lock:
            self.peers[peer.node_id] = peer

    def remove_peer(self, node_id: str):
        """Remove a peer."""
        with self._peers_lock:
            self.peers.pop(node_id, None)

    def get_random_peers(self, k: int, exclude: Optional[Set[str]] = None) -> List[PeerInfo]:
        """Select up to k random peers, excluding specified node_ids."""
        with self._peers_lock:
            candidates = [
                p for p in self.peers.values()
                if exclude is None or p.node_id not in exclude
            ]
        if len(candidates) <= k:
            return list(candidates)
        return random.sample(candidates, k)

    # ─── Channel Management ──────────────────────────────────────────

    def subscribe(self, channel_id: str, channel_type: ChannelType, channel_key: Optional[bytes] = None):
        """Subscribe to a channel."""
        self._subscriptions[channel_id] = channel_type
        if channel_key is not None:
            self._channel_keys[channel_id] = channel_key

    def on_message(self, channel_id: str, handler: Callable):
        """Register a handler for messages on a channel."""
        if channel_id not in self._message_handlers:
            self._message_handlers[channel_id] = []
        self._message_handlers[channel_id].append(handler)

    def on_direct_message(self, handler: Callable):
        """Register a handler for direct messages."""
        self._dm_handlers.append(handler)

    # ─── Network Interface ───────────────────────────────────────────

    def set_send_fn(self, fn: Callable):
        """Set the function used to send messages to other nodes."""
        self._send_fn = fn

    def _send_to_peer(self, peer_id: str, data: bytes):
        """Send raw data to a specific peer."""
        if self._send_fn:
            self._send_fn(self.node_id, peer_id, data)

    # ─── Message Reception ───────────────────────────────────────────

    def receive_message(self, raw: bytes, from_node_id: Optional[str] = None):
        """
        Called when a message arrives from the network.
        Handles dedup, TTL, gossip forwarding, and delivery.
        """
        try:
            env = MessageEnvelope.deserialize(raw)
        except Exception:
            return  # Malformed message

        # 1. TTL check
        if env.is_expired():
            return  # Silently drop expired messages

        # 2. Deduplication
        with self._dedup_lock:
            if env.message_id in self._dedup:
                return  # Already seen
            self._dedup.add(env.message_id)

        # 3. Handle the message based on channel type
        self._handle_message(env)

        # 4. Gossip forward if hop_count > 0 (but NOT for direct messages,
        #    which use onion routing for forwarding)
        if env.hop_count > 0 and env.channel_type != ChannelType.DIRECT:
            forwarded_env = MessageEnvelope(
                message_id=env.message_id,
                created_ts=env.created_ts,
                ttl_seconds=env.ttl_seconds,
                channel_id=env.channel_id,
                payload=env.payload,
                hop_count=env.hop_count - 1,
                channel_type=env.channel_type,
                sender_pubkey=env.sender_pubkey,
                signature=env.signature,
            )
            self._gossip_forward(forwarded_env, exclude={from_node_id} if from_node_id else set())

    def _handle_message(self, env: MessageEnvelope):
        """Process a message locally (deliver to handlers if subscribed)."""
        if env.channel_type == ChannelType.DIRECT:
            self._handle_direct_message(env)
            return

        channel_id = env.channel_id

        if channel_id not in self._subscriptions:
            return  # Not subscribed

        if env.channel_type == ChannelType.PUBLIC:
            # Verify signature
            if not verify_public_message(env):
                return  # Invalid signature
            payload_text = env.payload.decode("utf-8")

        elif env.channel_type == ChannelType.PRIVATE:
            channel_key = self._channel_keys.get(channel_id)
            if channel_key is None:
                return  # Don't have channel key
            payload_text = decrypt_private_message(env, channel_key)
            if payload_text is None:
                return  # Decryption failed

        else:
            return

        with self._received_lock:
            self.received_messages.append((payload_text, env))

        for handler in self._message_handlers.get(channel_id, []):
            try:
                handler(self.node_id, channel_id, payload_text, env)
            except Exception:
                pass

    def _handle_direct_message(self, env: MessageEnvelope):
        """
        Handle an onion-routed direct message.
        Try to peel one layer. If it's the final layer, deliver.
        If it's a relay layer, forward to the next hop.
        """
        try:
            next_hop, data, is_final = OnionRouter.peel_layer(
                env.payload,
                self.key_manager.encryption_private_key,
            )
        except Exception:
            return  # Not for us or corrupted

        if is_final:
            payload_text = data.decode("utf-8", errors="replace")
            with self._received_lock:
                self.received_messages.append((payload_text, env))
            for handler in self._dm_handlers:
                try:
                    handler(self.node_id, payload_text, env)
                except Exception:
                    pass
        else:
            # Forward to next hop — create a new envelope with the peeled data
            fwd_env = MessageEnvelope(
                message_id=env.message_id + "_fwd",
                created_ts=env.created_ts,
                ttl_seconds=env.ttl_seconds,
                channel_id=env.channel_id,
                payload=data,
                hop_count=env.hop_count,
                channel_type=ChannelType.DIRECT,
            )
            raw = fwd_env.serialize()
            self._send_to_peer(next_hop, raw)

    def _gossip_forward(self, env: MessageEnvelope, exclude: Set[str] = None):
        """Forward message to k random peers."""
        if exclude is None:
            exclude = set()
        peers = self.get_random_peers(self.fanout, exclude=exclude | {self.node_id})
        raw = env.serialize()
        for peer in peers:
            self._send_to_peer(peer.node_id, raw)

    # ─── Sending Messages ────────────────────────────────────────────

    def send_to_channel(self, env: MessageEnvelope):
        """Send a message to a channel via gossip."""
        # Mark as seen to prevent echo
        with self._dedup_lock:
            self._dedup.add(env.message_id)

        # Deliver locally
        self._handle_message(env)

        # Gossip (decrement hop before forwarding)
        if env.hop_count > 0:
            forwarded_env = MessageEnvelope(
                message_id=env.message_id,
                created_ts=env.created_ts,
                ttl_seconds=env.ttl_seconds,
                channel_id=env.channel_id,
                payload=env.payload,
                hop_count=env.hop_count - 1,
                channel_type=env.channel_type,
                sender_pubkey=env.sender_pubkey,
                signature=env.signature,
            )
            self._gossip_forward(forwarded_env)

    def send_direct(
        self,
        plaintext: str,
        recipient_node_id: str,
        route_node_ids: Optional[List[str]] = None,
        ttl_seconds: float = 60.0,
        hop_count: int = 10,
    ) -> Optional[MessageEnvelope]:
        """
        Send a direct message via onion routing.
        
        Args:
            plaintext: Message text.
            recipient_node_id: The recipient's node ID.
            route_node_ids: Optional list of relay node IDs. If None, random relays chosen.
            ttl_seconds: TTL for the envelope.
            hop_count: Hop count for the envelope.
        """
        # Resolve recipient
        with self._peers_lock:
            recipient_peer = self.peers.get(recipient_node_id)
        if recipient_peer is None:
            return None

        # Build route
        if route_node_ids is None:
            relays = self.get_random_peers(
                3, exclude={self.node_id, recipient_node_id}
            )
            if not relays:
                route_node_ids = []
            else:
                route_node_ids = [r.node_id for r in relays]

        route: List[Tuple[str, bytes]] = []
        for nid in route_node_ids:
            with self._peers_lock:
                peer = self.peers.get(nid)
            if peer is None:
                continue
            route.append((nid, peer.x25519_pubkey))

        # Build onion packet
        onion = OnionRouter.build_onion(
            payload=plaintext.encode("utf-8"),
            route=route,
            recipient_node_id=recipient_node_id,
            recipient_encryption_pubkey=recipient_peer.x25519_pubkey,
        )

        from hermes_p2p.message import create_direct_message
        env = create_direct_message(
            payload=onion.to_bytes(),
            ttl_seconds=ttl_seconds,
            hop_count=hop_count,
        )

        # Mark as seen
        with self._dedup_lock:
            self._dedup.add(env.message_id)

        # If we have relays, send to first relay
        if route:
            first_relay_id = route[0][0]
            raw = env.serialize()
            self._send_to_peer(first_relay_id, raw)
        else:
            # No relays — send directly to recipient
            raw = env.serialize()
            self._send_to_peer(recipient_node_id, raw)

        return env

    # ─── Bootstrap ───────────────────────────────────────────────────

    def set_bootstrap_peers(self, peer_addresses: List[str]):
        """Set well-known bootstrap peer addresses."""
        self._bootstrap_peers = peer_addresses

    def bootstrap(self):
        """
        Connect to bootstrap peers and announce ourselves.
        In the simulated network, bootstrap peers are node_ids.
        """
        my_info = self.get_peer_info()
        announcement = {
            "type": "peer_announce",
            "node_id": my_info.node_id,
            "address": my_info.address,
            "ed25519_pubkey": my_info.ed25519_pubkey.hex(),
            "x25519_pubkey": my_info.x25519_pubkey.hex(),
        }
        raw = json.dumps(announcement).encode("utf-8")
        for addr in self._bootstrap_peers:
            self._send_to_peer(addr, raw)

    def receive_raw(self, raw: bytes, from_node_id: Optional[str] = None):
        """
        Receive raw data — could be a message or a peer announcement.
        """
        try:
            data = json.loads(raw.decode("utf-8"))
            if isinstance(data, dict) and data.get("type") == "peer_announce":
                self._handle_peer_announce(data, from_node_id)
                return
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
            pass

        # Otherwise, treat as a message envelope
        self.receive_message(raw, from_node_id)

    def _handle_peer_announce(self, data: dict, from_node_id: Optional[str]):
        """Handle a peer announcement, avoiding infinite loops."""
        announcing_id = data["node_id"]

        # Avoid processing the same announcement twice
        announce_key = announcing_id
        if announce_key in self._seen_announces:
            return
        self._seen_announces.add(announce_key)

        # Don't add ourselves
        if announcing_id == self.node_id:
            return

        peer = PeerInfo(
            node_id=data["node_id"],
            address=data["address"],
            ed25519_pubkey=bytes.fromhex(data["ed25519_pubkey"]),
            x25519_pubkey=bytes.fromhex(data["x25519_pubkey"]),
        )
        self.add_peer(peer)

        # Send back our own info so the new node learns about us
        my_info = self.get_peer_info()
        reply = {
            "type": "peer_announce",
            "node_id": my_info.node_id,
            "address": my_info.address,
            "ed25519_pubkey": my_info.ed25519_pubkey.hex(),
            "x25519_pubkey": my_info.x25519_pubkey.hex(),
        }
        self._send_to_peer(announcing_id, json.dumps(reply).encode("utf-8"))

        # Share our other known peers with the new node
        with self._peers_lock:
            peers_to_share = [
                p for pid, p in self.peers.items()
                if pid != announcing_id
            ]
        for pinfo in peers_to_share:
            share = {
                "type": "peer_announce",
                "node_id": pinfo.node_id,
                "address": pinfo.address,
                "ed25519_pubkey": pinfo.ed25519_pubkey.hex(),
                "x25519_pubkey": pinfo.x25519_pubkey.hex(),
            }
            self._send_to_peer(announcing_id, json.dumps(share).encode("utf-8"))

    # ─── Dedup Stats ─────────────────────────────────────────────────

    def dedup_memory_bytes(self) -> int:
        """Return memory used by the deduplication filter."""
        with self._dedup_lock:
            return self._dedup.memory_bytes()

    def dedup_is_seen(self, message_id: str) -> bool:
        """Check if a message ID has been seen."""
        with self._dedup_lock:
            return message_id in self._dedup
