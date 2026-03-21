"""Mechanical arbiter — selects winner from competing candidates.

Used in competition mode (FormationStage.competitors >= 2).
Scoring-based, deterministic, no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from swarm.scoring import CompositeScore, ScoreRegistry


@dataclass
class Candidate:
    """A single competition candidate with accumulated results."""
    index: int
    execute_result: object = None
    test_result: object = None
    verify_result: object = None
    guardian_result: object = None
    review_verdict: object = None
    composite_score: CompositeScore | None = None
    disqualified: bool = False
    disqualify_reason: str = ""


class Arbiter:
    """Selects the best candidate from a competition pool."""

    def __init__(self, registry: ScoreRegistry) -> None:
        self._registry = registry

    def disqualify(self, candidate: Candidate, reason: str) -> None:
        """Mark a candidate as disqualified."""
        candidate.disqualified = True
        candidate.disqualify_reason = reason

    def select(self, candidates: list[Candidate]) -> Candidate | None:
        """Score each viable candidate and return the highest-scoring one.

        Returns None if all candidates are disqualified.
        """
        viable = [c for c in candidates if not c.disqualified]
        if not viable:
            return None

        for c in viable:
            if c.execute_result is not None and c.composite_score is None:
                c.composite_score = self._registry.score("execute", c.execute_result)

        # Sort by composite score value, descending. Tie-break by index (lower = earlier).
        viable.sort(key=lambda c: (-(c.composite_score.value if c.composite_score else 0.0), c.index))
        return viable[0]
