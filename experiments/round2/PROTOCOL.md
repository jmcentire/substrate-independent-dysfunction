# Round 2 Experiment Protocol

## Pre-Registered: Wander Backend Services

**Date:** 2026-02-13
**Task:** Build 7 interdependent microservices for a vacation rental booking platform
**Budget:** $50 per architecture × 4 = $200 total
**Model:** claude-opus-4-6 (all architectures)

## Motivation

Round 1 tested 4 architectures × 2 tasks but both tasks were within
single-agent capacity (Unary scored 18/18 on the simple task). This
round uses a task that genuinely exceeds single-agent capacity to find
the crossover point where multi-agent coordination becomes necessary.

## Architectures

| # | Architecture | Runner | Key Property |
|---|---|---|---|
| 1 | Unary | `unary/run.py` | Single agent, no coordination (control) |
| 2 | Hi-Trust | `hi_trust/run.py` | Hierarchical decompose-delegate-assemble, no review |
| 3 | Org Swarm | `org_swarm/run.py` | Gated review pipeline (11-stage linear) |
| 4 | Emergence | `emergence/run.py` | Stigmergic mesh (shared environment) |

## Task

Full task prompt in `task.md`. Summary: implement as many of 7 microservices
as possible within $50 budget, with working integration between services.

### Services (dependency order)

1. **Availability Service** — bitmask O(1) availability cache
2. **Pricing Service** — hash-sharded rate cache
3. **Property Service** — 4-tier override hierarchy
4. **Event Service** — Kafka routing, content-addressable IDs
5. **Sync Service** — 8 PMS adapters, bidirectional sync
6. **Payments Service** — Stripe mirror, hash-chained audit
7. **Booking Service** — <3s synchronous orchestrator

## Per-Component Scoring (0-3 scale × 7 services = 0-21 max)

| Score | Meaning | Criteria |
|-------|---------|----------|
| 0 | Not attempted | No files for this service |
| 1 | Attempted | Files exist but don't parse/compile OR major contract violations |
| 2 | Isolated | Component works in isolation, tests pass, API matches contract |
| 3 | Integrated | Component correctly calls/is called by dependent services |

## Cross-Component Integration (0-6 bonus points)

Score 1 point for each working integration boundary:

1. Booking → Availability (date check + acquire/release)
2. Booking → Pricing (rate lookup for quote)
3. Booking → Payments (invoice creation + payment capture)
4. Booking → Sync (PMS verification)
5. Sync → Availability (external block management)
6. Sync → Pricing (rate updates from PMS)

## Infrastructure Bonus (0-1)

1 point if shared types/schemas/config enable cross-service consistency.

## Total Score: /28

## Additional Metrics

| Metric | Source |
|--------|--------|
| Cost per point | result.json budget.spent_usd / score |
| Wall time | result.json wall_seconds |
| Files produced | result.json files_produced (source only) |
| Test pass rate | evaluation: tests passing / tests total |
| Interface mismatches | evaluation: contract violations detected |
| Questions asked | oracle/questions.jsonl line count |
| Budget utilization | spent / $50 cap |

## Run Schedule

| # | Architecture | Run ID | Budget |
|---|---|---|---|
| R2-1 | Unary | r2-unary-001 | $50 |
| R2-2 | Hi-Trust | r2-hitrust-001 | $50 |
| R2-3 | Org Swarm | r2-orgswarm-001 | $50 |
| R2-4 | Emergence | r2-emergence-001 | $50 |

## Runner Configuration Changes (vs Round 1)

| Runner | Change | Rationale |
|--------|--------|-----------|
| Unary | MAX_TOOL_ITERATIONS 50→100 | 7 services need more room |
| Hi-Trust | MAX_TOOL_ITERATIONS 30→50 | Deeper decomposition expected |
| Emergence | num_agents default 5→8 | More agents for more services |
| All | --task-file support added | Task too long for CLI argument |
| All | ask_question tool added | Oracle protocol for Q&A |

## Controls

- Same model (claude-opus-4-6) for all architectures
- Same task prompt (canonical text in task.md)
- Same reference material (copied into each sandbox)
- Same oracle answers (pre-seeded from prior decomposition)
- Same budget ($50 each)
- No human intervention (autonomous mode)
- Fresh state per run

## Pre-Registered Hypotheses

- **H1:** Unary will struggle to complete all 7 services within budget,
  producing fewer integrated components than at least one multi-agent architecture
- **H2:** Multi-agent architectures will show measurable coordination dysfunction
  (interface mismatches, duplicated work, verification theater) at integration
  boundaries
- **H3:** Dysfunction patterns will correlate with coordination topology:
  Org Swarm (most overhead) > Hi-Trust > Emergence > Unary
- **H4:** Cost per quality point will be higher for multi-agent architectures
  on this task, but multi-agent architectures will achieve higher absolute scores

## Evaluation Method

Post-run evaluation by independent subagent per architecture:
1. Read all source files in output/
2. Attempt to run any test suites
3. Score each component against contract from decomposition.json
4. Check integration boundaries (do cross-service calls use correct endpoints/types?)
5. Produce structured scorecard

Evaluator receives only the output directory (blinded to architecture).
