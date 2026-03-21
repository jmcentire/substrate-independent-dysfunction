"""
Node implementation for HermesP2P.

Each node has:
- Ed25519 keypair for identity / signing
- X25519 keypair for encryption (onion routing, DMs)
- Peer table
- Message store with TTL enforcement
- TTL-expiring set for deduplication (bounded)
- Gossip forwarding logic
- Onion routing processing
"""

import os
import time
import random
import threading
from typing import Dict, List, Optional, Set, Callable, Tuple
from dataclasses import dataclass, field

from nacl.signing import SigningKey, VerifyKey
from nacl.public import PrivateKey, PublicKey

from hermes_p2p.bloom_filter import TTLExpiringSet
from hermes_p2p.crypto import OnionRouter, ChannelCrypto, PACKET_SIZE
from hermes_p2p.message import Message, ChannelType, PeerAnnouncement


@dataclass
class NodeIdentity:
    """A node's cryptographic identity."""
    signing_key: SigningKey
    verify_key: VerifyKey
    encryption_private_key: PrivateKey
    encryption_public_key: PublicKey
    address: str

    @staticmethod
    def generate(address: str) -> "NodeIdentity":
        """Generate a new random identity."""
        sk = SigningKey.generate()
        vk = sk.verify_key
        enc_private = PrivateKey.generate()
        enc_public = enc_private.public_key
        return NodeIdentity(
            signing_key=sk,
            verify_key=vk,
            encryption_private_key=enc_private,
            encryption_public_key=enc_public,
            address=address,
        )

    @property
    def public_key_hex(self) -> str:
        return bytes(self.encryption_public_key).hex()

    @property
    def verify_key_hex(self) -> str:
        return bytes(self.verify_key).hex()


@dataclass
class PeerInfo:
    """Information about a known peer."""
    public_key_hex: str
    encryption_public_key: PublicKey
    verify_key: Optional[VerifyKey]
    address: str
    last_seen: float = 0.0


class Node:
    """
    A HermesP2P network node.
    """

    def __init__(
        self,
        address: str,
        identity: Optional[NodeIdentity] = None,
        fanout: int = 3,
        max_hops: int = 10,
        dedup_capacity: int = 10000,
        dedup_ttl: float = 300.0,
    ):
        self.address = address
        self.identity = identity or NodeIdentity.generate(address)
        self.fanout = fanout
        self.max_hops = max_hops

        # Peer table: address -> PeerInfo
        self.peers: Dict[str, PeerInfo] = {}
        self._peers_lock = threading.Lock()

        # Deduplication using TTL-expiring set (bounded)
        self.dedup = TTLExpiringSet(
            ttl_seconds=dedup_ttl,
            capacity=dedup_capacity,
        )

        # Message store (messages we're interested in, subject to TTL)
        self.message_store: List[Message] = []
        self._store_lock = threading.Lock()

        # Channel subscriptions: channel_id -> channel_key (for private) or None
        self.subscriptions: Dict[str, Optional[bytes]] = {}

        # Channel verify keys: channel_id -> {pubkey_hex: VerifyKey}
        self.channel_verify_keys: Dict[str, Dict[str, VerifyKey]] = {}

        # Callbacks
        self._on_message_callbacks: List[Callable[[Message, Optional[bytes]], None]] = []

        # Network send function (injected by SimulatedNetwork)
        self._send_fn: Optional[Callable[[str, dict], None]] = None

        # Bootstrap nodes
        self.bootstrap_addresses: List[str] = []

        # Track received decrypted payloads for testing
        self.received_payloads: List[Tuple[str, bytes]] = []
        self._received_lock = threading.Lock()

        # Onion relay log: records raw encrypted packets this node handled as relay
        self.relay_log: List[bytes] = []

        # Running flag
        self.running = True

    @property
    def public_key_hex(self) -> str:
        return self.identity.public_key_hex

    def set_send_function(self, fn: Callable[[str, dict], None]):
        """Set the function used to send messages to other nodes."""
        self._send_fn = fn

    def on_message(self, callback: Callable[[Message, Optional[bytes]], None]):
        """Register a callback for received messages."""
        self._on_message_callbacks.append(callback)

    def subscribe(self, channel_id: str, channel_key: Optional[bytes] = None):
        """Subscribe to a channel."""
        self.subscriptions[channel_id] = channel_key

    def add_channel_verify_key(self, channel_id: str, pubkey_hex: str, verify_key: VerifyKey):
        """Add a verify key for a public channel sender."""
        if channel_id not in self.channel_verify_keys:
            self.channel_verify_keys[channel_id] = {}
        self.channel_verify_keys[channel_id][pubkey_hex] = verify_key

    def add_peer(self, address: str, encryption_public_key: PublicKey,
                 verify_key: Optional[VerifyKey] = None):
        """Add or update a peer."""
        with self._peers_lock:
            pk_hex = bytes(encryption_public_key).hex()
            self.peers[address] = PeerInfo(
                public_key_hex=pk_hex,
                encryption_public_key=encryption_public_key,
                verify_key=verify_key,
                address=address,
                last_seen=time.time(),
            )

    def remove_peer(self, address: str):
        """Remove a peer."""
        with self._peers_lock:
            self.peers.pop(address, None)

    def get_peer_addresses(self) -> List[str]:
        """Get list of known peer addresses."""
        with self._peers_lock:
            return [a for a in self.peers.keys() if a != self.address]

    def get_random_peers(self, k: int, exclude: Optional[str] = None) -> List[str]:
        """Get up to k random peer addresses."""
        with self._peers_lock:
            candidates = [
                a for a in self.peers.keys()
                if a != self.address and a != exclude
            ]
        if len(candidates) <= k:
            return candidates
        return random.sample(candidates, k)

    # --- Sending ---

    def send_public_message(
        self,
        channel_id: str,
        plaintext: bytes,
        ttl_seconds: float = 60.0,
        hops: int = 10,
    ) -> Message:
        """Send a message on a public channel (signed, not encrypted)."""
        signed_payload = ChannelCrypto.sign_public_message(
            plaintext, self.identity.signing_key
        )
        msg = Message(
            ttl_seconds=ttl_seconds,
            channel_id=channel_id,
            channel_type=ChannelType.PUBLIC,
            payload=signed_payload,
            hops_remaining=hops,
            sender_pubkey_hex=self.identity.verify_key_hex,
        )
        self._inject_message(msg)
        return msg

    def send_private_message(
        self,
        channel_id: str,
        plaintext: bytes,
        channel_key: bytes,
        ttl_seconds: float = 60.0,
        hops: int = 10,
    ) -> Message:
        """Send a message on a private channel (symmetric encrypted)."""
        encrypted = ChannelCrypto.encrypt_private_channel(plaintext, channel_key)
        msg = Message(
            ttl_seconds=ttl_seconds,
            channel_id=channel_id,
            channel_type=ChannelType.PRIVATE,
            payload=encrypted,
            hops_remaining=hops,
        )
        self._inject_message(msg)
        return msg

    def send_direct_message(
        self,
        recipient_pubkey: PublicKey,
        plaintext: bytes,
        route: Optional[List[PublicKey]] = None,
        ttl_seconds: float = 60.0,
        hops: int = 10,
    ) -> Message:
        """
        Send a direct message via onion routing.

        Args:
            recipient_pubkey: Recipient's encryption public key.
            plaintext: The message content.
            route: List of relay public keys for onion routing. If None, simple DM.
            ttl_seconds: Message TTL.
            hops: Gossip hop count.
        """
        # Encrypt the plaintext for the recipient using SealedBox
        dm_encrypted = ChannelCrypto.encrypt_direct_message(plaintext, recipient_pubkey)

        if route and len(route) > 0:
            # Build onion packet with relays
            onion_payload = OnionRouter.build_onion(
                payload=dm_encrypted,
                route=route,
                recipient_pubkey=recipient_pubkey,
            )
        else:
            # No onion route, just send directly encrypted
            onion_payload = dm_encrypted

        msg = Message(
            ttl_seconds=ttl_seconds,
            channel_id="direct",
            channel_type=ChannelType.DIRECT,
            payload=onion_payload,
            hops_remaining=hops,
        )
        self._inject_message(msg)
        return msg

    def _inject_message(self, msg: Message):
        """Inject a locally-created message into the gossip network."""
        # Mark as seen
        self.dedup.add(msg.message_id, msg.ttl_seconds)
        # Process locally
        self._process_message(msg)
        # Gossip to peers
        self._gossip(msg)

    # --- Receiving ---

    def receive_message(self, msg_dict: dict, from_address: Optional[str] = None):
        """
        Called when a message arrives from the network.
        """
        if not self.running:
            return

        msg = Message.from_dict(msg_dict)

        # TTL check
        if msg.is_expired():
            return

        # Deduplication
        if self.dedup.add(msg.message_id, msg.ttl_seconds):
            # Already seen — deduplicate
            return

        # Process locally
        self._process_message(msg)

        # Gossip forward if hops remain
        if msg.hops_remaining > 0:
            fwd = msg.copy()
            fwd.hops_remaining -= 1
            self._gossip(fwd, exclude=from_address)

    def _process_message(self, msg: Message):
        """Process a message locally (decrypt, verify, store if subscribed)."""
        # Store for TTL-based retention
        with self._store_lock:
            self.message_store.append(msg)

        decrypted_payload = None

        if msg.channel_type == ChannelType.PUBLIC:
            if msg.channel_id in self.subscriptions:
                # Verify signature
                if msg.sender_pubkey_hex and msg.channel_id in self.channel_verify_keys:
                    vk_map = self.channel_verify_keys[msg.channel_id]
                    if msg.sender_pubkey_hex in vk_map:
                        vk = vk_map[msg.sender_pubkey_hex]
                        decrypted_payload = ChannelCrypto.verify_public_message(
                            msg.payload, vk
                        )
                else:
                    # No verify key registered, extract payload without verification
                    if len(msg.payload) >= 64:
                        decrypted_payload = msg.payload[64:]

        elif msg.channel_type == ChannelType.PRIVATE:
            if msg.channel_id in self.subscriptions:
                key = self.subscriptions[msg.channel_id]
                if key:
                    decrypted_payload = ChannelCrypto.decrypt_private_channel(
                        msg.payload, key
                    )

        elif msg.channel_type == ChannelType.DIRECT:
            self._process_onion_or_direct(msg)
            return  # Handled separately

        if decrypted_payload is not None:
            with self._received_lock:
                self.received_payloads.append((msg.channel_id, decrypted_payload))
            for cb in self._on_message_callbacks:
                cb(msg, decrypted_payload)

    def _process_onion_or_direct(self, msg: Message):
        """Process a direct/onion message."""
        payload = msg.payload

        # Check if this is an onion packet (constant size)
        if len(payload) == PACKET_SIZE:
            try:
                is_final, next_hop_bytes, inner = OnionRouter.peel_layer(
                    payload, self.identity.encryption_private_key
                )

                if is_final:
                    # We're the final destination in the onion route.
                    # inner contains the DM ciphertext (SealedBox) with padding after it.
                    # Try to decrypt the DM.
                    plaintext = self._try_decrypt_dm_from_padded(
                        inner, self.identity.encryption_private_key
                    )
                    if plaintext is not None:
                        with self._received_lock:
                            self.received_payloads.append(("direct", plaintext))
                        for cb in self._on_message_callbacks:
                            cb(msg, plaintext)
                    else:
                        # Couldn't decrypt - just a relay that happened to be at final position
                        self.relay_log.append(payload)
                else:
                    # We're an intermediate relay. Log and forward.
                    self.relay_log.append(payload)
                    next_hop_hex = next_hop_bytes.hex()

                    # Find peer with matching public key
                    target_address = self._find_peer_by_pubkey(next_hop_hex)

                    if target_address and self._send_fn:
                        fwd_msg = msg.copy()
                        fwd_msg.payload = inner  # Already PACKET_SIZE
                        self._send_fn(target_address, fwd_msg.to_dict())
                return
            except Exception:
                # Not a valid onion packet for us
                pass

        # Not an onion packet — try direct SealedBox decryption
        try:
            plaintext = ChannelCrypto.decrypt_direct_message(
                payload, self.identity.encryption_private_key
            )
            if plaintext is not None:
                with self._received_lock:
                    self.received_payloads.append(("direct", plaintext))
                for cb in self._on_message_callbacks:
                    msg_copy = msg
                    cb(msg_copy, plaintext)
        except Exception:
            pass

    def _try_decrypt_dm_from_padded(self, padded_data: bytes, private_key: PrivateKey) -> Optional[bytes]:
        """
        Try to decrypt a SealedBox-encrypted DM from padded data.
        The DM ciphertext is at the start of padded_data, followed by random padding.
        SealedBox format: [32 bytes ephemeral pubkey][16 bytes MAC][ciphertext]
        Total overhead = 48 bytes.
        
        We try various lengths starting from reasonable maximum down to minimum.
        """
        max_try = min(len(padded_data), 4096 + 48)
        min_try = 48  # SealedBox minimum (overhead only, 0 byte plaintext)
        
        # Binary-search-like approach: try common sizes first
        # Most DMs are small, so try small sizes first for efficiency
        for try_len in range(min_try, max_try + 1):
            try:
                result = ChannelCrypto.decrypt_direct_message(
                    padded_data[:try_len], private_key
                )
                if result is not None:
                    return result
            except Exception:
                continue
        return None

    def _find_peer_by_pubkey(self, pubkey_hex: str) -> Optional[str]:
        """Find a peer's address by their public key hex."""
        with self._peers_lock:
            for addr, peer in self.peers.items():
                if peer.public_key_hex == pubkey_hex:
                    return addr
        return None

    def _gossip(self, msg: Message, exclude: Optional[str] = None):
        """Forward message to k random peers."""
        if msg.hops_remaining <= 0:
            return
        if not self._send_fn:
            return

        targets = self.get_random_peers(self.fanout, exclude=exclude)
        for addr in targets:
            self._send_fn(addr, msg.to_dict())

    # --- TTL Enforcement ---

    def purge_expired(self):
        """Remove expired messages from the message store."""
        with self._store_lock:
            self.message_store = [
                m for m in self.message_store
                if not m.is_expired()
            ]

    # --- Peer Discovery / Bootstrap ---

    def bootstrap(self, bootstrap_addresses: List[str]):
        """Connect to bootstrap nodes to discover peers."""
        self.bootstrap_addresses = bootstrap_addresses
        if not self._send_fn:
            return

        announcement = PeerAnnouncement(
            public_key_hex=self.public_key_hex,
            address=self.address,
        )
        discover_msg = {
            "type": "peer_discover",
            "announcement": announcement.to_dict(),
        }
        for addr in bootstrap_addresses:
            self._send_fn(addr, discover_msg)

    def handle_peer_discovery(self, data: dict, from_address: Optional[str] = None):
        """Handle a peer discovery message."""
        msg_type = data.get("type", "")

        if msg_type == "peer_discover":
            ann = PeerAnnouncement.from_dict(data["announcement"])
            try:
                enc_pk = PublicKey(bytes.fromhex(ann.public_key_hex))
                self.add_peer(ann.address, enc_pk)
            except Exception:
                pass

            if self._send_fn:
                peer_list = []
                with self._peers_lock:
                    for addr, peer in self.peers.items():
                        peer_list.append({
                            "public_key_hex": peer.public_key_hex,
                            "address": addr,
                        })
                peer_list.append({
                    "public_key_hex": self.public_key_hex,
                    "address": self.address,
                })
                response = {
                    "type": "peer_list",
                    "peers": peer_list,
                }
                self._send_fn(ann.address, response)

        elif msg_type == "peer_list":
            peers = data.get("peers", [])
            for p in peers:
                if p["address"] != self.address:
                    try:
                        enc_pk = PublicKey(bytes.fromhex(p["public_key_hex"]))
                        self.add_peer(p["address"], enc_pk)
                    except Exception:
                        pass

        elif msg_type == "peer_announce":
            ann = PeerAnnouncement.from_dict(data["announcement"])
            try:
                enc_pk = PublicKey(bytes.fromhex(ann.public_key_hex))
                self.add_peer(ann.address, enc_pk)
            except Exception:
                pass

    def announce_to_peers(self):
        """Announce our identity to all known peers via gossip."""
        if not self._send_fn:
            return
        announcement = PeerAnnouncement(
            public_key_hex=self.public_key_hex,
            address=self.address,
        )
        msg = {
            "type": "peer_announce",
            "announcement": announcement.to_dict(),
        }
        with self._peers_lock:
            addrs = list(self.peers.keys())
        for addr in addrs:
            if addr != self.address:
                self._send_fn(addr, msg)

    # --- Network message handler ---

    def handle_network_message(self, data: dict, from_address: Optional[str] = None):
        """
        Main entry point for all network messages.
        Dispatches to appropriate handler.
        """
        if not self.running:
            return

        msg_type = data.get("type")
        if msg_type in ("peer_discover", "peer_list", "peer_announce"):
            self.handle_peer_discovery(data, from_address)
        elif "message_id" in data:
            self.receive_message(data, from_address)
        else:
            pass
