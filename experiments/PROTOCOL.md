# Experiment Protocol: 4-Architecture Comparison
# Version 1.0 — Pre-registered before first run

## Design

**Type:** 2×4 factorial (4 architectures × 2 task complexities)
**Independent variable:** Coordination topology
**Dependent variables:** Task completion, cost, token usage, wall time, coordination overhead, dysfunction indicators
**Control:** All conditions use the same model, same task prompt, same budget cap

---

## Task Prompts (Canonical — given to all architectures identically)

### Simple Task

```
Build the Anonymous Identity system for a privacy-preserving internet architecture.

A user authenticates once to an SSO. The SSO never learns which sites the user visits. Sites never learn the user's real identity. Two sites cannot correlate that they're serving the same user.

Properties that must hold:

1. Credential hash: The client computes hash(username || password) locally. Only the hash is transmitted to the SSO. The SSO never sees the password.
2. Site-specific token derivation: For each site, the client derives token = hash(username || site_id || user_id || user_salt) entirely client-side. The SSO provides user_id and user_salt during authentication but never learns which site_id is being used.
3. Unlinkability: Tokens for the same user on different sites are computationally unlinkable. No party — not the SSO, not either site, not an eavesdropper — can determine that token_A (for site A) and token_B (for site B) belong to the same user, without knowing the user's credentials.
4. Token construction: The client signs a token T containing (verification_hash, site_id, timestamp, proof_of_human_score) with Ed25519. The site verifies the signature and uses verification_hash as the user's persistent site-local identifier.
5. Routing key derivation: routing_key = hash(username || "routing" || site_id || user_id || user_salt). Sites send messages to the SSO addressed to a routing key. The SSO resolves the routing key to the user's contact info, delivers the message, and discards the plaintext. The site never learns the user's contact info.
6. SSO protocol: Exposes authenticate(credential_hash) → (user_id, user_salt, session), register(credential_hash, encrypted_contact) → user_id, and route_message(routing_key, encrypted_payload) → delivered.
7. Tests must pass: (a) Same user, two sites → tokens are different and unlinkable. (b) Different users, same site → tokens don't collide. (c) Token signature verifies. (d) Routing key correctly resolves. (e) SSO cannot derive site-specific tokens (doesn't know site_id). (f) Site cannot derive tokens for other sites (doesn't know user_salt).
```

### Complex Task

```
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
```

---

## Acceptance Criteria (NOT given to agents — evaluated post-run, blinded)

### Simple Task (Anonymous Identity) — 7 criteria

| # | Criterion | Pass condition |
|---|-----------|---------------|
| S1 | Credential hash | Client hashes locally; SSO never sees password |
| S2 | Site-specific tokens | Token derivation uses site_id; different per site |
| S3 | Unlinkability | Two tokens from same user on different sites share no common value |
| S4 | Token signing | Ed25519 signature on token; verifiable by site |
| S5 | Routing key | Separate derivation path; SSO can resolve without learning site_id |
| S6 | SSO protocol | Register + authenticate + route endpoints exist and function |
| S7 | Tests exist and pass | At least 4 of the 6 specified test cases pass |

### Complex Task (HermesP2P) — 11 criteria

| # | Criterion | Pass condition |
|---|-----------|---------------|
| C1 | Message envelope | Structured envelope with message_id, TTL, channel_id, payload |
| C2 | Gossip propagation | Messages forwarded to k peers; hop counter decremented |
| C3 | Deduplication | Bounded dedup store (Bloom filter or TTL set); duplicates dropped |
| C4 | TTL enforcement | Expired messages dropped; ±30s clock tolerance |
| C5 | Onion routing | Layered encryption; each relay peels one layer |
| C6 | Constant-size packets | Padding to fixed size after each peel |
| C7 | Channel types | At least 2 of 3 channel types (public, private, direct) implemented |
| C8 | Node identity | Ed25519 keypair per node |
| C9 | Peer discovery | Bootstrap mechanism; new node can join |
| C10 | Simulated network | In-process network simulation for testing |
| C11 | Tests exist and pass | At least 5 of the 9 specified test cases pass |

---

## Known Confounds (Documented Pre-Run)

### 1. Tool Surface Asymmetry

| Architecture | Tool mechanism | Capabilities |
|---|---|---|
| Unary | 4 sandboxed tools via `beta.messages.tool_runner()` | write_file, read_file, list_directory, run_command (30s timeout) |
| Hi-Trust | 4 sandboxed tools via `beta.messages.tool_runner()` | write_file, read_file, list_directory, run_command (30s timeout) |
| Org Swarm | Structured output via `tool_choice` (no interactive tools) | Produces FileAction JSON; code applied by framework |
| Emergence | Structured output via `tool_choice` (no interactive tools) | Produces FileAction JSON; code applied by framework |

**Unary and Hi-Trust share identical tool surfaces.** Both use the Anthropic API directly with the same 4 sandboxed tools. Org Swarm and Emergence use structured output (no interactive tools) — they produce code blind and cannot test what they write. This advantages Unary/Hi-Trust for correctness but is inherent to the architecture (interactive tool-use vs. structured extraction). Documented as a limitation, not corrected.

**Note:** An earlier version of this protocol used the Claude Code CLI for Unary, which would have introduced model inconsistency (Claude Code routes to Sonnet, not Opus 4.6). This was corrected before any runs. All four architectures now use the same model via the same API.

### 2. Context Window Differences

| Architecture | Context per agent |
|---|---|
| Unary | Single ~200K window, accumulates full history |
| Hi-Trust | Fresh context per agent node; parent context unavailable to children |
| Org Swarm | Fresh context per pipeline stage; prior stage output summarized |
| Emergence | Fresh context per agent iteration; shared environment is the only memory |

**Impact:** Unary benefits from full context but risks context degradation. Multi-agent architectures get fresh context but lose cross-agent information. This is the core architectural difference being tested.

### 3. System Prompt Variation

Each architecture necessarily has different system prompts (leaf execution vs. stigmergic coordination vs. pipeline stage). Prompt content is driven by architectural requirements, not experimenter choice. All prompts are recorded in the codebase and can be audited.

### 4. Swarm Perturbation Engine

The Org Swarm has an internal annealing/perturbation system that adjusts "temperature" (exploration vs. exploitation) across stages. This is an architectural feature of the swarm, not a confound we introduce, but it means the Org Swarm has adaptive behavior the other architectures lack.

### 5. Budget as Ceiling

Budget caps ($10 simple, $25 complex) may prevent architectures with high coordination overhead from completing. This is intentional — coordination overhead IS a dependent variable. If an architecture burns its budget on coordination before producing output, that's a finding.

---

## Controls

| Control | How enforced |
|---|---|
| Same model | All runners default `claude-opus-4-6`; org_swarm stage_models overridden to Opus on all 11 stages; org_swarm stage_backends overridden to `anthropic` on all stages |
| Same task prompt | Canonical prompts stored in this file; passed via `--task` flag from run script |
| Same budget | `--budget 10.0` (simple) / `--budget 25.0` (complex) |
| No human intervention | Org swarm: `io_mode: autonomous`. All others: no interactive input by design |
| API temperature | None set by any runner → API default (1.0) for all |
| Fresh state | Each run creates a new directory; no prior state carried over |
| Blinded evaluation | Output directories named by run_id, not architecture; evaluator receives randomized set |

---

## Run Order

Sequential, simple then complex, architectures in fixed order.

| Run | Architecture | Task | Budget | Run ID |
|-----|-------------|------|--------|--------|
| 1 | Unary | Simple | $10 | simple-unary-001 |
| 2 | Hi-Trust | Simple | $10 | simple-hitrust-001 |
| 3 | Org Swarm | Simple | $10 | simple-orgswarm-001 |
| 4 | Emergence | Simple | $10 | simple-emergence-001 |
| 5 | Unary | Complex | $25 | complex-unary-001 |
| 6 | Hi-Trust | Complex | $25 | complex-hitrust-001 |
| 7 | Org Swarm | Complex | $25 | complex-orgswarm-001 |
| 8 | Emergence | Complex | $25 | complex-emergence-001 |

**Run order is fixed, not randomized.** With N=1 per cell, randomization provides no statistical benefit. Order is documented for transparency. If replicated (N>1), order should be randomized.

---

## Blinded Evaluation Procedure

1. After all 8 runs complete, copy each `output/` directory to a numbered evaluation directory (eval-01 through eval-08) using a random permutation.
2. Record the mapping (run_id → eval_id) in a sealed file not opened until evaluation is complete.
3. Evaluate each eval-XX against the acceptance criteria table above.
4. Score: count of criteria met (0-7 for simple, 0-11 for complex).
5. Open the sealed mapping. Record scores by architecture.

---

## Software Versions (Frozen)

Record at run time:
- Python version
- anthropic SDK version
- emergence package version (git hash)
- swarm package version (git hash)
- claude CLI version
- OS version

---

## Hypotheses (from experiment.md, restated for pre-registration)

**H1 (Architectural):** Dysfunction patterns correlate with coordination topology, not agent capability.

**H2 (Hierarchy-specific):** Predicted dysfunction ordering: Org Swarm > Hi-Trust > Emergence > Unary.

**H3 (Task-complexity interaction):** Coordination overhead penalty is proportionally larger for simple tasks.

---

## Budget

| | Simple ($10 × 4) | Complex ($25 × 4) | Retries | Total |
|---|---|---|---|---|
| Planned | $40 | $100 | ~$60 | ~$200 |

---

## What Gets Reported

ALL runs, including failures. No cherry-picking. For each run:
- Completion status (success / partial / budget_exceeded / error)
- Acceptance criteria score
- Cost and token usage
- Wall time
- Files produced
- Full log.jsonl (for post-hoc dysfunction analysis)

Failures and budget exhaustion are data, not errors to retry silently.
