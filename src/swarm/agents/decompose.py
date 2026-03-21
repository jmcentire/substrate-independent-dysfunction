"""Decompose agent — break epic tasks into components.

Four-phase decomposition:
  1. ask_questions      — identify blocking/clarifying questions
  2. build_component_map — decompose into 3-7 logical components
  3. define_contracts    — inputs/outputs/invariants per component
  4. plan_implementation_order — topological sort by dependencies
"""

from __future__ import annotations

from swarm.agents.base import AgentBase
from swarm.schemas import (
    ComponentMapResult,
    ContractSetResult,
    DecomposeQuestionsResult,
    DiagnoseResult,
    ImplementationOrderResult,
)

QUESTIONS_SYSTEM = """You are a senior engineer doing task decomposition. Your job is to find
ambiguities in an epic programming task and RESOLVE them as engineering decisions.

You are NOT a questionnaire generator. You are an engineer who makes decisions.

## Your process

1. FIND ambiguities — interface boundaries, format propagation, scope boundaries,
   edge case tensions, implicit assumptions. Be thorough.

2. RESOLVE each ambiguity yourself by reasoning from the constraints. For each one:
   - What are the options?
   - What do the constraints require? (portability, determinism, simplicity, etc.)
   - Which option best satisfies the constraints?
   - Make the decision. State it clearly with rationale.

3. Only ASK the human about things you genuinely cannot determine from the spec:
   - Business/product decisions (what should the user experience be?)
   - Organizational context (what systems exist? what team owns this?)
   - Explicit preferences where multiple options are equally valid AND the choice
     has major architectural consequences

## Categories of ambiguity to look for

- INTERFACE BOUNDARIES: Where two components meet — data formats, encoding,
  serialization. Don't ask "what format?" — pick the format that satisfies the
  constraints (portable, deterministic, simple) and document your decision.

- FORMAT PROPAGATION: Data representation consistency across components.
  Don't ask — define the canonical format and use it everywhere.

- SCOPE BOUNDARIES: Where one component's responsibility ends and another begins.
  Don't ask — draw the line where it minimizes coupling.

- EDGE CASE TENSIONS: Where two reasonable choices conflict at a boundary.
  Don't ask — pick the one that fails safely and document why.

- IMPLICIT ASSUMPTIONS: Things the spec doesn't say. Don't ask — make the
  simplest assumption that satisfies the constraints and state it.

## Output

Your output has three parts:
1. decisions: Engineering decisions you made, with rationale grounded in constraints.
   This is the PRIMARY output. Most ambiguities should be resolved here.
2. questions: Only things you genuinely need human input for. These should be RARE
   (0-3 questions, not 10+). If you have more than 3, you're asking instead of deciding.
3. assumptions: Defaults you'll use if questions go unanswered.

## Anti-patterns (DO NOT do these)

- Listing 10+ "blocking" questions — that's asking the human to do your job
- Asking about things the spec already answers or the constraints determine
- Asking about implementation details you control (formats, encodings, representations)
- Asking "which option?" when one option clearly fits the constraints better
- Asking about domains you don't understand — learn the domain first, then decide
- Treating every ambiguity as a question — most are decisions you should make"""

COMPONENT_MAP_SYSTEM = """You are a task decomposition agent. Your job is to break an epic
programming task into 3-7 logical components.

Each component is a concern, not a file. A component might span multiple files
or a single file might contain parts of multiple components.

Level 0: Write a single sentence that captures the entire task.
Level 1: Decompose into the handful of logical components.

For each component:
- Give it a clear, unique id (lowercase, no spaces — e.g., "auth", "db_schema", "api_layer")
- Name it descriptively
- Describe what it does (2-3 sentences)
- List dependencies on other components by id
- Estimate complexity: trivial/simple/moderate/complex
- List likely affected files if you can guess

Keep components at the right granularity — each should be independently buildable
and testable once its dependencies are in place."""

CONTRACTS_SYSTEM = """You are a task decomposition agent. Your job is to define contracts
for each component in a decomposed task.

For each component, define:
- inputs: what data/context/APIs this component consumes
- outputs: what data/artifacts/APIs this component produces
- invariants: conditions that must always hold (e.g., "token expires in 24h")
- error_modes: what can go wrong and how it should be handled

These are promises, not implementations. They define the interface between components.

Also identify cross_component_constraints — rules that span multiple components
(e.g., "all API responses use the same error format")."""

IMPLEMENTATION_ORDER_SYSTEM = """You are a task decomposition agent. Your job is to determine
the implementation order for a set of components.

Rules:
- Topological sort by dependencies. Foundations first.
- If two components have no dependency relationship, they can be parallel.
- Assign sequential order numbers starting from 1.
- Group parallelizable components into parallel_groups.
- Give a clear rationale for each step's position.

The goal: each component can be built and tested before its dependents start."""


async def ask_questions(
    agent: AgentBase,
    task: str,
    diagnose_result: DiagnoseResult,
    learnings_context: str = "",
) -> tuple[DecomposeQuestionsResult, int, int]:
    """Phase 1: Identify questions to ask before decomposing."""
    prompt = (
        f"Task:\n{task}\n\n"
        f"Diagnosis:\n"
        f"  Type: {diagnose_result.task_type}\n"
        f"  Complexity: {diagnose_result.complexity}\n"
        f"  Language: {diagnose_result.language}\n"
        f"  Framework: {diagnose_result.framework}\n"
        f"  Summary: {diagnose_result.summary}\n"
        f"  Constraints: {', '.join(diagnose_result.constraints) or 'none'}\n"
        f"  Risks: {', '.join(diagnose_result.risks) or 'none'}"
    )
    system = QUESTIONS_SYSTEM
    if learnings_context:
        system += "\n\n" + learnings_context
    return await agent.assess(DecomposeQuestionsResult, prompt, system)


async def build_component_map(
    agent: AgentBase,
    task: str,
    diagnose_result: DiagnoseResult,
    answers: dict[str, str] | None = None,
    learnings_context: str = "",
) -> tuple[ComponentMapResult, int, int]:
    """Phase 2: Decompose into 3-7 logical components."""
    prompt = (
        f"Task:\n{task}\n\n"
        f"Diagnosis:\n"
        f"  Type: {diagnose_result.task_type}\n"
        f"  Complexity: {diagnose_result.complexity}\n"
        f"  Language: {diagnose_result.language}\n"
        f"  Summary: {diagnose_result.summary}"
    )
    if answers:
        prompt += "\n\nAnswers to questions:\n"
        for q, a in answers.items():
            prompt += f"  Q: {q}\n  A: {a}\n"
    system = COMPONENT_MAP_SYSTEM
    if learnings_context:
        system += "\n\n" + learnings_context
    return await agent.assess(ComponentMapResult, prompt, system)


async def define_contracts(
    agent: AgentBase,
    task: str,
    component_map: ComponentMapResult,
    learnings_context: str = "",
) -> tuple[ContractSetResult, int, int]:
    """Phase 3: Define contracts for each component."""
    components_text = ""
    for c in component_map.components:
        components_text += (
            f"\n  [{c.id}] {c.name}: {c.description}\n"
            f"    Dependencies: {', '.join(c.dependencies) or 'none'}\n"
            f"    Complexity: {c.estimated_complexity}\n"
        )
    prompt = (
        f"Task:\n{task}\n\n"
        f"One-sentence summary: {component_map.one_sentence}\n\n"
        f"Components:{components_text}\n"
        f"Interaction summary: {component_map.interaction_summary}"
    )
    system = CONTRACTS_SYSTEM
    if learnings_context:
        system += "\n\n" + learnings_context
    return await agent.assess(ContractSetResult, prompt, system)


async def plan_implementation_order(
    agent: AgentBase,
    component_map: ComponentMapResult,
    contracts: ContractSetResult,
    learnings_context: str = "",
) -> tuple[ImplementationOrderResult, int, int]:
    """Phase 4: Determine implementation order."""
    components_text = ""
    for c in component_map.components:
        components_text += f"\n  [{c.id}] {c.name} — deps: {', '.join(c.dependencies) or 'none'}"

    contracts_text = ""
    for ct in contracts.contracts:
        contracts_text += (
            f"\n  [{ct.component_id}] inputs: {', '.join(ct.inputs)}, "
            f"outputs: {', '.join(ct.outputs)}"
        )

    prompt = (
        f"Components:{components_text}\n\n"
        f"Contracts:{contracts_text}\n\n"
        f"Cross-component constraints: "
        f"{', '.join(contracts.cross_component_constraints) or 'none'}"
    )
    system = IMPLEMENTATION_ORDER_SYSTEM
    if learnings_context:
        system += "\n\n" + learnings_context
    return await agent.assess(ImplementationOrderResult, prompt, system)
