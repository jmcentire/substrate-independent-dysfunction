"""ProjectRun state machine + JSONL audit log.

State transitions:
  active → paused          (break point, signal, gate borderline)
  active → failed          (gate fails, epic complexity)
  active → completed       (submit succeeds)
  active → budget_exceeded (dollar cap hit)
  paused → active          (resume)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from swarm.schemas import ProjectRun, StageRecord

logger = logging.getLogger(__name__)


def create_run(project_dir: str) -> ProjectRun:
    """Create a new ProjectRun."""
    return ProjectRun(
        id=uuid4().hex[:12],
        project_dir=project_dir,
        status="active",
        created_at=datetime.now(),
    )


def record_stage(
    run: ProjectRun,
    stage: str,
    passed: bool,
    confidence: float = 0.0,
    detail: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
) -> None:
    """Record a pipeline stage result on the run."""
    run.record_stage(StageRecord(
        stage=stage,
        passed=passed,
        confidence=confidence,
        detail=detail,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
    ))


def format_run_summary(run: ProjectRun) -> str:
    """Format a run as a human-readable summary."""
    stages = " → ".join(
        f"{s.stage}({'ok' if s.passed else 'FAIL'})"
        for s in run.stages
    )
    lines = [
        f"[{run.id}] {run.status:15s} ${run.total_cost_usd:.4f}",
        f"  Formation: {run.formation or 'not selected'}",
        f"  Stages: {stages or 'none'}",
        f"  Project: {run.project_dir}",
    ]
    if run.pause_reason:
        lines.append(f"  Reason: {run.pause_reason}")
    return "\n".join(lines)
