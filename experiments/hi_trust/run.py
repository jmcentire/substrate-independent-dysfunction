#!/usr/bin/env python3
"""Hi-Trust Hierarchy: recursive decompose-delegate-assemble agent tree.

A root agent receives a task, decides whether to execute directly or
decompose into subtasks.  Subtasks are delegated to child agents with
full trust — no review, no approval gates.  Leaf agents produce code.
Assembly flows back up: each parent integrates children's outputs.

Usage:
    python experiments/hi_trust/run.py \
        --task "Build a CLI book library manager..." \
        --budget 10.0 \
        --output experiments/hi_trust/simple \
        --run-id run001
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic

# ---------------------------------------------------------------------------
# Shared infrastructure
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.instrumentation import (  # noqa: E402
    BudgetExceeded,
    BudgetTracker,
    InstrumentationLogger,
)
from shared.tools import make_tools  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_DEPTH = 4
MAX_CHILDREN = 5
DEFAULT_MODEL = "claude-opus-4-6"
MAX_TOOL_ITERATIONS = 50  # per agent loop (increased for multi-service tasks)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
DECOMPOSE_SYSTEM = """\
You are a technical lead deciding how to approach a software engineering task.

Given a task description, decide ONE of:
  (a) "execute" — the task is small enough to implement directly (~200 lines or fewer).
  (b) "decompose" — the task should be split into 2-{max_children} independent subtasks.

Rules:
- Each subtask must be independently implementable (no circular dependencies).
- Subtasks should produce concrete code artifacts, not abstract designs.
- If this task was delegated with specific scope, respect that scope.
- When in doubt, prefer fewer, larger subtasks over many tiny ones.
- Maximum subtask count: {max_children}.

{force_execute}

Respond with ONLY a JSON object (no markdown fencing):
{{
  "decision": "execute" | "decompose",
  "reasoning": "one sentence explaining why",
  "subtasks": [  // only if decompose
    {{"id": "short-slug", "description": "what to build, acceptance criteria, file names"}},
    ...
  ]
}}
"""

LEAF_SYSTEM = """\
You are a software engineer building one component of a larger system.

Your task is described below.  Write clean, working code using the provided
file tools.  Create all necessary files in the working directory.

Rules:
- Write complete, runnable code — not stubs or pseudocode.
- Do NOT build components assigned to other engineers.  Stick to your scope.
- If you need to import from sibling components, use standard import conventions
  and document the expected interface with a brief comment.
- Include a brief README.md or docstring explaining what you built and how to use it.
- Test your code by running it if possible (use the run_command tool).

Parent task context (for understanding overall scope):
{parent_context}

YOUR TASK:
{task}
"""

ASSEMBLY_SYSTEM = """\
You are an integration engineer.  Child engineers have built components in the
working directory.  Your job is to wire them together into a coherent product.

Rules:
- Do NOT rewrite existing code.  Integrate what's there.
- Create entry points, fix imports, add glue code as needed.
- If components have interface mismatches, write thin adapter code.
- Run the final product to verify it works (use the run_command tool).
- If something is broken, fix the minimal amount needed to make it work.

The original high-level task:
{root_task}

What was delegated to children:
{children_summary}
"""


# ---------------------------------------------------------------------------
# Agent node
# ---------------------------------------------------------------------------
def make_agent_id(depth: int, index: int) -> str:
    """Generate a human-readable agent ID like 'root', 'L1-0', 'L2-1'."""
    if depth == 0:
        return "root"
    return f"L{depth}-{index}"


def run_agent_node(
    *,
    client: Anthropic,
    model: str,
    agent_id: str,
    task: str,
    parent_context: str,
    depth: int,
    child_index: int,
    work_dir: Path,
    budget: BudgetTracker,
    logger: InstrumentationLogger,
) -> str:
    """Run one agent node.  Returns a text summary of what was produced."""

    logger.log(agent_id, "task_received", task[:200])

    # ------------------------------------------------------------------
    # Step 1: Decide — execute directly or decompose?
    # ------------------------------------------------------------------
    force = ""
    if depth >= MAX_DEPTH:
        force = "You MUST choose 'execute' — maximum decomposition depth reached."

    decision_data = _decide(
        client=client,
        model=model,
        agent_id=agent_id,
        task=task,
        depth=depth,
        force_execute=force,
        budget=budget,
        logger=logger,
    )

    decision = decision_data.get("decision", "execute")
    reasoning = decision_data.get("reasoning", "")
    subtasks = decision_data.get("subtasks", [])

    if decision == "decompose" and subtasks:
        logger.log(
            agent_id,
            "decision_made",
            f"Decompose into {len(subtasks)} subtasks: {reasoning}",
            extra={"subtask_ids": [s.get("id", "?") for s in subtasks]},
        )
    else:
        decision = "execute"  # normalize
        logger.log(agent_id, "decision_made", f"Execute directly: {reasoning}")

    # ------------------------------------------------------------------
    # Step 2a: Leaf execution
    # ------------------------------------------------------------------
    if decision == "execute":
        summary = _execute_leaf(
            client=client,
            model=model,
            agent_id=agent_id,
            task=task,
            parent_context=parent_context,
            work_dir=work_dir,
            budget=budget,
            logger=logger,
        )
        return summary

    # ------------------------------------------------------------------
    # Step 2b: Decompose — delegate to children, then assemble
    # ------------------------------------------------------------------
    subtasks = subtasks[:MAX_CHILDREN]
    children_summaries = []

    for i, subtask in enumerate(subtasks):
        child_id = make_agent_id(depth + 1, i)
        sub_desc = subtask.get("description", str(subtask))
        child_context = f"Root task: {task}\nThis subtask: {sub_desc}"

        child_summary = run_agent_node(
            client=client,
            model=model,
            agent_id=child_id,
            task=sub_desc,
            parent_context=child_context,
            depth=depth + 1,
            child_index=i,
            work_dir=work_dir,
            budget=budget,
            logger=logger,
        )
        children_summaries.append(
            {"id": subtask.get("id", f"child-{i}"), "summary": child_summary}
        )

    # ------------------------------------------------------------------
    # Step 3: Assembly — integrate children's outputs
    # ------------------------------------------------------------------
    logger.log(
        agent_id,
        "assembly_started",
        f"Assembling {len(children_summaries)} children's work",
    )

    assembly_summary = _assemble(
        client=client,
        model=model,
        agent_id=agent_id,
        root_task=task,
        children_summaries=children_summaries,
        work_dir=work_dir,
        budget=budget,
        logger=logger,
    )

    logger.log(
        agent_id,
        "assembled_from",
        assembly_summary[:200],
        extra={"children": [c["id"] for c in children_summaries]},
    )

    return assembly_summary


# ---------------------------------------------------------------------------
# Internal: decompose decision (single-shot, no tools)
# ---------------------------------------------------------------------------
def _decide(
    *,
    client: Anthropic,
    model: str,
    agent_id: str,
    task: str,
    depth: int,
    force_execute: str,
    budget: BudgetTracker,
    logger: InstrumentationLogger,
) -> dict:
    """Single-shot API call to decide execute vs decompose."""
    system = DECOMPOSE_SYSTEM.format(
        max_children=MAX_CHILDREN, force_execute=force_execute
    )
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": task}],
    )
    budget.record(response.usage.input_tokens, response.usage.output_tokens)
    logger.log(
        agent_id,
        "api_call",
        "decompose decision",
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
    )

    text = response.content[0].text if response.content else "{}"
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    text = re.sub(r"\n?\s*```$", "", text.strip())
    # Try to extract JSON object if surrounded by other text
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.log(agent_id, "error", f"Failed to parse decision JSON: {text[:200]}")
        return {"decision": "execute", "reasoning": "Failed to parse decision JSON"}


# ---------------------------------------------------------------------------
# Internal: leaf execution (tool-use loop)
# ---------------------------------------------------------------------------
def _execute_leaf(
    *,
    client: Anthropic,
    model: str,
    agent_id: str,
    task: str,
    parent_context: str,
    work_dir: Path,
    budget: BudgetTracker,
    logger: InstrumentationLogger,
) -> str:
    """Run a leaf agent that produces code using tools."""
    system = LEAF_SYSTEM.format(parent_context=parent_context, task=task)
    tools = make_tools(work_dir)

    summary = _run_tool_loop(
        client=client,
        model=model,
        agent_id=agent_id,
        system=system,
        user_message=f"Please implement the following:\n\n{task}",
        tools=tools,
        work_dir=work_dir,
        budget=budget,
        logger=logger,
        event_type="work_produced",
    )
    return summary


# ---------------------------------------------------------------------------
# Internal: assembly (tool-use loop)
# ---------------------------------------------------------------------------
def _assemble(
    *,
    client: Anthropic,
    model: str,
    agent_id: str,
    root_task: str,
    children_summaries: list[dict],
    work_dir: Path,
    budget: BudgetTracker,
    logger: InstrumentationLogger,
) -> str:
    """Run an assembly agent that integrates children's outputs."""
    children_text = "\n".join(
        f"- [{c['id']}]: {c['summary']}" for c in children_summaries
    )
    system = ASSEMBLY_SYSTEM.format(
        root_task=root_task, children_summary=children_text
    )
    tools = make_tools(work_dir)

    summary = _run_tool_loop(
        client=client,
        model=model,
        agent_id=agent_id,
        system=system,
        user_message=(
            "Please integrate the children's work into a coherent product. "
            "Start by listing the current directory to see what exists."
        ),
        tools=tools,
        work_dir=work_dir,
        budget=budget,
        logger=logger,
        event_type="assembled_from",
    )
    return summary


# ---------------------------------------------------------------------------
# Internal: tool-use loop via SDK tool_runner
# ---------------------------------------------------------------------------
def _run_tool_loop(
    *,
    client: Anthropic,
    model: str,
    agent_id: str,
    system: str,
    user_message: str,
    tools: list,
    work_dir: Path,
    budget: BudgetTracker,
    logger: InstrumentationLogger,
    event_type: str,
) -> str:
    """Run an agentic tool-use loop.  Returns the final text summary."""
    runner = client.beta.messages.tool_runner(
        model=model,
        max_tokens=16384,
        system=system,
        messages=[{"role": "user", "content": user_message}],
        tools=tools,
        max_iterations=MAX_TOOL_ITERATIONS,
    )

    final_text = ""
    for message in runner:
        # Record token usage for this turn
        if hasattr(message, "usage") and message.usage:
            budget.record(message.usage.input_tokens, message.usage.output_tokens)
            logger.log(
                agent_id,
                "api_call",
                f"tool loop turn ({message.stop_reason})",
                tokens_in=message.usage.input_tokens,
                tokens_out=message.usage.output_tokens,
            )
        # Capture final text blocks
        if message.stop_reason == "end_turn":
            for block in message.content:
                if hasattr(block, "text"):
                    final_text = block.text

    if not final_text:
        final_text = "(agent completed without text summary)"

    logger.log(agent_id, event_type, final_text[:200])
    return final_text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Hi-Trust Hierarchy experiment runner")
    parser.add_argument("--task", default=None, help="Task prompt (inline)")
    parser.add_argument("--task-file", default=None, help="Path to task prompt file")
    parser.add_argument("--budget", type=float, required=True, help="USD budget cap")
    parser.add_argument("--output", required=True, help="Base output directory")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Anthropic model ID")
    args = parser.parse_args()

    task = args.task
    if args.task_file:
        task = Path(args.task_file).read_text()
    if not task:
        parser.error("Either --task or --task-file is required")

    # Set up run directory
    run_dir = Path(args.output) / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    work_dir = run_dir / "output"
    work_dir.mkdir(exist_ok=True)

    # Save task for reproducibility
    (run_dir / "task.txt").write_text(task)

    # Initialize shared infrastructure
    budget = BudgetTracker(cap_usd=args.budget)
    logger = InstrumentationLogger(run_dir / "log.jsonl")
    client = Anthropic()  # reads ANTHROPIC_API_KEY

    started_at = datetime.now(timezone.utc)
    logger.log("system", "run_started", f"Hi-Trust Hierarchy | budget=${args.budget}")

    error = None
    try:
        run_agent_node(
            client=client,
            model=args.model,
            agent_id="root",
            task=task,
            parent_context="(this is the root task)",
            depth=0,
            child_index=0,
            work_dir=work_dir,
            budget=budget,
            logger=logger,
        )
    except BudgetExceeded as e:
        error = str(e)
        logger.log("system", "budget_exceeded", error)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        logger.log("system", "error", error)
        traceback.print_exc()

    completed_at = datetime.now(timezone.utc)
    wall_seconds = logger.elapsed_seconds()

    # Enumerate produced files
    files_produced = []
    if work_dir.exists():
        files_produced = [
            str(p.relative_to(work_dir)) for p in sorted(work_dir.rglob("*")) if p.is_file()
        ]

    logger.log(
        "system",
        "run_completed",
        f"${budget.spent_usd:.2f} spent, {len(files_produced)} files, {wall_seconds:.0f}s",
    )

    # Write result summary
    result = {
        "architecture": "hi_trust",
        "run_id": args.run_id,
        "task_prompt": task,
        "model": args.model,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "wall_seconds": round(wall_seconds, 1),
        "budget": budget.summary(),
        "files_produced": files_produced,
        "error": error,
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"Hi-Trust Hierarchy — {args.run_id}")
    print(f"{'=' * 60}")
    print(f"Cost:    ${budget.spent_usd:.4f} / ${args.budget:.2f}")
    print(f"Tokens:  {budget.total_input_tokens:,} in + {budget.total_output_tokens:,} out")
    print(f"Time:    {wall_seconds:.0f}s")
    print(f"Files:   {len(files_produced)}")
    if error:
        print(f"Error:   {error}")
    print(f"Output:  {run_dir}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
