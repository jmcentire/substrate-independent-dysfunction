#!/bin/bash
# Round 2 Experiment: Prepare work directory for an architecture run.
#
# Usage: ./prepare.sh <architecture> <run_id>
#   e.g.: ./prepare.sh unary r2-unary-001
#
# Creates the run directory with reference material and oracle answers
# pre-populated in the agent's sandbox.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ARCH="${1:?Usage: $0 <architecture> <run_id>}"
RUN_ID="${2:?Usage: $0 <architecture> <run_id>}"

# Determine base output directory per architecture
case "$ARCH" in
  unary)      BASE="${SCRIPT_DIR}/../runs/unary" ;;
  hi_trust)   BASE="${SCRIPT_DIR}/../runs/hi_trust" ;;
  emergence)  BASE="${SCRIPT_DIR}/../runs/emergence" ;;
  org_swarm)  BASE="${SCRIPT_DIR}/../runs/org_swarm" ;;
  *)          echo "Unknown architecture: $ARCH"; exit 1 ;;
esac

RUN_DIR="${BASE}/${RUN_ID}"
WORK_DIR="${RUN_DIR}/output"

echo "Preparing ${ARCH} run: ${RUN_ID}"
echo "  Run dir:  ${RUN_DIR}"
echo "  Work dir: ${WORK_DIR}"

# Create directories
mkdir -p "${WORK_DIR}/reference"
mkdir -p "${WORK_DIR}/oracle"

# Copy reference material into the sandbox
cp -r "${SCRIPT_DIR}/reference/"* "${WORK_DIR}/reference/"

# Copy oracle pre-seeded answers
if [ -f "${SCRIPT_DIR}/reference/oracle_answers.jsonl" ]; then
  cp "${SCRIPT_DIR}/reference/oracle_answers.jsonl" "${WORK_DIR}/oracle/answers.jsonl"
fi

# Copy task prompt
cp "${SCRIPT_DIR}/task.md" "${RUN_DIR}/task.txt"

echo "Done. Reference material and oracle answers in place."
echo ""
echo "To run:"
case "$ARCH" in
  unary)
    echo "  python experiments/unary/run.py \\"
    echo "    --task-file experiments/round2/task.md \\"
    echo "    --budget 50.0 --output experiments/runs/unary --run-id ${RUN_ID}"
    ;;
  hi_trust)
    echo "  python experiments/hi_trust/run.py \\"
    echo "    --task-file experiments/round2/task.md \\"
    echo "    --budget 50.0 --output experiments/runs/hi_trust --run-id ${RUN_ID}"
    ;;
  emergence)
    echo "  python experiments/emergence/run.py \\"
    echo "    --task-file experiments/round2/task.md \\"
    echo "    --budget 50.0 --output experiments/runs/emergence --run-id ${RUN_ID}"
    ;;
  org_swarm)
    echo "  python experiments/org_swarm/run.py \\"
    echo "    --task-file experiments/round2/task.md \\"
    echo "    --budget 50.0 --output experiments/runs/org_swarm --run-id ${RUN_ID}"
    ;;
esac
