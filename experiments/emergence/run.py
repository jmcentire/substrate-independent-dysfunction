#!/usr/bin/env python3
"""Emergence Swarm experiment wrapper.

Bridges the emergence swarm package (installed from ~/WanderRepos/emergence)
with the experiment's shared instrumentation format.

Sets up a fresh emergence project, runs the swarm, then converts the
native signal log to the experiment JSONL format and writes result.json.

Usage:
    python experiments/emergence/run.py \
        --task "Build a CLI book library manager..." \
        --budget 10.0 \
        --output experiments/emergence/simple \
        --run-id run001
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Shared infrastructure
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.instrumentation import InstrumentationLogger  # noqa: E402

# ---------------------------------------------------------------------------
# Emergence imports
# ---------------------------------------------------------------------------
from emergence.config import EmergenceConfig  # noqa: E402
from emergence.orchestrator import run_swarm  # noqa: E402
from emergence.project import init_project  # noqa: E402

DEFAULT_MODEL = "claude-opus-4-6"


def setup_project(
    project_dir: Path,
    task: str,
    budget: float,
    model: str,
    num_agents: int,
    repo_path: Path,
) -> None:
    """Initialize an emergence project directory for this experiment run."""
    init_project(
        project_dir,
        repo=str(repo_path),
        num_agents=num_agents,
        budget=budget,
        model=model,
        backend="anthropic",
    )
    # Overwrite task.md with the experiment task prompt
    (project_dir / "task.md").write_text(task)


def convert_signal_log(
    emergence_dir: Path,
    experiment_logger: InstrumentationLogger,
) -> None:
    """Convert emergence's signal_log.jsonl into experiment JSONL format.

    Emergence signals have: ts, agent, event, summary, tokens, task_id
    Experiment format has:  ts, wall_s, agent, event, summary, tokens_in, tokens_out, extra

    We map `tokens` → `tokens_in` (emergence doesn't split in/out in signals)
    and carry `task_id` into `extra`.
    """
    signal_path = emergence_dir / "signal_log.jsonl"
    if not signal_path.exists():
        return

    for line in signal_path.read_text().strip().splitlines():
        if not line.strip():
            continue
        try:
            sig = json.loads(line)
        except json.JSONDecodeError:
            continue

        extra = {}
        if sig.get("task_id"):
            extra["task_id"] = sig["task_id"]

        experiment_logger.log(
            agent_id=sig.get("agent", "unknown"),
            event_type=sig.get("event", "unknown"),
            summary=sig.get("summary", ""),
            tokens_in=sig.get("tokens", 0),
            tokens_out=0,
            extra=extra or None,
        )


def run(
    task: str,
    budget: float,
    output_dir: Path,
    run_id: str,
    model: str = DEFAULT_MODEL,
    num_agents: int = 5,
) -> dict:
    """Execute one emergence swarm run and return the result dict."""
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    work_dir = run_dir / "output"
    work_dir.mkdir(exist_ok=True)

    # Save task for reproducibility
    (run_dir / "task.txt").write_text(task)

    # Set up experiment logger (writes bookend events + converted signals)
    exp_logger = InstrumentationLogger(run_dir / "log.jsonl")

    started_at = datetime.now(timezone.utc)
    exp_logger.log("system", "run_started",
                   f"Emergence Swarm | agents={num_agents} | budget=${budget}")

    # Set up emergence project inside the run directory.
    # The project dir is run_dir/.emergence_project/
    # The repo (where code artifacts go) is run_dir/output/
    project_dir = run_dir / ".emergence_project"
    setup_project(project_dir, task, budget, model, num_agents, repo_path=work_dir)

    # Build a config that points to the right config.yaml
    global_config = EmergenceConfig(
        model=model,
        num_agents=num_agents,
        max_iterations_per_agent=20,
        max_runtime_seconds=1800,
        default_budget=budget,
        agent_stagger_seconds=1.0,
        backend="anthropic",
    )

    error = None
    state = None
    try:
        state = asyncio.run(run_swarm(project_dir, global_config))
    except KeyboardInterrupt:
        error = "Interrupted"
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        traceback.print_exc()

    completed_at = datetime.now(timezone.utc)
    wall_seconds = exp_logger.elapsed_seconds()

    # Convert emergence signal log → experiment format
    emergence_dir = project_dir / ".emergence"
    convert_signal_log(emergence_dir, exp_logger)

    # Log budget exceeded if applicable
    if state and state.status.value == "budget_exceeded":
        exp_logger.log("system", "budget_exceeded",
                       f"${state.total_cost_usd:.4f} spent, cap was ${budget:.2f}")

    if error:
        exp_logger.log("system", "error", error)

    # Enumerate produced files
    files_produced = []
    if work_dir.exists():
        files_produced = [
            str(p.relative_to(work_dir))
            for p in sorted(work_dir.rglob("*"))
            if p.is_file()
        ]

    # Extract cost/token data from RunState
    spent_usd = state.total_cost_usd if state else 0.0
    total_tokens = state.total_tokens if state else 0
    # Emergence tracks combined tokens; estimate split from pricing ratio
    # (rough: assume 3:1 output:input cost ratio means ~80% are input tokens)
    est_input = int(total_tokens * 0.8) if total_tokens else 0
    est_output = total_tokens - est_input if total_tokens else 0

    exp_logger.log(
        "system", "run_completed",
        f"${spent_usd:.2f} spent, {len(files_produced)} files, {wall_seconds:.0f}s",
    )

    # Write result.json
    result = {
        "architecture": "emergence",
        "run_id": run_id,
        "task_prompt": task,
        "model": model,
        "num_agents": num_agents,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "wall_seconds": round(wall_seconds, 1),
        "budget": {
            "cap_usd": budget,
            "spent_usd": round(spent_usd, 4),
            "total_input_tokens": est_input,
            "total_output_tokens": est_output,
            "total_tokens": total_tokens,
        },
        "emergence_state": {
            "status": state.status.value if state else "error",
            "total_tasks": state.total_tasks if state else 0,
            "tasks_completed": state.tasks_completed if state else 0,
            "agents": [
                {
                    "agent_id": a.agent_id,
                    "status": a.status.value,
                    "tasks_completed": a.tasks_completed,
                    "tasks_created": a.tasks_created,
                    "tokens_used": a.tokens_used,
                    "iterations": a.iterations,
                }
                for a in (state.agents if state else [])
            ],
        },
        "files_produced": files_produced,
        "error": error,
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"Emergence Swarm — {run_id}")
    print(f"{'=' * 60}")
    print(f"Status:  {state.status.value if state else 'error'}")
    print(f"Cost:    ${spent_usd:.4f} / ${budget:.2f}")
    print(f"Tokens:  {total_tokens:,}")
    print(f"Time:    {wall_seconds:.0f}s")
    print(f"Files:   {len(files_produced)}")
    print(f"Tasks:   {state.tasks_completed if state else 0}/{state.total_tasks if state else 0} completed")
    if state:
        for a in state.agents:
            print(f"  {a.agent_id}: {a.status.value} "
                  f"(done={a.tasks_completed}, created={a.tasks_created}, "
                  f"iters={a.iterations})")
    if error:
        print(f"Error:   {error}")
    print(f"Output:  {run_dir}")
    print(f"{'=' * 60}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Emergence Swarm experiment runner")
    parser.add_argument("--task", default=None, help="Task prompt (inline)")
    parser.add_argument("--task-file", default=None, help="Path to task prompt file")
    parser.add_argument("--budget", type=float, required=True, help="USD budget cap")
    parser.add_argument("--output", required=True, help="Base output directory")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Anthropic model ID")
    parser.add_argument("--num-agents", type=int, default=8, help="Number of agents")
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
        num_agents=args.num_agents,
    )


if __name__ == "__main__":
    main()
