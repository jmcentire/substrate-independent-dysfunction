# Data

Primary audit trail from the swarm pipeline deployment (Study 1).

## Files

- **state.json** — Complete execution state for the 89-stage run (Run 2). Contains per-stage metrics: token counts, costs, durations, pass/fail status, and classification results. All aggregate figures in the paper are derived from this file.
- **audit.jsonl** — Timestamped event log containing **two sequential runs** from the same project (see below).
- **swarm.yaml** — Pipeline configuration used for both runs.
- **task.md** — Task specification given to the swarm.

## Two-Run Structure of audit.jsonl

The audit log contains two runs from the same swarm project, separated by a restart:

| Entries | Timestamps | Description |
|---------|-----------|-------------|
| 0–14 | 2026-02-11T20:23 – 20:47 | **Run 1 (aborted).** 15 stages before manual restart. Contains the 28-second governance conflict (entries 13–14: project-level reject at 20:46:48, architect force-approve at 20:47:16). |
| 15–103 | 2026-02-11T21:00 – 2026-02-12T15:05 | **Run 2 (complete).** 89 stages. All aggregate metrics in the paper (cost, tokens, rejection rates, bikeshedding counts, verification theater) are derived from this run via `state.json`. |

The governance conflict cited in the paper (Section 5) occurred during Run 1. All other quantitative claims are from Run 2. Both runs used the same system, configuration, and task specification. The restart occurred because Run 1 was manually stopped after the escalation event.

## Verification

```python
import json

with open("state.json") as f:
    state = json.load(f)

# All aggregate claims verifiable from state.json (Run 2)
assert len(state["stages"]) == 89
assert round(state["total_cost_usd"], 2) == 57.43
assert state["total_tokens"] == 7_166_664

reviews = [s for s in state["stages"] if s["stage"] == "review"]
assert sum(1 for s in reviews if not s["passed"]) == 7  # 87% rejection rate
assert len(reviews) == 8

# Governance conflict verifiable from audit.jsonl (Run 1, entries 13-14)
with open("audit.jsonl") as f:
    audit = [json.loads(line) for line in f]

escalations = [e for e in audit[:15] if "escalation" in e.get("stage", "")]
assert len(escalations) == 2  # entries 13-14
assert "reject" in escalations[0]["detail"]       # project: reject
assert "force_approve" in escalations[1]["detail"] # architect: force_approve
```
