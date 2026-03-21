"""Lifecycle monitor — watches the watchers.

Detects systemic problems across projects:
  - Repeated failures at the same stage
  - Learning health (decay rate, validation rate)
  - High pause rate
  - Budget usage patterns
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

from swarm.control import StabilityMonitor, build_state_vector
from swarm.memory import LearningsStore
from swarm.schemas import ProjectRun

logger = logging.getLogger(__name__)

HIGH_CONTRADICTION_RATE = 0.5
LEARNING_COVERAGE_MIN = 2


class HealthReport:
    """Structured health report from lifecycle monitor."""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.info: list[str] = []
        self.metrics: dict[str, int | float | str] = {}

    @property
    def healthy(self) -> bool:
        return len(self.warnings) == 0

    def format(self) -> str:
        lines = ["# Swarm Health Report", ""]

        if self.healthy:
            lines.append("**Status: HEALTHY**")
        else:
            lines.append(f"**Status: {len(self.warnings)} WARNING(S)**")

        lines.append("")

        if self.warnings:
            lines.append("## Warnings")
            for w in self.warnings:
                lines.append(f"- {w}")
            lines.append("")

        if self.metrics:
            lines.append("## Metrics")
            for k, v in self.metrics.items():
                lines.append(f"- **{k}:** {v}")
            lines.append("")

        if self.info:
            lines.append("## Info")
            for i in self.info:
                lines.append(f"- {i}")
            lines.append("")

        return "\n".join(lines)


class LifecycleMonitor:
    """Monitors system health across project runs and learnings."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._learnings_store = LearningsStore(base_dir=base_dir)

    def check_health(self, runs: list[ProjectRun] | None = None) -> HealthReport:
        report = HealthReport()

        if runs is None:
            runs = []

        self._check_learning_health(report)
        self._check_run_success_rate(runs, report)
        self._check_formation_metrics(runs, report)
        self._check_stability(runs, report)

        return report

    def _check_learning_health(self, report: HealthReport) -> None:
        entries = self._learnings_store._load_all()
        if not entries:
            report.info.append("No learnings yet — system is in cold-start phase")
            report.metrics["total_learnings"] = 0
            return

        total = len(entries)
        decayed = sum(1 for e in entries if e.is_decayed)
        validated = sum(1 for e in entries if e.times_validated > 0)
        contradicted = sum(1 for e in entries if e.times_contradicted > 0)
        categories_covered = len(set(e.category for e in entries if not e.is_decayed))

        report.metrics["total_learnings"] = total
        report.metrics["validated_learnings"] = validated
        report.metrics["contradicted_learnings"] = contradicted
        report.metrics["decayed_learnings"] = decayed
        report.metrics["categories_covered"] = categories_covered

        if total > 5 and contradicted / total > HIGH_CONTRADICTION_RATE:
            report.warnings.append(
                f"High contradiction rate: {contradicted}/{total} learnings contradicted."
            )

        if total > 5 and decayed / total > 0.5:
            report.warnings.append(
                f"{decayed}/{total} learnings have decayed."
            )

        if categories_covered < LEARNING_COVERAGE_MIN:
            report.info.append(f"Only {categories_covered} learning categories covered.")

        cross_role = sum(1 for e in entries if e.target_agents)
        if cross_role > 0:
            report.info.append(f"{cross_role} cross-role learning(s) — backward feedback active")

    def _check_run_success_rate(self, runs: list[ProjectRun], report: HealthReport) -> None:
        if not runs:
            return

        total = len(runs)
        completed = sum(1 for r in runs if r.status == "completed")
        failed = sum(1 for r in runs if r.status == "failed")
        paused = sum(1 for r in runs if r.status == "paused")
        budget_exceeded = sum(1 for r in runs if r.status == "budget_exceeded")

        report.metrics["total_runs"] = total
        report.metrics["completed"] = completed
        report.metrics["failed"] = failed
        report.metrics["paused"] = paused
        report.metrics["budget_exceeded"] = budget_exceeded

        if total > 10:
            success_rate = completed / total
            report.metrics["success_rate"] = f"{success_rate:.0%}"

            if success_rate < 0.1:
                report.warnings.append(
                    f"Very low success rate: {success_rate:.0%} ({completed}/{total})."
                )

    def _check_formation_metrics(self, runs: list[ProjectRun], report: HealthReport) -> None:
        if not runs:
            return

        formation_counts: Counter[str] = Counter()
        paused = sum(1 for r in runs if r.status == "paused")

        for r in runs:
            formation = r.formation or "unknown"
            formation_counts[formation] += 1

        report.metrics["pause_count"] = paused

        known = {k: v for k, v in formation_counts.items() if k != "unknown"}
        if known:
            report.metrics["formations"] = ", ".join(f"{k}={v}" for k, v in sorted(known.items()))

        if len(runs) > 5 and paused > 0:
            pause_rate = paused / len(runs)
            report.metrics["pause_rate"] = f"{pause_rate:.0%}"
            if pause_rate > 0.3:
                report.warnings.append(
                    f"High pause rate: {pause_rate:.0%} ({paused}/{len(runs)})."
                )

    def _check_stability(self, runs: list[ProjectRun], report: HealthReport) -> None:
        """Compute V(x) and dV/dt from runs."""
        if not runs:
            return

        state_vec = build_state_vector(runs, self._learnings_store)
        monitor = StabilityMonitor(window_size=20)
        reading = monitor.record(state_vec)

        report.metrics["v_x"] = round(reading.v_x, 4)
        report.metrics["dv_dt"] = round(reading.dv_dt, 4)
        report.metrics["stability_state"] = reading.state

        if reading.state == "degrading":
            report.warnings.append(f"System degrading: V(x)={reading.v_x:.3f}, dV/dt={reading.dv_dt:.3f}")
        elif reading.state == "oscillating":
            report.warnings.append(f"System oscillating: V(x)={reading.v_x:.3f}")
