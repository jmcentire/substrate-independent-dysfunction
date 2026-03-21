"""Scoring functions — composable metrics for pipeline stages.

Core types:
  Score          — single named metric with value in [0,1] and weight
  CompositeScore — weighted average of scores
  ScoreRegistry  — maps stage names to scoring functions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Score:
    """A single named score in [0, 1]."""
    name: str
    value: float
    weight: float = 1.0
    detail: str = ""


class CompositeScore:
    """Weighted average of individual scores."""

    def __init__(self, scores: list[Score]) -> None:
        self.scores = list(scores)

    @property
    def value(self) -> float:
        if not self.scores:
            return 0.0
        total_weight = sum(s.weight for s in self.scores)
        if total_weight == 0:
            return 0.0
        return sum(s.value * s.weight for s in self.scores) / total_weight

    def __repr__(self) -> str:
        return f"CompositeScore({self.value:.3f}, n={len(self.scores)})"


# ── Score function type ─────────────────────────────────────────────

ScoreFunction = Callable[[object], Score]


# ── Built-in score functions ────────────────────────────────────────


def confidence_score(result: object) -> Score:
    """Extract .confidence from any result."""
    conf = getattr(result, "confidence", 0.0)
    return Score(name="confidence", value=float(conf), weight=2.0)


def diff_size_score(result: object) -> Score:
    """Penalize large diffs — 1.0 for small, decays toward 0."""
    total = getattr(result, "total_diff_lines", 0)
    if total <= 0:
        return Score(name="diff_size", value=1.0, weight=1.0)
    # Sigmoid-ish decay: 50 lines = 1.0, 500 lines = ~0.1
    value = max(0.0, min(1.0, 1.0 - (total / 500.0)))
    return Score(name="diff_size", value=value, weight=1.0, detail=f"{total} lines")


def coverage_score(result: object) -> Score:
    """tests_passing / tests_total."""
    passing = getattr(result, "tests_passing", 0)
    total = getattr(result, "tests_total", 0)
    if total == 0:
        return Score(name="test_coverage", value=0.0, weight=1.5)
    return Score(name="test_coverage", value=passing / total, weight=1.5)


def risk_count_score(result: object) -> Score:
    """Penalize high risk counts."""
    risks = getattr(result, "risks", [])
    count = len(risks) if isinstance(risks, list) else 0
    value = max(0.0, 1.0 - (count * 0.2))
    return Score(name="risk_count", value=value, weight=1.0, detail=f"{count} risks")


def coherence_score(result: object) -> Score:
    """From IntegrateResult — binary coherent/incoherent weighted by confidence."""
    coherent = getattr(result, "coherent", False)
    conf = getattr(result, "confidence", 0.0)
    value = float(conf) if coherent else 0.0
    return Score(name="coherence", value=value, weight=2.0)


def guardian_score(result: object) -> Score:
    """Binary pass/fail from guardian."""
    passed = getattr(result, "passed", False)
    return Score(name="guardian", value=1.0 if passed else 0.0, weight=3.0)


def issue_count_score(result: object) -> Score:
    """Penalize review issues."""
    issues = getattr(result, "issues", [])
    count = len(issues) if isinstance(issues, list) else 0
    value = max(0.0, 1.0 - (count * 0.15))
    return Score(name="issue_count", value=value, weight=1.0, detail=f"{count} issues")


# ── ScoreRegistry ───────────────────────────────────────────────────


class ScoreRegistry:
    """Maps stage names to lists of (ScoreFunction, weight)."""

    def __init__(self) -> None:
        self._registry: dict[str, list[tuple[ScoreFunction, float]]] = {}

    def register(self, stage: str, fn: ScoreFunction, weight: float = 1.0) -> None:
        self._registry.setdefault(stage, []).append((fn, weight))

    def score(self, stage: str, result: object) -> CompositeScore:
        fns = self._registry.get(stage, [])
        scores = []
        for fn, weight in fns:
            s = fn(result)
            scores.append(Score(name=s.name, value=s.value, weight=s.weight * weight, detail=s.detail))
        return CompositeScore(scores)

    def has_scores_for(self, stage: str) -> bool:
        return stage in self._registry and len(self._registry[stage]) > 0

    @property
    def registered_stages(self) -> list[str]:
        return list(self._registry.keys())


def default_registry() -> ScoreRegistry:
    """Standard scoring for all 9 stages."""
    reg = ScoreRegistry()

    # Diagnose
    reg.register("diagnose", confidence_score)
    reg.register("diagnose", risk_count_score)

    # Decompose
    reg.register("decompose", confidence_score)

    # Architect
    reg.register("architect", confidence_score)

    # Locate
    reg.register("locate", confidence_score)

    # Execute
    reg.register("execute", confidence_score)
    reg.register("execute", diff_size_score)

    # Test
    reg.register("test", confidence_score)

    # Verify
    reg.register("verify", coverage_score)

    # Review
    reg.register("review", confidence_score)
    reg.register("review", issue_count_score)
    reg.register("review", risk_count_score)

    # Integrate
    reg.register("integrate", coherence_score)
    reg.register("integrate", confidence_score)

    # Submit (no scoring — mechanical)
    reg.register("submit", guardian_score)

    return reg
