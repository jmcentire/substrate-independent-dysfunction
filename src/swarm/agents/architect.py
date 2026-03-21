"""Architect agent — Plan the approach for the task.

Context boundary: sees task, diagnose.task_type, diagnose.complexity,
diagnose.language, diagnose.framework, diagnose.constraints.
Excluded: diagnose.reasoning.
"""

from __future__ import annotations

from swarm.agents.base import AgentBase
from swarm.schemas import ArchitectResult, DiagnoseResult, ReviewSubVerdict, ReviewVerdict

SYSTEM_PROMPT = """You are a software architect agent for a programming tool.

Given a task description and diagnosis, plan the implementation approach.

## Your Job

1. Define the overall approach (how to solve this)
2. Break it into ordered steps
3. Identify which files need to be created, modified, or deleted
4. Assess risks for each step
5. Be specific about file paths and changes needed

## Rules

- Keep the plan minimal — solve the task, don't gold-plate
- Read the spec for INTENT, not for instruction count. A thorough spec describing a
  simple system is still a simple system. The quality of the description is not evidence
  of implementation difficulty.
- Prefer fewer files. One well-structured file beats many small files when the task is cohesive.
  Ask: "what is this actually asking me to build?" not "how do I implement everything listed?"
- Identify files that need modification vs. files for context only
- Flag high-risk steps (changes to shared code, public APIs, database schemas)
- If a step requires creating new files, list them in new_files
- If a step requires deleting files, list them in deleted_files

## Signals

Leave consternation and escalation BLANK unless something is genuinely wrong.
Do NOT use them to confirm that things are fine or to provide status updates.

- Set consternation ONLY if something feels wrong but you can't formalize it
  (e.g., the task asks to modify a pattern that doesn't exist in the code).
- Set escalation ONLY if the task requires changes beyond what automated tooling
  can handle (e.g., multi-repo changes, breaking API changes, infrastructure mods).
- If everything looks fine: leave both fields empty."""


async def architect(
    agent: AgentBase,
    task: str,
    diagnose_result: DiagnoseResult,
    repo_context: str = "",
    file_listing: str = "",
    learnings_context: str = "",
    cascade_context: str = "",
) -> tuple[ArchitectResult, int, int]:
    """Plan the implementation approach.

    Context boundary: architect sees task_type, complexity, language, framework, constraints.
    Excluded: diagnose.reasoning.
    """
    prompt = (
        f"Task: {task}\n\n"
        f"Task type: {diagnose_result.task_type}\n"
        f"Complexity: {diagnose_result.complexity}\n"
        f"Language: {diagnose_result.language}\n"
        f"Framework: {diagnose_result.framework}\n"
        f"Constraints: {', '.join(diagnose_result.constraints) if diagnose_result.constraints else 'none'}\n"
        f"Risks: {', '.join(diagnose_result.risks) if diagnose_result.risks else 'none'}\n"
    )
    if repo_context:
        prompt += f"\nRepo context:\n{repo_context}\n"
    if file_listing:
        prompt += f"\nFile listing:\n{file_listing}\n"

    system = SYSTEM_PROMPT
    if cascade_context:
        system += "\n\n" + cascade_context
    if learnings_context:
        system += "\n\n" + learnings_context

    return await agent.assess(ArchitectResult, prompt, system, max_tokens=32768)


# ── Architecture review sub-pass prompts ──────────────────────────

MVP_SYSTEM_PROMPT = """You review architecture plans for MVP fitness. Focus ONLY on:
- Does the plan produce working code that solves the stated task?
- Is the plan building features that nobody asked for AND that interfere with testing?
- Will the proposed structure compile, run, and be testable?

A plan that works and passes tests is a good plan, even if you'd structure it differently.
Unnecessary components are only a problem if they PREVENT delivery or testability.
Approve unless the plan will FAIL to produce working, testable code.
Leave consternation/escalation BLANK unless something is genuinely wrong."""

SIMPLICITY_SYSTEM_PROMPT = """You review architecture plans for complexity that blocks delivery. Focus ONLY on:
- Does the complexity make the code HARDER TO TEST or HARDER TO GET WORKING?
- Will extra abstractions cause integration failures or make debugging impossible?
- Is the plan so complex that it's unlikely to produce passing tests in a reasonable timeframe?

Complexity is only a problem when it prevents working, tested code. Many-file structures,
abstractions, and patterns are fine if they serve the implementation and remain testable.
Approve if the plan will produce working code. Reject only if complexity will PREVENT delivery.
Leave consternation/escalation BLANK unless something is genuinely wrong."""

INTEGRITY_SYSTEM_PROMPT = """You review architecture plans for structural problems that cause failures. Focus ONLY on:
- Will circular dependencies prevent the code from compiling or running?
- Will tight coupling make the code UNTESTABLE (can't mock, can't isolate)?
- Are there dependency conflicts that will cause runtime errors?

Structural preferences (cohesion, coupling style, dependency direction) are NOT grounds for
rejection unless they cause concrete failures. The question is: "will this structure WORK
and be TESTABLE?" — not "is this the structure I'd choose?"
Approve if the structure will produce working, testable code. Reject only for structural
problems that will cause build failures, runtime errors, or make testing impossible.
Leave consternation/escalation BLANK unless something is genuinely wrong."""

REUSE_SYSTEM_PROMPT = """You review architecture plans for reinventing functionality that already works. Focus ONLY on:
- Does the plan reimplement something that already exists AND WORKS in the codebase?
- Would using the existing implementation be MORE RELIABLE than building new?

Reimplementation is only a problem when an existing, tested solution is available and the
new implementation is likely to have bugs the existing one already fixed.
Custom implementation is fine when existing solutions don't fit the requirements.
Approve if the approach will produce working code. Reject only if reuse would be
clearly more reliable AND the reimplementation is likely to introduce bugs.
Leave consternation/escalation BLANK unless something is genuinely wrong."""

STANDARDS_SYSTEM_PROMPT = """You review architecture plans for standards violations that cause failures. Focus ONLY on:
- Security: does the plan introduce exploitable vulnerabilities?
- Correctness: does the plan violate language/framework constraints that will cause errors?

Naming conventions, project structure preferences, and style choices are NOT grounds for
rejection. Only reject for security vulnerabilities that are exploitable or for violations
of framework requirements that will cause the code to fail.
Approve if the plan follows enough convention to compile, run, and pass tests.
Leave consternation/escalation BLANK unless something is genuinely wrong."""

ARCHITECT_SUB_PASS_PROMPTS = {
    "mvp": MVP_SYSTEM_PROMPT,
    "simplicity": SIMPLICITY_SYSTEM_PROMPT,
    "integrity": INTEGRITY_SYSTEM_PROMPT,
    "reuse": REUSE_SYSTEM_PROMPT,
    "standards": STANDARDS_SYSTEM_PROMPT,
}


async def architect_review_sub_pass(
    agent: AgentBase,
    pass_type: str,
    task: str,
    architect_result: ArchitectResult,
    diagnose_result: DiagnoseResult | None = None,
    learnings_context: str = "",
) -> tuple[ReviewSubVerdict, int, int]:
    """Run a single focused architecture review sub-pass."""
    system = ARCHITECT_SUB_PASS_PROMPTS.get(pass_type, MVP_SYSTEM_PROMPT)
    if learnings_context:
        system += "\n\n" + learnings_context

    # Build plan summary for review
    steps_desc = "\n".join(
        f"  {i+1}. {step.description} (risk: {step.risk}, files: {', '.join(step.files_involved) or 'none'})"
        for i, step in enumerate(architect_result.steps)
    )
    prompt = (
        f"Task: {task}\n\n"
        f"Architecture plan:\n"
        f"  Approach: {architect_result.approach}\n"
        f"  Steps:\n{steps_desc}\n"
        f"  Affected files: {', '.join(architect_result.affected_files)}\n"
        f"  New files: {', '.join(architect_result.new_files) or 'none'}\n"
        f"  Deleted files: {', '.join(architect_result.deleted_files) or 'none'}\n"
    )
    if diagnose_result:
        prompt += (
            f"\nDiagnosis:\n"
            f"  Complexity: {diagnose_result.complexity}\n"
            f"  Language: {diagnose_result.language}\n"
            f"  Constraints: {', '.join(diagnose_result.constraints) if diagnose_result.constraints else 'none'}\n"
        )

    result, tok_in, tok_out = await agent.assess(ReviewSubVerdict, prompt, system)
    result.pass_type = pass_type
    return result, tok_in, tok_out


async def architect_review_full(
    agent: AgentBase,
    task: str,
    architect_result: ArchitectResult,
    diagnose_result: DiagnoseResult | None = None,
    sub_passes: list[str] | None = None,
    learnings_context: str = "",
) -> tuple[ReviewVerdict, int, int]:
    """Run full architecture review with sub-passes.

    Aggregation: majority reject OR any high-confidence (>=0.85) reject
    → overall reject. A single low-confidence sub-pass cannot block.
    """
    if not sub_passes:
        # No sub-passes → approve (no review)
        return ReviewVerdict(
            verdict="approve", confidence=1.0, issues=[],
            reasoning="No architecture review sub-passes configured",
        ), 0, 0

    sub_verdicts = []
    total_tok_in = 0
    total_tok_out = 0
    all_issues: list[str] = []

    for pass_type in sub_passes:
        sv, tok_in, tok_out = await architect_review_sub_pass(
            agent, pass_type, task, architect_result, diagnose_result,
            learnings_context=learnings_context,
        )
        sub_verdicts.append(sv)
        total_tok_in += tok_in
        total_tok_out += tok_out
        all_issues.extend(sv.issues)

    # Aggregate: majority reject OR any high-confidence reject → overall reject.
    # A single opinionated sub-pass should not block the entire architecture.
    reject_count = sum(1 for sv in sub_verdicts if sv.verdict == "reject")
    approve_count = len(sub_verdicts) - reject_count
    high_confidence_reject = any(
        sv.verdict == "reject" and sv.confidence >= 0.85
        for sv in sub_verdicts
    )
    majority_reject = reject_count > approve_count
    overall_reject = majority_reject or high_confidence_reject
    avg_confidence = (
        sum(sv.confidence for sv in sub_verdicts) / len(sub_verdicts)
        if sub_verdicts else 0.0
    )

    verdict = ReviewVerdict(
        verdict="reject" if overall_reject else "approve",
        confidence=avg_confidence,
        issues=all_issues,
        reasoning="Architecture review: " + ", ".join(sv.pass_type for sv in sub_verdicts),
        sub_verdicts=sub_verdicts,
    )

    return verdict, total_tok_in, total_tok_out


ARCHITECT_PERSPECTIVE_SHIFT_PREFIX = """PERSPECTIVE SHIFT: You previously reviewed this architecture and raised concerns.
The factual constraints have been addressed. You are now re-reviewing with ONLY the
subjective issues in mind.

Re-examine with fresh eyes. Ask yourself:
1. Does the proposed approach cause a concrete failure, or would you just architect it differently?
2. Can you describe a specific scenario where this architecture FAILS in production?
3. Is this plan overengineered — or is your alternative underspecified?

A well-written spec for a simple system looks like a spec for a complex system.
Read for intent, not for instruction count.

To continue blocking, you must be genuinely convinced (confidence > {threshold:.0%}) that
the architectural issues represent REAL problems — not preferences.

Previous architectural concerns to re-evaluate:
{issues}

Now review the architecture again with this fresh perspective.

"""
