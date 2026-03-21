"""Well-formedness checker — Completeness, Liveness, Dissipation.

Three properties that must hold for the pipeline to be well-formed:
  Completeness  — every non-mechanical stage has registered scoring
  Liveness      — pipeline terminates within bounded time/loops
  Dissipation   — budget spend stays below cap
"""

from __future__ import annotations

from dataclasses import dataclass, field

from swarm.scoring import ScoreRegistry


# Mechanical stages that don't need scoring
MECHANICAL_STAGES = frozenset(["verify", "submit"])


@dataclass
class WellFormednessReport:
    """Result of well-formedness checks."""
    complete: bool = True
    live: bool = True
    dissipating: bool = True
    violations: list[str] = field(default_factory=list)

    @property
    def well_formed(self) -> bool:
        return self.complete and self.live and self.dissipating


class WellFormednessChecker:
    """Checks three properties: completeness, liveness, dissipation."""

    def __init__(
        self,
        registry: ScoreRegistry,
        max_pipeline_duration: float = 3600.0,  # 1 hour
        max_gate_timeout: int = 120,             # 2 hours
        max_review_loops: int = 5,               # absolute upper bound
    ) -> None:
        self._registry = registry
        self._max_duration = max_pipeline_duration
        self._max_gate_timeout = max_gate_timeout
        self._max_review_loops = max_review_loops

    def check_completeness(self, formation: object) -> tuple[bool, list[str]]:
        """Every non-mechanical stage must have registered scoring."""
        violations = []
        stages = getattr(formation, "stages", [])
        for stage in stages:
            name = getattr(stage, "name", "")
            if name in MECHANICAL_STAGES:
                continue
            if not self._registry.has_scores_for(name):
                violations.append(f"Stage '{name}' has no registered scoring functions")
        return len(violations) == 0, violations

    def check_liveness(
        self,
        formation: object,
        elapsed: float = 0.0,
        review_loops: int = 0,
    ) -> tuple[bool, list[str]]:
        """Pipeline must terminate within bounded time and loop counts."""
        violations = []
        if elapsed > self._max_duration:
            violations.append(
                f"Pipeline duration {elapsed:.0f}s exceeds max {self._max_duration:.0f}s"
            )
        max_loops = getattr(formation, "max_review_loops", 0)
        if max_loops > self._max_review_loops:
            violations.append(
                f"Formation max_review_loops {max_loops} exceeds absolute bound {self._max_review_loops}"
            )
        if review_loops > self._max_review_loops:
            violations.append(
                f"Current review loops {review_loops} exceeds absolute bound {self._max_review_loops}"
            )
        return len(violations) == 0, violations

    def check_dissipation(self, budget: object) -> tuple[bool, list[str]]:
        """Budget spend must be below cap."""
        violations = []
        spend = getattr(budget, "project_spend", 0.0)
        cap = getattr(budget, "per_project_cap", 0.0)
        if cap > 0 and spend > cap:
            violations.append(f"Budget spend ${spend:.4f} exceeds cap ${cap:.2f}")
        return len(violations) == 0, violations

    def check_all(
        self,
        formation: object,
        budget: object,
        elapsed: float = 0.0,
        review_loops: int = 0,
    ) -> WellFormednessReport:
        """Run all three checks."""
        report = WellFormednessReport()

        complete, c_violations = self.check_completeness(formation)
        report.complete = complete
        report.violations.extend(c_violations)

        live, l_violations = self.check_liveness(formation, elapsed, review_loops)
        report.live = live
        report.violations.extend(l_violations)

        dissipating, d_violations = self.check_dissipation(budget)
        report.dissipating = dissipating
        report.violations.extend(d_violations)

        return report
