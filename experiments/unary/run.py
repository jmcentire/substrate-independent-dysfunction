#!/usr/bin/env python3
"""Unary experiment runner: single agent, no coordination.

One Opus 4.6 agent with the same sandboxed tools as all other architectures.
No decomposition, no delegation, no review. The control condition — tests
whether coordination topology adds or subtracts value.

Uses the Anthropic API directly (not Claude Code CLI) to ensure model
consistency across all four architectures.

Usage:
    python experiments/unary/run.py \
        --task "Build the Anonymous Identity system..." \
        --budget 10.0 \
        --output experiments/unary/simple \
        --run-id run001
"""

from __future__ import annotations

import argparse
import json
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
DEFAULT_MODEL = "claude-opus-4-6"
MAX_TOOL_ITERATIONS = 100  # generous — single agent needs room for multi-service tasks

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are a software engineer. Build exactly what is asked for using the \
provided file tools. Write clean, working code. Create all necessary files \
in the working directory. Test your code by running it if possible (use \
the run_command tool). Include a test suite that demonstrates the required \
properties hold.
"""


def run(
    task: str,
    budget: float,
    output_dir: Path,
    run_id: str,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Execute one unary (single agent, no coordination) run."""
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    work_dir = run_dir / "output"
    work_dir.mkdir(exist_ok=True)

    # Save task for reproducibility
    (run_dir / "task.txt").write_text(task)

    # Initialize shared infrastructure
    budget_tracker = BudgetTracker(cap_usd=budget)
    logger = InstrumentationLogger(run_dir / "log.jsonl")
    client = Anthropic()  # reads ANTHROPIC_API_KEY
    tools = make_tools(work_dir)

    started_at = datetime.now(timezone.utc)
    logger.log("system", "run_started", f"Unary (single agent) | budget=${budget}")
    logger.log("agent", "task_received", task[:200])

    error = None
    final_text = ""

    try:
        # Single agentic tool-use loop — same API as hi-trust leaf nodes
        runner = client.beta.messages.tool_runner(
            model=model,
            max_tokens=16384,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Please implement the following:\n\n{task}"}],
            tools=tools,
            max_iterations=MAX_TOOL_ITERATIONS,
        )

        for message in runner:
            if hasattr(message, "usage") and message.usage:
                budget_tracker.record(
                    message.usage.input_tokens,
                    message.usage.output_tokens,
                )
                logger.log(
                    "agent", "api_call",
                    f"tool loop turn ({message.stop_reason})",
                    tokens_in=message.usage.input_tokens,
                    tokens_out=message.usage.output_tokens,
                )
            if message.stop_reason == "end_turn":
                for block in message.content:
                    if hasattr(block, "text"):
                        final_text = block.text

    except BudgetExceeded as e:
        error = str(e)
        logger.log("system", "budget_exceeded", error)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        logger.log("system", "error", error)
        traceback.print_exc()

    if final_text:
        logger.log("agent", "work_produced", final_text[:200])

    completed_at = datetime.now(timezone.utc)
    wall_seconds = logger.elapsed_seconds()

    # Enumerate produced files
    files_produced = []
    if work_dir.exists():
        files_produced = [
            str(p.relative_to(work_dir))
            for p in sorted(work_dir.rglob("*"))
            if p.is_file()
        ]

    logger.log(
        "system", "run_completed",
        f"${budget_tracker.spent_usd:.2f} spent, {len(files_produced)} files, {wall_seconds:.0f}s",
    )

    # Write result.json
    result = {
        "architecture": "unary",
        "run_id": run_id,
        "task_prompt": task,
        "model": model,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "wall_seconds": round(wall_seconds, 1),
        "budget": budget_tracker.summary(),
        "files_produced": files_produced,
        "error": error,
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"Unary (single agent) — {run_id}")
    print(f"{'=' * 60}")
    print(f"Cost:    ${budget_tracker.spent_usd:.4f} / ${budget:.2f}")
    print(f"Tokens:  {budget_tracker.total_input_tokens:,} in + {budget_tracker.total_output_tokens:,} out")
    print(f"Time:    {wall_seconds:.0f}s")
    print(f"Files:   {len(files_produced)}")
    if error:
        print(f"Error:   {error}")
    print(f"Output:  {run_dir}")
    print(f"{'=' * 60}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Unary (single agent) experiment runner")
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

    run(
        task=task,
        budget=args.budget,
        output_dir=Path(args.output),
        run_id=args.run_id,
        model=args.model,
    )


if __name__ == "__main__":
    main()
