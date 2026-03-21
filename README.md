# Substrate-Independent Dysfunction in Multi-Agent AI

Code, data, and experimental protocols accompanying:

> **"The Organizational Physics of Multi-Agent AI: Substrate-Independent Dysfunction in Autonomous Software Engineering Swarms"**
> Jeremy McEntire, 2026

## Overview

This repository contains the source code for a gated-review multi-agent software engineering pipeline ("swarm"), the experimental harness for a controlled 4-architecture comparison study, and the raw audit data supporting the paper's empirical claims.

The paper presents evidence that organizational dysfunction is substrate-independent: the same failure patterns (bikeshedding, governance conflicts, verification theater, budget overruns) emerge in LLM-based multi-agent systems as in human organizations, driven by information-theoretic constraints rather than human psychology.

## Repository Structure

```
.
├── src/swarm/           # Swarm pipeline source code
│   ├── agents/          # Agent implementations (review.py contains 6 anti-dysfunction mechanisms)
│   ├── scoring.py       # Proxy-metric scoring (Goodhart evidence)
│   ├── control.py       # Lyapunov stability monitor
│   └── ...
├── tests/               # Test suite
├── config.yaml          # Default pipeline configuration
├── data/                # Primary audit trail (Study 1)
│   ├── state.json       # 89-stage execution state with per-stage metrics
│   ├── audit.jsonl      # Timestamped event log
│   ├── swarm.yaml       # Run configuration
│   └── task.md          # Task specification
├── experiments/         # Controlled experiment (Study 3)
│   ├── PROTOCOL.md      # Pre-registered experimental protocol
│   ├── run_experiment.sh # Experiment runner (2x4 factorial)
│   ├── shared/          # Shared instrumentation and tools
│   ├── unary/           # Single-agent baseline (A1)
│   ├── hi_trust/        # Trust-based hierarchy (A2)
│   ├── org_swarm/       # Gated-review hierarchy (A3)
│   ├── emergence/       # Stigmergic coordination (A4)
│   ├── round2/          # Extended replication protocol
│   └── runs/            # Raw run logs and results
└── pyproject.toml       # Package definition
```

## Key Findings

From `data/state.json` (independently verifiable):

| Metric | Value |
|--------|-------|
| Pipeline stages | 89 |
| Total cost | $57.43 |
| Total tokens | 7.17M |
| Wall time | 18.1 hours |
| Budget wasted on rejections | 22.6% ($12.97) |
| Code review rejection rate | 87% (7/8) |
| Pure bikeshedding cases | 4 (factual=0, subjective=15-23) |
| Verify stages with tests=0/0 | 9/9 |
| Budget overrun | 2.3x cap |

## Anti-Dysfunction Mechanisms

The swarm includes six explicit anti-dysfunction mechanisms (see `src/swarm/agents/review.py`):

1. Factual/subjective issue classification
2. Perspective-shift prompting
3. Multi-level escalation (project + architect)
4. Scoped sub-pass review (structural, logic, consistency, blast radius)
5. Anti-bikeshedding directives
6. Lyapunov stability monitoring (`src/swarm/control.py`)

All six failed to prevent the predicted dysfunction patterns. The dysfunction emerged despite countermeasures, not because of them.

## Reproducing the Experiments

### Requirements

- Python >= 3.12
- An Anthropic API key (`ANTHROPIC_API_KEY` environment variable)

### Installation

```bash
pip install -e ".[cli,dev]"
```

### Running the 4-architecture comparison (Study 3)

```bash
export ANTHROPIC_API_KEY=your-key
cd experiments
bash run_experiment.sh --complex-only
```

**Warning:** Each run costs $10-25 in API calls. A full 2x4 factorial costs ~$140.

### Running tests

```bash
pytest
```

## Data Verification

All claims in the paper can be independently verified from `data/state.json`:

```python
import json
with open("data/state.json") as f:
    state = json.load(f)

print(f"Stages: {len(state['stages'])}")
print(f"Cost: ${state['total_cost_usd']:.2f}")
print(f"Tokens: {state['total_tokens']:,}")

reviews = [s for s in state['stages'] if s['stage_type'] == 'review']
rejected = [s for s in reviews if not s['passed']]
print(f"Review rejection rate: {len(rejected)}/{len(reviews)} = {len(rejected)/len(reviews)*100:.0f}%")
```

## License

MIT

## Citation

If you use this code or data, please cite:

```bibtex
@article{mcentire2026substrate,
  title={The Organizational Physics of Multi-Agent AI: Substrate-Independent Dysfunction in Autonomous Software Engineering Swarms},
  author={McEntire, Jeremy},
  year={2026}
}
```
