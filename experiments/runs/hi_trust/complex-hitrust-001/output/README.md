# HermesP2P

An ephemeral peer-to-peer messaging system where messages propagate via gossip,
each node retains messages only for their TTL, and no node accumulates a complete
message log. Messages are onion-routed so no relay knows both sender and recipient.

## Architecture

```
hermes_p2p/
├── __init__.py          # Package exports
├── bloom_filter.py      # Bounded deduplication (BloomFilter, TTLExpiringSet)
├── crypto.py            # Onion routing + channel encryption
├── message.py           # Message envelope and channel types
├── network.py           # Simulated network for testing
└── node.py              # Node implementation (identity, gossip, routing)

tests/
└── test_hermes.py       # Comprehensive test suite (26 tests)
```

## Core Components

### Message Envelope
Each message has: `message_id`, `created_ts`, `ttl_seconds`, `channel_id`, `payload`, `hops_remaining`, and `channel_type`. Messages are serialized to dicts for network transport.

### Gossip Propagation
When a node receives a new message, it forwards to `k` random peers (configurable fanout, default k=3). The hop counter is decremented at each relay; messages with `hops=0` are not forwarded further.

### Deduplication (Bounded)
Nodes use a `TTLExpiringSet` backed by a `BloomFilter` to track seen `message_id`s. The bloom filter provides O(1) lookups with bounded memory. Entries expire after their TTL, preventing unbounded growth. The bloom filter bit array is fixed-size regardless of how many items are inserted.

### TTL Enforcement
Messages older than their TTL (plus a ±30 second clock skew tolerance) are silently dropped on receipt. The `purge_expired()` method cleans the local message store.

### Onion Routing (Sphinx-inspired, Constant-Size Packets)

**Design:**
- Packets are always `PACKET_SIZE` bytes (4293 bytes).
- Structure: `[32 bytes: ephemeral pubkey] + [BODY_SIZE bytes: encrypted body]`
- Each relay computes a shared secret via ECDH with the ephemeral key, derives a stream cipher key, and XOR-decrypts the body.
- The decrypted body starts with a 65-byte header: `[1 byte: is_final][32 bytes: next_hop_pubkey][32 bytes: next_ephemeral_pubkey]`
- The relay strips its header (shifts body left by 65 bytes), appends 65 bytes of random padding, and forwards with the next ephemeral key.
- **Result:** Every relay sees exactly the same packet size. No position information leaks.

**Building (inside-out):**
1. Sender constructs the innermost layer (recipient's payload + padding).
2. Encrypts with stream cipher using shared secret with recipient.
3. Wraps each relay's header + encrypted body, encrypts with that relay's stream key.
4. Prepends the first ephemeral public key.

**Privacy guarantees:**
- Each relay only knows its predecessor and successor.
- No relay can read the payload (encrypted for recipient with SealedBox).
- Constant packet size prevents traffic analysis based on position.

### Channel Types

| Type | Encryption | Verification |
|------|-----------|-------------|
| **Public** | None (plaintext signed) | Ed25519 signature verified by any subscriber |
| **Private** | NaCl SecretBox (symmetric) | Only members with the channel key can decrypt |
| **Direct** | NaCl SealedBox + Onion routing | Only recipient's private key can decrypt |

### Node Identity
Each node has:
- **Ed25519 keypair** for signing (public channel messages, identity)
- **X25519 keypair** for encryption (onion routing, direct messages)
- A network address (string identifier)

### Peer Discovery
- **Bootstrap:** New nodes connect to well-known addresses and receive the current peer list.
- **Gossip:** Peer announcements propagate through the network, letting nodes discover each other organically.

### Simulated Network
The `SimulatedNetwork` class provides in-process message delivery with:
- Configurable latency (uniform random in [min, max])
- Configurable packet loss rate
- Network partitioning (groups of nodes that can/cannot communicate)
- Full mesh setup helper for testing

## Quick Start

```python
from hermes_p2p import SimulatedNetwork, Node, ChannelCrypto

# Create network and nodes
net = SimulatedNetwork()
nodes = [Node(address=f"node_{i}") for i in range(5)]
for node in nodes:
    net.register_node(node)
net.setup_full_mesh()

# Public channel messaging
channel = "general"
for node in nodes:
    node.subscribe(channel)
    node.add_channel_verify_key(channel, nodes[0].identity.verify_key_hex, nodes[0].identity.verify_key)

nodes[0].send_public_message(channel, b"Hello everyone!")

# Private channel messaging
channel_key = ChannelCrypto.generate_channel_key()
for node in nodes:
    node.subscribe("secret", channel_key)
nodes[0].send_private_message("secret", b"Classified info", channel_key)

# Direct message with onion routing
route = [nodes[1].identity.encryption_public_key, nodes[2].identity.encryption_public_key]
nodes[0].send_direct_message(
    recipient_pubkey=nodes[4].identity.encryption_public_key,
    plaintext=b"Private DM",
    route=route,
)
```

## Running Tests

```bash
python3 -m pytest tests/test_hermes.py -v
```

### Test Coverage

| # | Test | Property |
|---|------|----------|
| a | `test_public_message_reaches_all_subscribers` | Messages reach all subscribers within TTL |
| a | `test_private_message_reaches_subscribers` | Private messages reach key holders |
| b | `test_expired_message_dropped` | Expired messages are dropped silently |
| b | `test_ttl_enforcement_purge` | Store purges expired messages |
| c | `test_duplicate_message_ignored` | Duplicate messages are deduplicated |
| c | `test_dedup_bloom_filter` | Bloom filter membership testing works |
| c | `test_ttl_expiring_set` | TTL expiring set expires old entries |
| d | `test_onion_routing_privacy` | Intermediate relays cannot read payload |
| d | `test_constant_size_packets` | All onion layers maintain constant size |
| d | `test_onion_end_to_end_via_network` | Onion DMs delivered through simulated network |
| e | `test_bloom_filter_bounded_memory` | Bloom filter memory is bounded |
| e | `test_ttl_expiring_set_bounded` | TTL set doesn't grow unboundedly |
| f | `test_valid_signature_accepted` | Valid Ed25519 signatures accepted |
| f | `test_invalid_signature_rejected` | Wrong key signatures rejected |
| f | `test_tampered_message_rejected` | Tampered messages rejected |
| f | `test_public_channel_e2e_verification` | E2E signature verification works |
| g | `test_unreadable_without_key` | Private messages unreadable without key |
| g | `test_non_member_cannot_read` | Non-members can't decrypt private channels |
| h | `test_new_node_discovers_peers` | Bootstrap discovers existing peers |
| h | `test_bootstrapped_node_receives_messages` | Bootstrapped node receives gossip |
| i | `test_partition_prevents_delivery` | Messages don't cross partitions |
| i | `test_partition_recovery_no_leak` | Partition messages don't leak after healing |

## Dependencies

- **PyNaCl** (libsodium bindings): Ed25519 signing, X25519 key exchange, SealedBox, SecretBox
- **Python 3.8+**: dataclasses, typing, hashlib, os, time, struct, threading

## Design Decisions

1. **Stream cipher for onion body**: Instead of nesting SealedBox (which changes packet size per layer), we use XOR-based stream encryption with per-hop ECDH shared secrets. This naturally maintains constant packet size.

2. **Per-hop ephemeral keys**: Each hop has its own ephemeral keypair embedded in the header of the previous layer. This avoids the need for EC point blinding (which NaCl doesn't expose for Curve25519).

3. **SealedBox for DM payload**: The actual message content is encrypted with SealedBox for the recipient, providing forward secrecy independent of the onion routing.

4. **TTLExpiringSet for dedup**: Combines a bloom filter (bounded memory, fast lookups) with a timestamp dict (precise TTL tracking). The timestamp dict is bounded by capacity with LRU eviction.
