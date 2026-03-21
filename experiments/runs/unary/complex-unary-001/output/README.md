# HermesP2P

An ephemeral peer-to-peer messaging system with gossip propagation, TTL-based message expiry, onion routing, and channel-based messaging. No node accumulates a complete message log.

## Architecture

```
hermes_p2p/
├── __init__.py          # Package exports
├── bloom_filter.py      # Bounded Bloom filter with TTL-expiring segments
├── crypto.py            # Ed25519/X25519 keys, AES-GCM, onion encryption
├── message.py           # Message envelope, channel types, creation helpers
├── network.py           # Simulated network for testing (configurable latency/loss)
└── node.py              # Node: identity, peers, gossip, dedup, routing
tests/
└── test_hermes.py       # 45 tests covering all 9 required properties
```

## Core Design

### Message Envelope
Every message carries `(message_id, created_ts, ttl_seconds, channel_id, payload)` plus a `hop_count` for gossip propagation control. The `message_id` is a random UUID for deduplication.

### Gossip Propagation
When a node receives a message, it forwards to **k random peers** (configurable fanout, default k=3). Each relay decrements the hop counter. Messages with `hop_count=0` are delivered locally but not forwarded.

### Deduplication
Nodes track seen `message_id`s using a **TTL-expiring segmented Bloom filter** — a rotating set of fixed-capacity Bloom filters, each covering a time window. Old segments are pruned, keeping memory bounded regardless of message volume.

### TTL Enforcement
Nodes silently drop messages where `now > created_ts + ttl_seconds + 30s` (30-second clock skew tolerance).

### Onion Routing
For a 3-hop route through relays R1→R2→R3→recipient:
1. Encrypt payload for recipient (innermost layer)
2. Wrap in encryption for R3 (next_hop = recipient)
3. Wrap for R2 (next_hop = R3)
4. Wrap for R1 (next_hop = R2)

Each relay decrypts one layer using X25519 ECDH + HKDF + AES-256-GCM, discovers only the next hop, and forwards. **No relay learns both sender and recipient.**

### Constant-Size Packets
All onion packets are padded to a fixed 4096-byte size. After each relay peels its layer, the packet is re-padded, so packet size reveals nothing about position in the route.

### Channel Types
- **Public**: Messages signed with Ed25519; any subscriber verifies the signature
- **Private**: Symmetric-key encrypted (AES-256-GCM); only members with the channel key can read
- **Direct**: Onion-routed to a specific recipient's public key

### Node Identity
Each node has an Ed25519 keypair (signing/identity) and an X25519 keypair (encryption/key-exchange).

### Peer Discovery
Bootstrap mechanism: new nodes connect to well-known addresses, receive peer announcements, and learn about the network. Existing nodes share their peer tables with newcomers.

### Simulated Network
The `SimulatedNetwork` class provides in-process message delivery with:
- Configurable latency (uniform random range)
- Configurable packet loss rate
- Network partitioning and recovery
- Synchronous or async delivery modes

## Running Tests

```bash
pip install cryptography pytest
python -m pytest tests/test_hermes.py -v
```

## Test Coverage

| # | Property | Tests |
|---|----------|-------|
| a | Message reaches all subscribers within TTL | 3 tests |
| b | Expired messages are dropped | 3 tests |
| c | Duplicate messages are deduplicated | 3 tests |
| d | Onion routing: relays cannot read payload | 6 tests |
| e | Bloom filter bounds memory usage | 5 tests |
| f | Public channel signature verification | 5 tests |
| g | Private channel unreadable without key | 5 tests |
| h | New node bootstraps and receives messages | 3 tests |
| i | Network partition and recovery | 3 tests |
| — | Structural & edge cases | 9 tests |

**Total: 45 tests, all passing.**

## Dependencies

- `cryptography` — Ed25519, X25519, AES-GCM, HKDF
- `pytest` — test runner (dev only)
- Python 3.9+
