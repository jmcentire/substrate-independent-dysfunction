#!/usr/bin/env python3
"""Org Swarm experiment wrapper.

Bridges the existing swarm pipeline (installed from ~/WanderRepos/swarm)
with the experiment's shared instrumentation format.

The swarm implements a gated-review hierarchy:
  diagnose → decompose → architect → review → locate → execute →
  test → verify → review → submit

Usage:
    python experiments/org_swarm/run.py \
        --task "Build a CLI book library manager..." \
        --budget 10.0 \
        --output experiments/org_swarm/simple \
        --run-id run001
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Shared infrastructure
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.instrumentation import InstrumentationLogger  # noqa: E402

DEFAULT_MODEL = "claude-opus-4-6"


def setup_swarm_project(
    project_dir: Path,
    task: str,
    budget: float,
    repo_path: Path,
) -> None:
    """Initialize a swarm project directory for this experiment run."""
    # Use swarm CLI to init (creates .swarm/, swarm.yaml, task.md)
    subprocess.run(
        ["swarm", "init", str(project_dir),
         "--repo", str(repo_path),
         "--budget", str(budget)],
        check=True,
        capture_output=True,
        text=True,
    )

    # Overwrite task.md with experiment prompt
    (project_dir / "task.md").write_text(f"# Task\n\n{task}\n")

    # Configure swarm.yaml for experiment:
    # - autonomous mode (no human interaction)
    # - force ALL stages to same model (no per-stage routing)
    # - force ALL stages to anthropic backend (no claude_code backend)
    config_path = project_dir / "swarm.yaml"
    import yaml
    config = {}
    if config_path.exists():
        config = yaml.safe_load(config_path.read_text()) or {}
    config["io_mode"] = "autonomous"
    config["repo"] = str(repo_path)
    config["budget"] = budget
    config["backend"] = "anthropic"
    # Override per-stage model routing: every stage uses the experiment model.
    # The global config.yaml routes some stages to Sonnet/Haiku — we must
    # override at the project level to ensure a controlled experiment.
    all_stages = [
        "diagnose", "architect", "decompose", "architect_review",
        "locate", "execute", "test", "review", "integrate",
        "verify", "submit",
    ]
    config["stage_models"] = {s: "claude-opus-4-6" for s in all_stages}
    config["stage_backends"] = {s: "anthropic" for s in all_stages}
    config_path.write_text(yaml.dump(config, default_flow_style=False))


def convert_state_json(
    state_path: Path,
    logger: InstrumentationLogger,
) -> dict | None:
    """Convert swarm's .swarm/state.json to experiment JSONL events.

    Returns the parsed state dict, or None if not found.
    """
    if not state_path.exists():
        return None

    state = json.loads(state_path.read_text())

    # Convert each stage to a log event
    stages = state.get("stages", [])
    for stage in stages:
        stage_name = stage.get("stage", "unknown")
        passed = stage.get("passed", False)
        detail = stage.get("detail", "")
        tokens_in = stage.get("tokens_in", 0)
        tokens_out = stage.get("tokens_out", 0)
        cost = stage.get("cost_usd", 0.0)

        summary = f"{stage_name}: {'passed' if passed else 'REJECTED'}"
        if detail:
            summary += f" — {detail[:150]}"

        logger.log(
            agent_id=stage_name,
            event_type="work_produced" if passed else "decision_made",
            summary=summary,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            extra={
                "passed": passed,
                "cost_usd": cost,
                "confidence": stage.get("confidence"),
            },
        )

    return state


def main():
    parser = argparse.ArgumentParser(description="Org Swarm experiment runner")
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

    # Set up experiment logger
    logger = InstrumentationLogger(run_dir / "log.jsonl")

    started_at = datetime.now(timezone.utc)
    logger.log("system", "run_started", f"Org Swarm | budget=${args.budget}")

    # Create swarm project inside run directory
    # The swarm project dir gets its own .swarm/ state
    # Code artifacts go to work_dir (output/)
    project_dir = run_dir / ".swarm_project"
    error = None
    state = None

    try:
        setup_swarm_project(project_dir, task, args.budget, repo_path=work_dir)

        # Run the swarm
        print(f"Starting Org Swarm on {project_dir}...")
        result = subprocess.run(
            ["swarm", "run", str(project_dir), "--force-new"],
            capture_output=True,
            text=True,
            timeout=7200,  # 2 hour timeout
            env={**__import__("os").environ},
        )

        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        if result.returncode != 0:
            error = f"swarm exited with code {result.returncode}"

    except subprocess.TimeoutExpired:
        error = "Timeout (2 hours)"
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        traceback.print_exc()

    completed_at = datetime.now(timezone.utc)
    wall_seconds = logger.elapsed_seconds()

    # Convert swarm state to experiment log
    state_path = project_dir / ".swarm" / "state.json"
    state = convert_state_json(state_path, logger)

    if error:
        logger.log("system", "error", error)

    # Extract metrics from state
    spent_usd = state.get("total_cost_usd", 0.0) if state else None
    total_tokens = state.get("total_tokens", 0) if state else None
    run_status = state.get("status", "unknown") if state else "error"
    stages = state.get("stages", []) if state else []

    if run_status == "budget_exceeded":
        logger.log("system", "budget_exceeded",
                   f"${spent_usd:.4f} spent, cap was ${args.budget:.2f}")

    # Enumerate produced files (exclude .swarm/ internals)
    files_produced = []
    if work_dir.exists():
        files_produced = [
            str(p.relative_to(work_dir))
            for p in sorted(work_dir.rglob("*"))
            if p.is_file() and ".swarm" not in p.parts
        ]

    logger.log(
        "system", "run_completed",
        f"${spent_usd or 0:.2f} spent, {len(files_produced)} files, {wall_seconds:.0f}s",
    )

    # Compute per-stage token breakdown if available
    total_in = sum(s.get("tokens_in", 0) for s in stages)
    total_out = sum(s.get("tokens_out", 0) for s in stages)

    # Write result.json
    result_data = {
        "architecture": "org_swarm",
        "run_id": args.run_id,
        "task_prompt": task,
        "model": args.model,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "wall_seconds": round(wall_seconds, 1),
        "budget": {
            "cap_usd": args.budget,
            "spent_usd": round(spent_usd, 4) if spent_usd else None,
            "total_input_tokens": total_in or None,
            "total_output_tokens": total_out or None,
            "total_tokens": total_tokens,
        },
        "swarm_state": {
            "status": run_status,
            "formation": state.get("formation") if state else None,
            "stages_total": len(stages),
            "stages_passed": sum(1 for s in stages if s.get("passed")),
            "stages_rejected": sum(1 for s in stages if not s.get("passed")),
        },
        "files_produced": files_produced,
        "error": error,
    }
    (run_dir / "result.json").write_text(json.dumps(result_data, indent=2) + "\n")

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"Org Swarm — {args.run_id}")
    print(f"{'=' * 60}")
    print(f"Status:  {run_status}")
    print(f"Cost:    ${spent_usd or 0:.4f} / ${args.budget:.2f}")
    if total_tokens:
        print(f"Tokens:  {total_tokens:,}")
    print(f"Time:    {wall_seconds:.0f}s")
    print(f"Files:   {len(files_produced)}")
    print(f"Stages:  {sum(1 for s in stages if s.get('passed'))}/{len(stages)} passed")
    if error:
        print(f"Error:   {error}")
    print(f"Output:  {run_dir}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
