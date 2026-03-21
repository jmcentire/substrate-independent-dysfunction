"""Perturbation/annealing engine — temperature schedules for exploration.

Temperature controls exploration vs exploitation:
  High temp → more exploration (more competitors, varied approaches)
  Low temp  → exploitation (deterministic, best-known approach)

Anneal on success (cool), heat on failure.
"""

from __future__ import annotations

from dataclasses import dataclass


# Phase-specific temperature modifiers
PHASE_TEMPERATURE: dict[str, float] = {
    "diagnose": 0.3,
    "architect": 0.7,
    "locate": 0.3,
    "execute": 0.4,
    "test": 0.3,
    "verify": 0.1,
    "review": 0.2,
    "integrate": 0.3,
    "submit": 0.1,
}


@dataclass
class Temperature:
    """Clamped temperature value."""
    value: float
    floor: float = 0.1
    ceiling: float = 1.0

    def clamp(self) -> None:
        self.value = max(self.floor, min(self.ceiling, self.value))


@dataclass
class PerturbationState:
    """Tracks temperature and consecutive success/failure streaks."""
    temperature: Temperature
    consecutive_successes: int = 0
    consecutive_failures: int = 0


class PerturbationEngine:
    """Temperature-driven perturbation with annealing."""

    def __init__(
        self,
        initial_temp: float = 0.5,
        cool_rate: float = 0.9,
        heat_rate: float = 1.3,
        floor: float = 0.1,
        ceiling: float = 1.0,
    ) -> None:
        self._state = PerturbationState(
            temperature=Temperature(value=initial_temp, floor=floor, ceiling=ceiling),
        )
        self._cool_rate = cool_rate
        self._heat_rate = heat_rate

    @property
    def state(self) -> PerturbationState:
        return self._state

    @property
    def temperature(self) -> float:
        return self._state.temperature.value

    def anneal_success(self) -> None:
        """Cool on success — reduce exploration."""
        self._state.temperature.value *= self._cool_rate
        self._state.temperature.clamp()
        self._state.consecutive_successes += 1
        self._state.consecutive_failures = 0

    def anneal_failure(self) -> None:
        """Heat on failure — increase exploration."""
        self._state.temperature.value *= self._heat_rate
        self._state.temperature.clamp()
        self._state.consecutive_failures += 1
        self._state.consecutive_successes = 0

    def stage_temperature(self, stage: str) -> float:
        """Get temperature for a specific stage (base * phase modifier)."""
        modifier = PHASE_TEMPERATURE.get(stage, 0.5)
        return self._state.temperature.value * modifier

    def recommended_competitors(self, stage: str, base_competitors: int) -> int:
        """Temperature-adjusted competitor count.

        Higher temperature → more competitors.
        At temp >= 0.8, add 1 competitor.
        """
        if base_competitors < 2:
            return base_competitors
        temp = self.stage_temperature(stage)
        if temp >= 0.8:
            return base_competitors + 1
        if temp <= 0.2:
            return max(2, base_competitors - 1)
        return base_competitors

    def should_perturb(self, stage: str) -> bool:
        """Whether temperature is high enough to warrant perturbation."""
        return self.stage_temperature(stage) > 0.5
