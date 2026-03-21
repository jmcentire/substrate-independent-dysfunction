"""Control plane meta-agents — analyze and decide, never write code.

Four meta-agents using cheaper model (Haiku):
  HealthAgent      — monitors V(x)/dV/dt, detects degradation
  TuningAgent      — recommends perturbation/annealing adjustments
  ProgramAgent     — cross-task coherence analysis
  GovernanceAgent  — learning promotion approval, well-formedness
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from swarm.agents.base import AgentBase


# ── Meta-schemas ────────────────────────────────────────────────────


class HealthAnalysis(BaseModel):
    """HealthAgent output — system health assessment."""
    status: str  # "healthy", "degrading", "critical"
    concerns: list[str] = []
    recommendations: list[str] = []
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""


class TuningRecommendation(BaseModel):
    """TuningAgent output — perturbation/annealing adjustments."""
    temperature_adjustment: float = 0.0  # Delta to apply
    competitor_adjustment: int = 0       # Delta to competitor count
    formation_suggestion: str = ""       # Suggest formation change
    reasoning: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


class ProgramAnalysis(BaseModel):
    """ProgramAgent output — cross-task coherence."""
    conflicts: list[str] = []
    shared_files: list[str] = []
    coordination_needed: bool = False
    recommendations: list[str] = []
    reasoning: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


class GovernanceDecision(BaseModel):
    """GovernanceAgent output — learning promotion approval."""
    approved: bool = False
    concerns: list[str] = []
    reasoning: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


# ── System prompts ─────────────────────────────────────────────────

HEALTH_SYSTEM_PROMPT = """You are a health monitoring agent for an autonomous programming system.

Analyze the stability metrics (V(x), dV/dt, oscillation) and run history to assess
system health. You do NOT write code — you analyze and recommend.

## Input
- Stability reading: V(x) value, rate of change, oscillation flag
- Recent run summaries: success/failure/pause counts
- Learning health: growth rate, contradiction rate

## Output
- Status: healthy (V(x) low and stable), degrading (V(x) rising), critical (oscillating or V(x) > threshold)
- List specific concerns
- Recommend concrete actions (pause, simplify formation, reduce scope)"""

TUNING_SYSTEM_PROMPT = """You are a tuning agent for an autonomous programming system.

Based on stability metrics and perturbation state, recommend adjustments to temperature,
competition, and formation. You do NOT write code — you tune parameters.

## Rules
- If system is degrading: reduce temperature, reduce competitors
- If system is stable and succeeding: slightly reduce temperature (anneal)
- If system is oscillating: significantly reduce temperature
- Only suggest formation changes if current formation consistently fails"""

PROGRAM_SYSTEM_PROMPT = """You are a program management agent for an autonomous programming system.

Analyze cross-task state for conflicts and coordination needs.
You do NOT write code — you detect conflicts and recommend coordination.

## Input
- Conflict report: which tasks share files, overlap severity
- Task summaries: what each task is doing

## Output
- List specific conflicts with severity
- Flag shared files that need coordination
- Recommend sequencing or coordination strategies"""

GOVERNANCE_SYSTEM_PROMPT = """You are a governance agent for an autonomous programming system.

Decide whether validated learnings should be promoted from scoped to repo-level.
You do NOT write code — you approve or reject learning promotions.

## Rules
- Approve if the learning is general enough to apply beyond its original scope
- Reject if the learning is too specific or might not generalize
- Reject if the learning conflicts with existing repo-level learnings
- Consider the validation count — higher is more trustworthy"""


# ── Meta-agent functions ───────────────────────────────────────────


async def analyze_health(
    agent: AgentBase,
    stability_reading: str,
    runs_summary: str,
    learnings_summary: str = "",
) -> tuple[HealthAnalysis, int, int]:
    """Run health analysis on system state."""
    prompt = (
        f"Stability reading:\n{stability_reading}\n\n"
        f"Run history:\n{runs_summary}\n\n"
        + (f"Learning health:\n{learnings_summary}\n" if learnings_summary else "")
    )
    return await agent.assess(HealthAnalysis, prompt, HEALTH_SYSTEM_PROMPT)


async def recommend_tuning(
    agent: AgentBase,
    stability_reading: str,
    perturbation_state: str,
) -> tuple[TuningRecommendation, int, int]:
    """Recommend tuning adjustments."""
    prompt = (
        f"Stability reading:\n{stability_reading}\n\n"
        f"Perturbation state:\n{perturbation_state}\n"
    )
    return await agent.assess(TuningRecommendation, prompt, TUNING_SYSTEM_PROMPT)


async def analyze_program(
    agent: AgentBase,
    conflict_report: str,
    task_summaries: str,
) -> tuple[ProgramAnalysis, int, int]:
    """Analyze cross-task coherence."""
    prompt = (
        f"Conflict report:\n{conflict_report}\n\n"
        f"Task summaries:\n{task_summaries}\n"
    )
    return await agent.assess(ProgramAnalysis, prompt, PROGRAM_SYSTEM_PROMPT)


async def decide_governance(
    agent: AgentBase,
    learning_description: str,
    promotion_context: str,
) -> tuple[GovernanceDecision, int, int]:
    """Decide whether to approve learning promotion."""
    prompt = (
        f"Learning to promote:\n{learning_description}\n\n"
        f"Promotion context:\n{promotion_context}\n"
    )
    return await agent.assess(GovernanceDecision, prompt, GOVERNANCE_SYSTEM_PROMPT)
