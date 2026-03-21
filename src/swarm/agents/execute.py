"""Execute agent — Write code changes (create/modify/delete).

Context boundary: sees task, diagnose.task_type, diagnose.language,
locate.files (paths + relevance only), file contents.
Excluded: all .reasoning fields, locate.search_strategy.
"""

from __future__ import annotations

from pathlib import Path

from swarm.agents.base import AgentBase
from swarm.schemas import DiagnoseResult, ExecuteResult, LocateResult

SYSTEM_PROMPT = """You are a code execution agent for a programming tool.

Generate the code changes needed to complete the task. You can:
- **modify**: Change existing code (provide exact original text and replacement)
- **create**: Create new files (provide full file content)
- **delete**: Delete files (provide the original content for audit)

## Rules

1. For modifications: 'original' must be EXACT verbatim text from the source file
2. 'content' is the replacement text (for modify) or full file content (for create)
3. Keep changes minimal — complete the task, nothing more
4. Follow existing code style (indentation, naming, imports)
5. Do NOT refactor surrounding code unless the task requires it
6. Do NOT add unrelated improvements
7. Provide an explanation for each file change

## Confidence

Your confidence should reflect:
- How sure you are that original text matches exactly (for modify)
- Whether the change fully addresses the task
- Whether the change could introduce new issues

## Signals

Leave consternation and escalation BLANK unless something is genuinely wrong.
Do NOT use them to confirm that things are fine or to provide status updates.

- Set consternation ONLY if the code around the change looks fragile or has patterns
  that suggest deeper issues beyond the current task.
- Set escalation ONLY if the correct implementation requires architectural changes
  beyond what a single set of file operations can handle.
- If everything looks fine: leave both fields empty."""


async def execute(
    agent: AgentBase,
    task: str,
    diagnose_result: DiagnoseResult,
    locate_result: LocateResult,
    repo_path: Path,
    learnings_context: str = "",
    cascade_context: str = "",
    review_issues: list[str] | None = None,
) -> tuple[ExecuteResult, int, int]:
    """Generate code changes.

    Context boundary: execute sees task_type, language, file paths + content.
    Excluded: all .reasoning, locate.search_strategy.
    """
    file_contents = []
    for loc in locate_result.files:
        if loc.relevance in ("primary", "context"):
            filepath = repo_path / loc.path
            if filepath.exists():
                content = filepath.read_text()
                file_contents.append(f"--- {loc.path} ({loc.relevance}) ---\n{content}")

    prompt = (
        f"Task type: {diagnose_result.task_type}\n"
        f"Language: {diagnose_result.language}\n\n"
        f"Task: {task}\n\n"
        f"Source files:\n\n" + "\n\n".join(file_contents)
    )

    if review_issues:
        constraints = "\n".join(f"- {issue}" for issue in review_issues)
        prompt += f"\n\n## Review Feedback (address these issues)\n{constraints}"

    system = SYSTEM_PROMPT
    if cascade_context:
        system += "\n\n" + cascade_context
    if learnings_context:
        system += "\n\n" + learnings_context

    return await agent.assess(ExecuteResult, prompt, system, max_tokens=32768)
