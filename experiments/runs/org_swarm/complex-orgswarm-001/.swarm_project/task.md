# Task

Build HermesP2P: an ephemeral peer-to-peer messaging system where messages propagate via gossip, each node retains messages only for their TTL, and no node accumulates a complete message log. Messages are onion-routed so no relay knows both sender and recipient.

Properties that must hold:

1. Message envelope: Each message has (message_id, created_ts, ttl_seconds, channel_id, payload). The payload is an onion-encrypted blob. message_id is a random unique identifier for deduplication.
2. Gossip propagation: When a node receives a message, it forwards to k random peers (configurable fanout, default k=3). Each relay decrements a hop counter. Messages with hop=0 are not forwarded.
3. Deduplication: Nodes track seen message_ids to prevent infinite circulation. The deduplication store must be bounded — use a Bloom filter or TTL-expiring set, not an unbounded hash set.
4. TTL enforcement: Nodes discard messages older than their TTL. A node that receives a message past its TTL drops it silently. Clock skew tolerance of ±30 seconds.
5. Onion routing: The sender constructs layered encryption. For a 3-hop route through relays R1→R2→R3→recipient: encrypt for recipient, wrap in encryption for R3, wrap for R2, wrap for R1. Each relay decrypts one layer, discovers the next hop, and forwards the remainder. No relay learns both sender and recipient.
6. Constant-size packets: After each relay decrypts its layer, the packet size must not reveal the relay's position in the route. Pad to a fixed size.
7. Three channel types: Public channels (messages are signed by sender, any subscriber can verify), Private channels (symmetric-key encrypted, only members with the channel key can read), Direct messages (ephemeral onion route to a specific recipient's public key).
8. Node identity: Each node has an Ed25519 keypair. Nodes advertise their public key and network address to peers.
9. Peer discovery: A bootstrap mechanism where new nodes connect to well-known addresses to discover peers. After bootstrap, nodes learn about other nodes through gossip (peer announcements).
10. Simulated network: For testing, implement a Network class that simulates message delivery between in-process nodes with configurable latency and packet loss. No real sockets required.
11. Tests must pass: (a) Message reaches all subscribers within TTL. (b) Expired messages are dropped. (c) Duplicate messages are deduplicated. (d) Onion routing: intermediate relays cannot read the payload. (e) Bloom filter bounds memory usage. (f) Public channel messages are verified by signature. (g) Private channel messages are unreadable without channel key. (h) New node successfully bootstraps and receives messages. (i) Network partition and recovery: messages sent during partition don't leak across after reconnection.
