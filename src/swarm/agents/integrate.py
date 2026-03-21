"""Integrate agent — Cross-file coherence check.

Context boundary: sees task (raw), all file changes (code only),
test code, verify results.
Excluded: all .reasoning from all stages.
"""

from __future__ import annotations

from swarm.agents.base import AgentBase
from swarm.schemas import ExecuteResult, IntegrateResult, TestResult, VerifyResult

SYSTEM_PROMPT = """You are a cross-file coherence checker for an autonomous programming tool.

Your job is to verify that all the code changes work together as a coherent whole.
Individual changes may be correct in isolation but break when combined.

## Check For

1. **Import consistency**: Are all new imports valid? Do removed exports break other files?
2. **Type consistency**: Do function signatures match across call sites?
3. **Naming consistency**: Are variable/function names consistent across files?
4. **Control flow**: Does the overall data flow make sense across files?
5. **Side effects**: Do changes in one file have unintended effects on others?
6. **Missing pieces**: Are there changes that should have been made but weren't?

## Rules

- Focus on CROSS-FILE issues, not issues within a single file
- If all changes are in a single file, focus on consistency with the rest of the codebase
- Set coherent=true only if you're confident the changes work together
- List specific issues if not coherent

## Signals

Leave consternation and escalation BLANK unless something is genuinely wrong.
Do NOT use them to confirm that things are fine or to provide status updates.

- Set consternation ONLY if the changes reveal architectural inconsistencies in the
  codebase that aren't caused by these changes but make them risky.
- Set escalation ONLY if coherence requires changes to files not included in the
  current set (e.g., a shared interface needs updating).
- If everything looks fine: leave both fields empty."""


async def integrate(
    agent: AgentBase,
    task: str,
    execute_result: ExecuteResult,
    test_result: TestResult | None,
    verify_result: VerifyResult | None,
    learnings_context: str = "",
    cascade_context: str = "",
) -> tuple[IntegrateResult, int, int]:
    """Run cross-file coherence check.

    Context boundary: integrate sees task (raw), all code changes, test code.
    Excluded: all .reasoning from all stages.
    """
    # All file changes — code only
    diff_desc = "\n\n".join(
        f"File: {fa.path} ({fa.action})\n"
        + (f"Original:\n```\n{fa.original}\n```\nNew:\n```\n{fa.content}\n```" if fa.action == "modify"
           else f"Content:\n```\n{fa.content}\n```" if fa.action == "create"
           else f"Deleted:\n```\n{fa.original}\n```")
        for fa in execute_result.files
    )

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

    prompt = (
        f"Task: {task}\n\n"
        f"All changes:\n{diff_desc}\n\n"
        + (f"Tests:\n{test_desc}\n\n" if test_desc else "")
        + (f"Verification:\n{verify_desc}\n" if verify_desc else "")
    )

    system = SYSTEM_PROMPT
    if cascade_context:
        system += "\n\n" + cascade_context
    if learnings_context:
        system += "\n\n" + learnings_context

    return await agent.assess(IntegrateResult, prompt, system)
