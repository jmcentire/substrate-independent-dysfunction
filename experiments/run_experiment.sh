#!/bin/bash
# Run the full 4-architecture × 2-task experiment.
#
# Usage:
#   source ~/.profile  # ensure ANTHROPIC_API_KEY is set
#   cd /Users/jmcentire/Personal/ASD/dysfunction
#   ./experiments/run_experiment.sh [--simple-only | --complex-only | --run N]
#
# Each run is independent. If a run fails, subsequent runs still execute.
# All output goes to experiments/runs/{run_id}/

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
RUNS_DIR="${SCRIPT_DIR}/runs"

# --- Canonical Task Prompts ---
# Stored here (not in separate files) so this script is the single source of truth.

read -r -d '' SIMPLE_TASK << 'TASK_EOF'
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
TASK_EOF

read -r -d '' COMPLEX_TASK << 'TASK_EOF'
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
TASK_EOF

# --- Software Version Snapshot ---
snapshot_versions() {
    echo "=== Software Versions ==="
    echo "Date: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "Python: $(python3 --version 2>&1)"
    echo "anthropic: $(python3 -c 'import anthropic; print(anthropic.__version__)' 2>/dev/null || echo 'not installed')"
    echo "emergence: $(python3 -c 'import emergence; print("installed")' 2>/dev/null || echo 'not installed')"
    echo "swarm: $(python3 -c 'import swarm; print("installed")' 2>/dev/null || echo 'not installed')"
    echo "OS: $(uname -srm)"
    echo "========================="
}

# --- Run Definitions ---
# Format: run_number|architecture|task_complexity|budget|run_id
RUNS=(
    "1|unary|simple|10.0|simple-unary-001"
    "2|hi_trust|simple|10.0|simple-hitrust-001"
    "3|org_swarm|simple|10.0|simple-orgswarm-001"
    "4|emergence|simple|10.0|simple-emergence-001"
    "5|unary|complex|25.0|complex-unary-001"
    "6|hi_trust|complex|25.0|complex-hitrust-001"
    "7|org_swarm|complex|25.0|complex-orgswarm-001"
    "8|emergence|complex|25.0|complex-emergence-001"
)

# --- Argument Parsing ---
RUN_FILTER=""
TASK_FILTER=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --simple-only) TASK_FILTER="simple"; shift ;;
        --complex-only) TASK_FILTER="complex"; shift ;;
        --run) RUN_FILTER="$2"; shift 2 ;;
        *) echo "Unknown arg: $1. Usage: $0 [--simple-only|--complex-only|--run N]"; exit 1 ;;
    esac
done

# --- Pre-flight ---
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  4-Architecture Experiment — Substrate-Independent Dysfunction  ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Check API key
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "[FATAL] ANTHROPIC_API_KEY not set. Run: source ~/.profile"
    exit 1
fi
echo "[OK] ANTHROPIC_API_KEY is set"

snapshot_versions
echo ""

mkdir -p "$RUNS_DIR"

# Save versions to file
snapshot_versions > "${RUNS_DIR}/versions.txt"

# --- Execute Runs ---
for run_def in "${RUNS[@]}"; do
    IFS='|' read -r run_num arch task_complexity budget run_id <<< "$run_def"

    # Apply filters
    if [[ -n "$RUN_FILTER" && "$run_num" != "$RUN_FILTER" ]]; then continue; fi
    if [[ -n "$TASK_FILTER" && "$task_complexity" != "$TASK_FILTER" ]]; then continue; fi

    # Select task prompt
    if [[ "$task_complexity" == "simple" ]]; then
        TASK="$SIMPLE_TASK"
    else
        TASK="$COMPLEX_TASK"
    fi

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Run $run_num/8: $arch | $task_complexity | \$$budget | $run_id"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"

    OUTPUT_DIR="${RUNS_DIR}/${arch}"

    case "$arch" in
        unary)
            python3 "${SCRIPT_DIR}/unary/run.py" \
                --task "$TASK" \
                --budget "$budget" \
                --output "$OUTPUT_DIR" \
                --run-id "$run_id" \
                2>&1 | tee "${RUNS_DIR}/${run_id}.log"
            ;;
        hi_trust)
            python3 "${SCRIPT_DIR}/hi_trust/run.py" \
                --task "$TASK" \
                --budget "$budget" \
                --output "$OUTPUT_DIR" \
                --run-id "$run_id" \
                2>&1 | tee "${RUNS_DIR}/${run_id}.log"
            ;;
        org_swarm)
            python3 "${SCRIPT_DIR}/org_swarm/run.py" \
                --task "$TASK" \
                --budget "$budget" \
                --output "$OUTPUT_DIR" \
                --run-id "$run_id" \
                2>&1 | tee "${RUNS_DIR}/${run_id}.log"
            ;;
        emergence)
            python3 "${SCRIPT_DIR}/emergence/run.py" \
                --task "$TASK" \
                --budget "$budget" \
                --output "$OUTPUT_DIR" \
                --run-id "$run_id" \
                --num-agents 5 \
                2>&1 | tee "${RUNS_DIR}/${run_id}.log"
            ;;
    esac

    EXIT_CODE=$?
    echo "  Finished: $(date -u +"%Y-%m-%dT%H:%M:%SZ") (exit=$EXIT_CODE)"
done

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Experiment complete. Results in: $RUNS_DIR"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# --- Summary ---
echo "Run summaries:"
for run_def in "${RUNS[@]}"; do
    IFS='|' read -r run_num arch task_complexity budget run_id <<< "$run_def"
    if [[ -n "$RUN_FILTER" && "$run_num" != "$RUN_FILTER" ]]; then continue; fi
    if [[ -n "$TASK_FILTER" && "$task_complexity" != "$TASK_FILTER" ]]; then continue; fi

    RESULT_FILE="${RUNS_DIR}/${arch}/${run_id}/result.json"
    if [[ -f "$RESULT_FILE" ]]; then
        SPENT=$(python3 -c "import json; r=json.load(open('$RESULT_FILE')); print(r.get('budget',{}).get('spent_usd','?'))" 2>/dev/null || echo "?")
        FILES=$(python3 -c "import json; r=json.load(open('$RESULT_FILE')); print(len(r.get('files_produced',[])))" 2>/dev/null || echo "?")
        WALL=$(python3 -c "import json; r=json.load(open('$RESULT_FILE')); print(r.get('wall_seconds','?'))" 2>/dev/null || echo "?")
        ERR=$(python3 -c "import json; r=json.load(open('$RESULT_FILE')); e=r.get('error'); print('OK' if e is None else e[:60])" 2>/dev/null || echo "?")
        printf "  %-30s  cost=\$%-8s  files=%-4s  time=%-6ss  %s\n" "$run_id" "$SPENT" "$FILES" "$WALL" "$ERR"
    else
        printf "  %-30s  (no result.json)\n" "$run_id"
    fi
done
