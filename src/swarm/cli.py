"""CLI — init, run, resume, status, learn, health.

Entry point for the swarm command-line tool.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from swarm.budget import BudgetExceeded, BudgetTracker
from swarm.config import load_project_config, load_swarm_config
from swarm.human_io import create_io
from swarm.lifecycle import create_run, format_run_summary, record_stage
from swarm.lifecycle_monitor import LifecycleMonitor
from swarm.manager import PipelineManager, PipelineState, SignalBus
from swarm.memory import LearningsStore
from swarm.project import ProjectManager

logger = logging.getLogger(__name__)


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize a new project directory."""
    pm = ProjectManager(args.project)
    pm.init(
        repo=args.repo or "",
        formation=args.formation or "auto",
        io_mode=args.io or "interactive",
        budget=args.budget or 5.0,
    )
    print(f"Project initialized: {pm.project_dir}")
    print(f"  Edit {pm.task_path} to describe your task")
    print(f"  Edit {pm.config_path} to configure settings")
    print(f"  Then run: swarm run {args.project}")


def cmd_run(args: argparse.Namespace) -> None:
    """Execute the pipeline for a project."""
    pm = ProjectManager(args.project)

    # Auto-detect paused state → resume instead
    if getattr(args, "force_new", False):
        if pm.has_state():
            pm.clear_state()
            print("Cleared previous run state")
    elif pm.has_state():
        existing = pm.load_state()
        if existing.status in ("paused", "budget_exceeded"):
            if existing.status == "budget_exceeded":
                existing.status = "active"
                existing.pause_reason = ""
                print(f"Run {existing.id} hit budget — resuming with fresh budget")
            else:
                print(f"Found paused run {existing.id} — resuming")
            _do_resume(pm, existing)
            return

    if not pm.task_path.exists():
        print(f"Error: No task.md found in {pm.project_dir}", file=sys.stderr)
        print(f"Run 'swarm init {args.project}' first", file=sys.stderr)
        sys.exit(1)

    task = pm.load_task()
    config = pm.load_config()
    swarm_config = load_swarm_config()

    # Parse redo components
    redo_components = set()
    if getattr(args, "redo", None):
        redo_components = set(args.redo.split(","))
        # Clear the selected components so they get rebuilt
        cleared = pm.clear_components(list(redo_components))
        if cleared:
            print(f"Cleared components for redo: {', '.join(cleared)}")

    # Create run
    run = pm.create_run()
    run_id = run.id

    if args.dry_run:
        print(f"[DRY RUN] Would execute pipeline for {pm.project_dir}")
        print(f"  Task: {task[:100]}...")
        print(f"  Formation: {config.formation}")
        print(f"  IO mode: {config.io_mode}")
        print(f"  Budget: ${config.budget:.2f}")
        if redo_components:
            print(f"  Redo: {', '.join(redo_components)}")
        return

    print(f"Starting run {run_id} for {pm.project_dir}")
    print(f"  Formation: {config.formation}")
    print(f"  IO mode: {config.io_mode}")
    print(f"  Budget: ${config.budget:.2f}")
    if redo_components:
        print(f"  Redo components: {', '.join(redo_components)}")

    try:
        asyncio.run(run_pipeline(pm, run, task, config, swarm_config, redo_components))
    except BudgetExceeded:
        run.mark_budget_exceeded()
        pm.save_state(run)
        print(f"Budget exceeded for run {run_id}")
    except KeyboardInterrupt:
        run.pause("User interrupted")
        pm.save_state(run)
        print(f"\nRun {run_id} paused (interrupted)")
    except Exception as e:
        run.fail(str(e))
        pm.save_state(run)
        print(f"Run {run_id} failed: {e}", file=sys.stderr)
        raise

    print(f"\n{format_run_summary(run)}")
    _print_resume_hint(pm, run)


async def run_pipeline(pm, run, task, config, swarm_config, redo_components=None):
    """Execute the formation-driven pipeline.

    Creates PipelineManager and delegates to it.
    """
    budget = BudgetTracker(per_project_cap=config.budget)
    model = config.model or swarm_config.model
    budget.set_model_pricing(model)
    budget.start_project()

    try:
        from swarm.agents.base import AgentBase
        backend = config.backend or "anthropic"
        agent = AgentBase(budget, model, backend=backend)
    except (ImportError, ValueError) as e:
        run.fail(f"Agent init failed: {e}")
        pm.save_state(run)
        return

    human_io = create_io(config.io_mode, pm.project_dir)

    try:
        manager = PipelineManager(
            pm=pm, run=run, task=task,
            config=config, swarm_config=swarm_config,
            budget=budget, agent=agent, human_io=human_io,
            redo_components=redo_components,
        )
        await manager.run()
    finally:
        await agent.close()


def cmd_resume(args: argparse.Namespace) -> None:
    """Resume a paused pipeline."""
    pm = ProjectManager(args.project)

    if not pm.has_state():
        print(f"Error: No saved state in {pm.project_dir}", file=sys.stderr)
        sys.exit(1)

    run = pm.load_state()
    if run.status not in ("paused", "budget_exceeded"):
        print(f"Run {run.id} is {run.status}, not resumable", file=sys.stderr)
        sys.exit(1)

    if run.status == "budget_exceeded":
        run.status = "active"
        run.pause_reason = ""
        print("Budget was exceeded — resuming with fresh budget")

    _do_resume(pm, run, resume_message=getattr(args, "message", "") or "")


def _do_resume(pm: ProjectManager, run, resume_message: str = "") -> None:
    """Shared resume logic for cmd_resume and auto-resume."""
    config = pm.load_config()
    swarm_config = load_swarm_config()

    print(f"Resuming run {run.id} from stage index {run.current_stage_index}")
    print(f"  Paused reason: {run.pause_reason}")
    print(f"  Formation: {run.formation}")
    if resume_message:
        print(f"  Resume message: {resume_message}")

    try:
        asyncio.run(resume_pipeline(pm, run, config, swarm_config, resume_message=resume_message))
    except BudgetExceeded:
        run.mark_budget_exceeded()
        pm.save_state(run)
        print(f"Budget exceeded for run {run.id}")
    except KeyboardInterrupt:
        run.pause("User interrupted")
        pm.save_state(run)
        print(f"\nRun {run.id} paused (interrupted)")
    except Exception as e:
        run.fail(str(e))
        pm.save_state(run)
        print(f"Run {run.id} failed: {e}", file=sys.stderr)
        raise

    print(f"\n{format_run_summary(run)}")
    _print_resume_hint(pm, run)


async def resume_pipeline(pm, run, config, swarm_config, resume_message: str = ""):
    """Resume a paused pipeline from saved state."""
    task = pm.load_task()
    budget = BudgetTracker(per_project_cap=config.budget)
    model = config.model or swarm_config.model
    budget.set_model_pricing(model)
    budget.start_project()

    try:
        from swarm.agents.base import AgentBase
        backend = config.backend or "anthropic"
        agent = AgentBase(budget, model, backend=backend)
    except (ImportError, ValueError) as e:
        run.fail(f"Agent init failed: {e}")
        pm.save_state(run)
        return

    human_io = create_io(config.io_mode, pm.project_dir)

    try:
        manager = PipelineManager(
            pm=pm, run=run, task=task,
            config=config, swarm_config=swarm_config,
            budget=budget, agent=agent, human_io=human_io,
        )
        await manager.resume(run.current_stage_index, resume_message=resume_message)
    finally:
        await agent.close()


def _print_resume_hint(pm: ProjectManager, run) -> None:
    """Print resume instructions if the run is paused."""
    if run.status == "paused":
        state_path = pm.state_path
        print(f"\n  State saved: {state_path}")
        print(f"  Resume with: swarm run {pm.project_dir}")
        print(f"           or: swarm resume {pm.project_dir}")


def cmd_status(args: argparse.Namespace) -> None:
    """Show pipeline state and history."""
    pm = ProjectManager(args.project)

    if not pm.has_state():
        print(f"No pipeline state for {pm.project_dir}")
        outputs = pm.list_stage_outputs()
        if outputs:
            print(f"  Stage outputs: {', '.join(outputs)}")
        return

    run = pm.load_state()
    print(format_run_summary(run))

    # Show stage outputs
    outputs = pm.list_stage_outputs()
    if outputs:
        print(f"\n  Available stage outputs: {', '.join(outputs)}")

    # Show audit trail
    audit = pm.load_audit()
    if audit:
        print(f"\n  Audit trail ({len(audit)} entries):")
        for entry in audit[-10:]:
            status = "PASS" if entry["passed"] else "FAIL"
            print(f"    [{status}] {entry['stage']}: {entry.get('detail', '')[:60]}")


def cmd_learn(args: argparse.Namespace) -> None:
    """Extract learnings from a completed project."""
    pm = ProjectManager(args.project)

    if not pm.has_state():
        print(f"Error: No state for {pm.project_dir}", file=sys.stderr)
        sys.exit(1)

    run = pm.load_state()
    if run.status != "completed":
        print(f"Warning: Run {run.id} is {run.status}, not completed")

    # Show summary
    print(f"Run {run.id}: {run.status}")
    print(f"  Formation: {run.formation}")
    print(f"  Cost: ${run.total_cost_usd:.4f}")
    print(f"  Stages: {len(run.stages)}")


def cmd_components(args: argparse.Namespace) -> None:
    """List components and their build status for an epic project."""
    pm = ProjectManager(args.project)

    # Load decomposition plan for component names
    plan_data = pm.load_stage_output("decompose")
    components_data = pm.list_components()

    if not plan_data and not components_data:
        print(f"No components found for {pm.project_dir}")
        print("  Run an epic pipeline first to generate components")
        return

    # Build name lookup from plan
    name_map = {}
    if plan_data and "component_map" in plan_data:
        cmap = plan_data["component_map"]
        for comp in cmap.get("components", []):
            name_map[comp["id"]] = comp.get("name", comp["id"])

    # Also show components from plan that haven't been built yet
    plan_ids = set(name_map.keys())
    built_ids = {c["component_id"] for c in components_data}
    unbuilt = plan_ids - built_ids

    print(f"Components for {pm.project_dir}:\n")
    print(f"  {'ID':<25} {'Name':<30} {'Status':<12} {'Stages':<8} Issues")
    print(f"  {'─'*25} {'─'*30} {'─'*12} {'─'*8} {'─'*30}")

    for comp in components_data:
        cid = comp["component_id"]
        name = name_map.get(cid, cid)
        status = comp["status"]
        stages = len(comp.get("stages_completed", []))
        issues = "; ".join(comp.get("issues", []))[:50]
        marker = "✓" if status == "completed" else "✗" if status == "failed" else "…"
        print(f"  {cid:<25} {name:<30} {marker} {status:<10} {stages:<8} {issues}")

    for cid in sorted(unbuilt):
        name = name_map.get(cid, cid)
        print(f"  {cid:<25} {name:<30} — not started  —        —")

    # Summary
    completed = sum(1 for c in components_data if c["status"] == "completed")
    failed = sum(1 for c in components_data if c["status"] == "failed")
    total = len(plan_ids) if plan_ids else len(components_data)
    print(f"\n  {completed}/{total} completed, {failed} failed, {len(unbuilt)} not started")

    if failed > 0:
        failed_ids = [c["component_id"] for c in components_data if c["status"] == "failed"]
        print(f"\n  Redo failed: swarm run {pm.project_dir} --redo {','.join(failed_ids)}")


def cmd_health(args: argparse.Namespace) -> None:
    """System health check."""
    swarm_dir = Path(args.dir) if args.dir else Path(".")

    monitor = LifecycleMonitor(swarm_dir)
    report = monitor.check_health([])
    print(report.format())


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="swarm",
        description="Emergence-based autonomous programming tool",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize a new project")
    init_parser.add_argument("project", help="Project directory name")
    init_parser.add_argument("--repo", help="Path to the target repo")
    init_parser.add_argument("--formation", help="Formation: auto, quick, standard, thorough, custom")
    init_parser.add_argument("--io", help="IO mode: interactive, file, autonomous")
    init_parser.add_argument("--budget", type=float, help="Dollar budget cap")

    # run
    run_parser = subparsers.add_parser("run", help="Execute the pipeline")
    run_parser.add_argument("project", help="Project directory")
    run_parser.add_argument("--dry-run", action="store_true", help="Show what would happen without executing")
    run_parser.add_argument("--force-new", action="store_true", help="Start a new run even if a paused run exists")
    run_parser.add_argument("--redo", help="Comma-separated component IDs to rebuild (epic only)")

    # resume
    resume_parser = subparsers.add_parser("resume", help="Resume a paused pipeline")
    resume_parser.add_argument("project", help="Project directory")
    resume_parser.add_argument("-m", "--message", help="Message to inject as context for the next stage")

    # status
    status_parser = subparsers.add_parser("status", help="Show pipeline state")
    status_parser.add_argument("project", help="Project directory")

    # learn
    learn_parser = subparsers.add_parser("learn", help="Extract learnings from completed project")
    learn_parser.add_argument("project", help="Project directory")

    # components
    comp_parser = subparsers.add_parser("components", help="List epic pipeline components")
    comp_parser.add_argument("project", help="Project directory")

    # health
    health_parser = subparsers.add_parser("health", help="System health check")
    health_parser.add_argument("--dir", help="Directory to check (default: current)")

    # Shorthand: `swarm <project>` → auto-detect run or resume
    # Must intercept before parse_args() since argparse rejects unknown subcommands
    _known_commands = {"init", "run", "resume", "status", "learn", "components", "health", "-v", "--verbose", "-h", "--help"}
    if len(sys.argv) >= 2 and sys.argv[1] not in _known_commands:
        project = sys.argv[1]
        force_new = "--force-new" in sys.argv
        if "-v" in sys.argv or "--verbose" in sys.argv:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.WARNING)
        pm = ProjectManager(project)
        if not force_new and pm.has_state():
            existing = pm.load_state()
            if existing.status in ("paused", "budget_exceeded"):
                if existing.status == "budget_exceeded":
                    existing.status = "active"
                    existing.pause_reason = ""
                    print(f"Run {existing.id} hit budget — resuming with fresh budget")
                _do_resume(pm, existing)
                return
        if pm.task_path.exists():
            # Rewrite argv so argparse sees: run <project> [flags]
            extra_flags = [a for a in sys.argv[2:] if a.startswith("-")]
            sys.argv = [sys.argv[0], "run", project] + extra_flags
        else:
            print(f"No task.md or paused state in {pm.project_dir}", file=sys.stderr)
            sys.exit(1)

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "init": cmd_init,
        "run": cmd_run,
        "resume": cmd_resume,
        "status": cmd_status,
        "learn": cmd_learn,
        "components": cmd_components,
        "health": cmd_health,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)
