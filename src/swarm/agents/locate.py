"""Locate agent — Find specific files and code sections for the task.

Context boundary: sees task, diagnose.task_type, diagnose.complexity,
diagnose.language, architect.affected_files, architect.new_files, architect.steps.
Excluded: architect.reasoning, diagnose.reasoning.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from swarm.agents.base import AgentBase
from swarm.schemas import ArchitectResult, DiagnoseResult, LocateResult

SYSTEM_PROMPT = """You are a code locator agent for a programming tool.

Given a task and architecture plan, identify the specific files that need
to be created, modified, or read for context.

For each file, explain WHY it's relevant. Mark files as:
- primary: needs modification or creation
- context: needed to understand the codebase but probably doesn't need changes
- test: existing test file that should be updated or referenced
- new: file that needs to be created

Be specific with file paths — use the exact paths from the listing.
If you're unsure, lower your confidence score.

## Signals

Leave consternation and escalation BLANK unless something is genuinely wrong.
Do NOT use them to confirm that things are fine or to provide status updates.

- Set consternation ONLY if the architecture plan references files or patterns that
  don't exist in the repo (e.g., plan assumes a module structure that isn't there).
- Set escalation ONLY if the task requires changes to files outside the repo boundary
  (e.g., shared libraries, external configs, infrastructure files).
- If everything looks fine: leave both fields empty."""


async def get_file_tree(repo_path: Path) -> str:
    """Get the file listing for a repo (language-agnostic)."""
    proc = await asyncio.create_subprocess_exec(
        "find", str(repo_path / "src"), "-type", "f",
        "-not", "-path", "*/__pycache__/*",
        "-not", "-path", "*/node_modules/*",
        "-not", "-path", "*/.git/*",
        "-not", "-name", "*.pyc",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if not stdout:
        proc = await asyncio.create_subprocess_exec(
            "find", str(repo_path), "-type", "f",
            "-not", "-path", "*/node_modules/*",
            "-not", "-path", "*/.git/*",
            "-not", "-path", "*/__pycache__/*",
            "-not", "-name", "*.pyc",
            "-maxdepth", "4",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

    lines = stdout.decode().strip().splitlines()
    repo_str = str(repo_path) + "/"
    return "\n".join(line.replace(repo_str, "") for line in sorted(lines))


async def locate(
    agent: AgentBase,
    task: str,
    diagnose_result: DiagnoseResult,
    architect_result: ArchitectResult,
    repo_path: Path,
    learnings_context: str = "",
    cascade_context: str = "",
) -> tuple[LocateResult, int, int]:
    """Run file locator on a diagnosed task.

    Context boundary: locate sees task_type, complexity, language,
    architect.affected_files, architect.new_files, architect.steps.
    Excluded: architect.reasoning, diagnose.reasoning.
    """
    file_tree = await get_file_tree(repo_path)

    # Steps without reasoning
    steps_desc = "\n".join(
        f"- {step.description} (files: {', '.join(step.files_involved)})"
        for step in architect_result.steps
    )

    prompt = (
        f"Task type: {diagnose_result.task_type}\n"
        f"Complexity: {diagnose_result.complexity}\n"
        f"Language: {diagnose_result.language}\n\n"
        f"Architecture plan affected files: {', '.join(architect_result.affected_files)}\n"
        f"New files to create: {', '.join(architect_result.new_files) if architect_result.new_files else 'none'}\n"
        f"Steps:\n{steps_desc}\n\n"
        f"Task: {task}\n\n"
        f"File listing ({repo_path.name}):\n{file_tree}"
    )

    system = SYSTEM_PROMPT
    if cascade_context:
        system += "\n\n" + cascade_context
    if learnings_context:
        system += "\n\n" + learnings_context

    return await agent.assess(LocateResult, prompt, system)
