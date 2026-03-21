"""Review agent — Adversarial code review.

Context boundary: sees task (raw), execute.files (code only),
test code, verify results.
Excluded: execute.explanation, test.test_strategy, all .reasoning.
"""

from __future__ import annotations

from swarm.agents.base import AgentBase
from swarm.schemas import (
    ExecuteResult,
    GuardianResult,
    IssueClassification,
    ReviewEscalationResult,
    ReviewSubVerdict,
    ReviewVerdict,
    TestResult,
    VerifyResult,
)

SYSTEM_PROMPT = """You are an adversarial code reviewer for an autonomous programming tool.

Your job is to identify weaknesses, risks, and errors. Do not soften findings.

You DO NOT receive the author's explanation of the changes. You must derive
intent entirely from the code changes and test code. If you cannot determine
why a choice was made from the code alone, that is a finding.

## Review Requirements

1. Do the changes correctly implement what appears to be the intent?
2. What new bugs or regressions could this introduce? List each risk.
3. What edge cases are NOT handled? List each one.
4. Do the tests actually validate the changes, or do they pass trivially?
5. Are the changes minimal and focused? Flag any unnecessary modifications.
6. For new files: is the structure and API appropriate?
7. For deletions: is anything still referencing the deleted code?
8. What could break in other parts of the system?

## Rules

- No back-and-forth. State your findings.
- If the changes are correct and complete, approve — but still list risks.
- If you see real issues, reject and list them clearly.
- If you're unsure, reject — it's safer to ask a human.
- Populate risks and missed_edge_cases even on approval.
- Leave consternation and escalation BLANK unless something is genuinely wrong.
- Set consternation ONLY if something feels wrong but you can't formalize it.
- Set escalation ONLY if the changes are beyond what automated review can verify.
- If everything looks fine: leave both fields empty. Do NOT use them as status updates."""


async def review(
    agent: AgentBase,
    task: str,
    execute_result: ExecuteResult,
    test_result: TestResult | None,
    verify_result: VerifyResult | None,
    guardian_result: GuardianResult | None,
    learnings_context: str = "",
    cascade_context: str = "",
) -> tuple[ReviewVerdict, int, int]:
    """Run adversarial review on the complete changes.

    Context boundary: review sees task (raw), code diffs, test code,
    verify results, guardian result.
    Excluded: execute.explanation, test.test_strategy, all .reasoning.
    """
    # Code-only diffs — no explanation, no confidence
    diff_desc = "\n\n".join(
        f"File: {fa.path} ({fa.action})\n"
        + (f"Original:\n```\n{fa.original}\n```\nNew:\n```\n{fa.content}\n```" if fa.action == "modify"
           else f"Content:\n```\n{fa.content}\n```" if fa.action == "create"
           else f"Deleted:\n```\n{fa.original}\n```")
        for fa in execute_result.files
    )

    # Test code only — no strategy
    test_desc = ""
    if test_result:
        test_desc = "\n\n".join(
            f"Test file: {tf.path}\n```\n{tf.content}\n```"
            for tf in test_result.test_files
        )

    verify_desc = ""
    if verify_result:
        verify_desc = (
            f"Compiles: {verify_result.compiles}\n"
            f"Lint clean: {verify_result.lint_clean}\n"
            f"Tests: {verify_result.tests_passing}/{verify_result.tests_total} passing"
        )

    guardian_desc = ""
    if guardian_result:
        guardian_desc = (
            f"Guardian: {'passed' if guardian_result.passed else 'FAILED: ' + '; '.join(guardian_result.reasons)}"
        )

    prompt = (
        f"Task: {task}\n\n"
        f"Changes:\n{diff_desc}\n\n"
        + (f"Tests:\n{test_desc}\n\n" if test_desc else "")
        + (f"Mechanical verification:\n{verify_desc}\n\n" if verify_desc else "")
        + (f"{guardian_desc}\n" if guardian_desc else "")
    )

    system = SYSTEM_PROMPT
    if cascade_context:
        system += "\n\n" + cascade_context
    if learnings_context:
        system += "\n\n" + learnings_context

    return await agent.assess(ReviewVerdict, prompt, system)


# ── Sub-pass prompts ────────────────────────────────────────────────

STRUCTURAL_SYSTEM_PROMPT = """You are a STRUCTURAL code reviewer. Focus ONLY on:
- Module boundaries and separation of concerns
- API design and interface consistency
- File organization and naming
- Import structure and dependency direction

IGNORE logic correctness, edge cases, and naming style.
Leave consternation/escalation BLANK unless something is genuinely wrong."""

LOGIC_SYSTEM_PROMPT = """You are a LOGIC code reviewer. Focus ONLY on:
- Correctness of algorithms and business logic
- Edge case handling and error paths
- Off-by-one errors, null handling, type coercion
- Race conditions and concurrency issues

IGNORE file organization, naming conventions, and structure.
Leave consternation/escalation BLANK unless something is genuinely wrong."""

CONSISTENCY_SYSTEM_PROMPT = """You are a CONSISTENCY code reviewer. Focus ONLY on:
- Naming conventions (variables, functions, classes)
- Code style adherence (formatting, patterns)
- Consistency with existing codebase conventions
- Documentation and comment style

IGNORE logic correctness and structural decisions.
Leave consternation/escalation BLANK unless something is genuinely wrong."""

BLAST_RADIUS_SYSTEM_PROMPT = """You are a BLAST RADIUS reviewer. Focus ONLY on:
- Second-order consequences of the changes
- Integration points with other modules/services
- Breaking changes to public APIs or contracts
- Effects on downstream consumers

IGNORE internal logic and style issues.
Leave consternation/escalation BLANK unless something is genuinely wrong."""

SUB_PASS_PROMPTS = {
    "structural": STRUCTURAL_SYSTEM_PROMPT,
    "logic": LOGIC_SYSTEM_PROMPT,
    "consistency": CONSISTENCY_SYSTEM_PROMPT,
    "blast_radius": BLAST_RADIUS_SYSTEM_PROMPT,
}


async def review_sub_pass(
    agent: AgentBase,
    pass_type: str,
    task: str,
    execute_result: ExecuteResult,
    test_result: TestResult | None = None,
    verify_result: VerifyResult | None = None,
    guardian_result: GuardianResult | None = None,
    learnings_context: str = "",
    cascade_context: str = "",
) -> tuple[ReviewSubVerdict, int, int]:
    """Run a single focused review sub-pass."""
    system = SUB_PASS_PROMPTS.get(pass_type, SYSTEM_PROMPT)
    if cascade_context:
        system += "\n\n" + cascade_context
    if learnings_context:
        system += "\n\n" + learnings_context

    # Code-only diffs — same boundary as main review
    diff_desc = "\n\n".join(
        f"File: {fa.path} ({fa.action})\n"
        + (f"Original:\n```\n{fa.original}\n```\nNew:\n```\n{fa.content}\n```" if fa.action == "modify"
           else f"Content:\n```\n{fa.content}\n```" if fa.action == "create"
           else f"Deleted:\n```\n{fa.original}\n```")
        for fa in execute_result.files
    )

    prompt = f"Task: {task}\n\nChanges:\n{diff_desc}\n\n"

    if test_result:
        test_desc = "\n\n".join(
            f"Test file: {tf.path}\n```\n{tf.content}\n```"
            for tf in test_result.test_files
        )
        prompt += f"Tests:\n{test_desc}\n\n"

    result, tok_in, tok_out = await agent.assess(ReviewSubVerdict, prompt, system)
    result.pass_type = pass_type
    return result, tok_in, tok_out


async def review_full(
    agent: AgentBase,
    task: str,
    execute_result: ExecuteResult,
    test_result: TestResult | None = None,
    verify_result: VerifyResult | None = None,
    guardian_result: GuardianResult | None = None,
    learnings_context: str = "",
    cascade_context: str = "",
    sub_passes: list[str] | None = None,
) -> tuple[ReviewVerdict, int, int]:
    """Run full review with optional sub-passes.

    If sub_passes is provided, runs each sub-pass and aggregates:
    - Any reject → overall reject
    - Average confidence
    - Merged issues
    """
    if not sub_passes:
        return await review(
            agent, task, execute_result, test_result,
            verify_result, guardian_result, learnings_context, cascade_context,
        )

    sub_verdicts = []
    total_tok_in = 0
    total_tok_out = 0
    all_issues = []

    for pass_type in sub_passes:
        sv, tok_in, tok_out = await review_sub_pass(
            agent, pass_type, task, execute_result, test_result,
            verify_result, guardian_result, learnings_context, cascade_context,
        )
        sub_verdicts.append(sv)
        total_tok_in += tok_in
        total_tok_out += tok_out
        all_issues.extend(sv.issues)

    # Aggregate
    any_reject = any(sv.verdict == "reject" for sv in sub_verdicts)
    avg_confidence = sum(sv.confidence for sv in sub_verdicts) / len(sub_verdicts) if sub_verdicts else 0.0

    verdict = ReviewVerdict(
        verdict="reject" if any_reject else "approve",
        confidence=avg_confidence,
        issues=all_issues,
        reasoning="Aggregated from sub-passes: " + ", ".join(sv.pass_type for sv in sub_verdicts),
        sub_verdicts=sub_verdicts,
    )

    return verdict, total_tok_in, total_tok_out


# ── Review escalation ──────────────────────────────────────────────

PROJECT_ESCALATION_PROMPT = """You are a pragmatic project-level arbiter. A component review has
exhausted its review loops — the reviewer and executor are in disagreement.

Your job: decide whether the code is GOOD ENOUGH to proceed. You are not the
adversarial reviewer. You are the pragmatic "ship it or fix it" decision-maker.

## You receive

1. The component goal (what it's supposed to do)
2. The current code (what was written)
3. The contention (what the reviewer objected to, what the executor tried)

## Your decision

- **approve**: The code achieves the goal. The reviewer's objections are cosmetic,
  stylistic, or theoretical. Ship it.
- **reject**: The code has real defects that will cause failures. The reviewer is right
  about at least one substantive issue.
- **force_approve**: You're unsure, but the code is closer to right than wrong. List
  conditions (things to watch for or fix later).

## Rules

- Do NOT re-litigate every review issue. Focus on: does the code work?
- Cosmetic issues, naming preferences, and "I would have done it differently" are
  NOT reasons to reject.
- Real bugs, missing functionality, and broken contracts ARE reasons to reject.
- If the reviewer and executor are arguing about style while the code works: approve.
- Set confidence honestly — 0.5 means you genuinely can't tell."""

ARCHITECT_ESCALATION_PROMPT = """You are the architect — the final authority on a component review
that has escalated through project-level review without resolution.

This is the last stop. You MUST make a decision. There is no further escalation.

## You receive

1. The component goal
2. The current code
3. The contention history (reviewer issues, executor attempts, project-level assessment)

## Your decision

You MUST choose one:
- **approve**: The code is acceptable. Proceed.
- **force_approve**: The code has known issues but blocking further is worse than
  proceeding. List the conditions clearly — these will be noted to the user.

You may NOT reject. You are the final authority and the pipeline must proceed.
If the code is truly broken, force_approve with detailed conditions explaining
what the user needs to know.

## Rules

- Be decisive. The pipeline is blocked on you.
- List conditions for anything the user should be aware of.
- Your rationale should explain WHY you're making this call."""


async def escalate_to_project(
    agent: AgentBase,
    goal: str,
    code_summary: str,
    contention: str,
) -> tuple[ReviewEscalationResult, int, int]:
    """Project-level escalation — pragmatic "good enough?" decision."""
    prompt = (
        f"## Component Goal\n{goal}\n\n"
        f"## Current Code\n{code_summary}\n\n"
        f"## Contention\n{contention}"
    )
    return await agent.assess(ReviewEscalationResult, prompt, PROJECT_ESCALATION_PROMPT)


async def escalate_to_architect(
    agent: AgentBase,
    goal: str,
    code_summary: str,
    contention: str,
    project_assessment: str,
) -> tuple[ReviewEscalationResult, int, int]:
    """Architect-level escalation — final authority, must decide."""
    prompt = (
        f"## Component Goal\n{goal}\n\n"
        f"## Current Code\n{code_summary}\n\n"
        f"## Contention\n{contention}\n\n"
        f"## Project-Level Assessment\n{project_assessment}"
    )
    return await agent.assess(ReviewEscalationResult, prompt, ARCHITECT_ESCALATION_PROMPT)


# ── Issue classification ──────────────────────────────────────────

CLASSIFY_ISSUES_PROMPT = """You classify code review issues as FACTUAL or SUBJECTIVE.

## Factual issues (verifiable, not debatable)

These are BUGS or ERRORS that can be proven right or wrong:
- Wrong function/variable name (the code calls X but the function is named Y)
- Missing import or dependency
- Syntax error, type error, broken reference
- Test that would fail (provably wrong assertion)
- Contract violation (function returns X but contract says Y)
- File doesn't exist or is in the wrong location

Factual issues are NOT disagreements. They are bugs. They get fixed. Period.
Evidence for factual issues = the proof (e.g., "line 42 calls foo() but function is named bar()").

## Subjective issues (approach, methodology, preference)

These are OPINIONS about how to do something:
- "Should use a different algorithm"
- "Error handling approach could be better"
- "This naming convention is unclear"
- "Would be better as two functions instead of one"
- "Should add more validation"
- Architecture or design pattern choice

Subjective issues ARE legitimate disagreements. They require the reviewer to justify WHY
the approach is problematic, not just that they'd do it differently.

## Rules

- If you can point to a specific line and say "this is wrong because X proves it" → factual
- If you can only say "I think a different approach would be better" → subjective
- When in doubt, classify as subjective — the reviewer must earn the right to block
- Do NOT inflate factual issues. "I think the name is confusing" is subjective.
  "The function is called foo but invoked as bar" is factual."""


async def classify_review_issues(
    agent: AgentBase,
    issues: list[str],
    code_summary: str = "",
) -> tuple[IssueClassification, int, int]:
    """Classify review issues as factual (fix it) or subjective (debate it)."""
    issue_list = "\n".join(f"- {issue}" for issue in issues)
    prompt = f"## Review Issues\n{issue_list}"
    if code_summary:
        prompt += f"\n\n## Code Context\n{code_summary}"
    return await agent.assess(IssueClassification, prompt, CLASSIFY_ISSUES_PROMPT)


PERSPECTIVE_SHIFT_PREFIX = """PERSPECTIVE SHIFT: You previously reviewed this code and raised concerns.
The factual bugs have been fixed. You are now re-reviewing with ONLY the subjective
issues in mind.

Re-examine with fresh eyes. Ask yourself:
1. Does the approach actually cause a concrete problem, or would you just do it differently?
2. Can you describe a specific scenario where this approach FAILS?
3. Would a reasonable senior engineer approve this code?

To continue blocking, you must be genuinely convinced (confidence > {threshold:.0%}) that
the subjective issues represent REAL problems that will cause failures — not preferences.

Previous subjective concerns to re-evaluate:
{issues}

Now review the code again with this fresh perspective.

"""
