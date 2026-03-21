# HermesP2P

An ephemeral peer-to-peer messaging system where messages propagate via gossip,
each node retains messages only for their TTL, and no node accumulates a complete
message log. Messages are onion-routed so no relay knows both sender and recipient.

## Features

- **Gossip propagation** with configurable fanout
- **Onion routing** with constant-size packets
- **TTL enforcement** with clock skew tolerance
- **Deduplication** via bounded Bloom filter
- **Three channel types**: Public, Private, Direct Messages
- **Ed25519 node identity**
- **Peer discovery** via bootstrap + gossip
- **Simulated network** for testing

## Installation

```bash
pip install -e .
```

## Testing

```bash
python -m pytest tests/ -v
```
