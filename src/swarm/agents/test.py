"""Test agent — Write regression tests for the changes.

Context boundary: sees task_type, language, execute.files (code diffs only),
existing test patterns.
Excluded: execute.explanation, all .reasoning fields.
"""

from __future__ import annotations

from pathlib import Path

from swarm.agents.base import AgentBase
from swarm.schemas import DiagnoseResult, ExecuteResult, LocateResult, TestResult

SYSTEM_PROMPT = """You are a test generation agent for a programming tool.

Write tests that verify the code changes work correctly.

## Your Job

1. Write tests that validate the new/modified behavior
2. Cover edge cases and error conditions
3. Follow existing test patterns in the repo
4. For bugfixes: write a test that would fail before the fix and pass after

## Rules

- Place test files in the project's test directory convention
- Use the project's existing test framework (detect from existing tests)
- Name tests descriptively
- Test behavior, not implementation details
- Keep tests focused and independent

## Confidence

Your confidence should reflect how well the tests actually validate the changes.
A test that only checks the happy path is low confidence.

## Signals

Leave consternation and escalation BLANK unless something is genuinely wrong.
Do NOT use them to confirm that things are fine, suggest nice-to-haves, or
note things that would be different in a production environment.

- Set consternation ONLY if the changes are genuinely hard to test in isolation
  (deeply coupled to runtime state, external services, or complex setup).
- Set escalation ONLY if proper testing is IMPOSSIBLE without infrastructure not
  available (needs live database, external service that can't be mocked, etc.).
  Preferences about test runners, compilation, or fixture patterns are NOT
  reasons to escalate — write the best tests you can with what's available.
- If everything looks fine: leave both fields empty."""


async def generate_tests(
    agent: AgentBase,
    task: str,
    diagnose_result: DiagnoseResult,
    locate_result: LocateResult,
    execute_result: ExecuteResult,
    repo_path: Path,
    learnings_context: str = "",
    cascade_context: str = "",
) -> tuple[TestResult, int, int]:
    """Generate tests for code changes.

    Context boundary: tester sees task_type, language, code diffs (no explanation),
    existing test patterns.
    Excluded: execute.explanation, execute.confidence, all .reasoning.
    """
    existing_tests = []
    for loc in locate_result.files:
        if loc.relevance == "test":
            filepath = repo_path / loc.path
            if filepath.exists():
                content = filepath.read_text()
                existing_tests.append(f"--- {loc.path} (existing test) ---\n{content}")

    # Code diffs only — no explanation
    diff_desc = "\n".join(
        f"File: {fa.path} ({fa.action})\n"
        + (f"  Original: {fa.original[:300]}...\n  New: {fa.content[:300]}..." if fa.action == "modify"
           else f"  Content: {fa.content[:300]}..." if fa.action == "create"
           else f"  Deleted: {fa.original[:300]}...")
        for fa in execute_result.files
    )

    prompt = (
        f"Task type: {diagnose_result.task_type}\n"
        f"Language: {diagnose_result.language}\n\n"
        f"Task: {task}\n\n"
        f"Changes:\n{diff_desc}\n\n"
        + (f"Existing test patterns:\n\n" + "\n\n".join(existing_tests) if existing_tests
           else "No existing tests found — use standard test patterns for the language.")
    )

    system = SYSTEM_PROMPT
    if cascade_context:
        system += "\n\n" + cascade_context
    if learnings_context:
        system += "\n\n" + learnings_context

    return await agent.assess(TestResult, prompt, system, max_tokens=32768)
