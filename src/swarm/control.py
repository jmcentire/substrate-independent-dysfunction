"""V(x)/dV/dt stability monitoring — control-theory foundation.

Components:
  StateVector       — snapshot of system health metrics
  StabilityReading  — V(x), dV/dt, oscillation detection, recommendation
  StabilityMonitor  — windowed history with linear regression
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


# Weights for V(x) computation — positive = bad, negative = good
WEIGHTS: dict[str, float] = {
    "success_rate": -2.0,       # Higher success → lower V(x)
    "learning_growth": -0.5,    # More learnings → slightly lower V(x)
    "pause_rate": 1.5,          # More pauses → higher V(x)
    "budget_efficiency": -1.0,  # Better efficiency → lower V(x)
    "contradiction_rate": 1.0,  # More contradictions → higher V(x)
    "signal_rate": 0.8,         # More signals → higher V(x)
    "review_reject_rate": 1.2,  # More rejections → higher V(x)
    "avg_review_loops": 0.5,    # More loops → higher V(x)
}


@dataclass
class StateVector:
    """Snapshot of system health metrics at a point in time."""
    timestamp: float = field(default_factory=time.time)
    success_rate: float = 0.0
    learning_growth: float = 0.0
    pause_rate: float = 0.0
    budget_efficiency: float = 0.0
    contradiction_rate: float = 0.0
    signal_rate: float = 0.0
    review_reject_rate: float = 0.0
    avg_review_loops: float = 0.0


@dataclass
class StabilityReading:
    """Result of stability analysis."""
    v_x: float                              # Current Lyapunov value
    dv_dt: float                            # Rate of change
    oscillating: bool = False               # Sign-flip detection
    state: str = "stable"                   # "stable", "degrading", "improving", "oscillating"
    recommendation: str = ""                # Human-readable recommendation


class StabilityMonitor:
    """Windowed stability monitor with V(x) and dV/dt computation."""

    def __init__(self, window_size: int = 20) -> None:
        self._window_size = window_size
        self._history: list[tuple[float, float]] = []  # (timestamp, v_x)

    @property
    def history(self) -> list[tuple[float, float]]:
        return list(self._history)

    def compute_v(self, state: StateVector) -> float:
        """Compute Lyapunov function V(x) from state vector."""
        v = 0.0
        for metric, weight in WEIGHTS.items():
            val = getattr(state, metric, 0.0)
            v += weight * val
        return v

    def compute_dv_dt(self) -> float:
        """Linear regression slope over recent history."""
        if len(self._history) < 2:
            return 0.0

        n = len(self._history)
        times = [h[0] for h in self._history]
        values = [h[1] for h in self._history]

        t_mean = sum(times) / n
        v_mean = sum(values) / n

        numerator = sum((t - t_mean) * (v - v_mean) for t, v in zip(times, values))
        denominator = sum((t - t_mean) ** 2 for t in times)

        if abs(denominator) < 1e-10:
            return 0.0

        return numerator / denominator

    def detect_oscillation(self) -> bool:
        """Detect oscillation via sign flips in consecutive dV values."""
        if len(self._history) < 4:
            return False

        deltas = []
        for i in range(1, len(self._history)):
            deltas.append(self._history[i][1] - self._history[i - 1][1])

        sign_flips = 0
        for i in range(1, len(deltas)):
            if deltas[i] * deltas[i - 1] < 0:
                sign_flips += 1

        return sign_flips >= 3

    def record(self, state: StateVector) -> StabilityReading:
        """Record a state vector and return a stability reading."""
        v_x = self.compute_v(state)
        self._history.append((state.timestamp, v_x))

        # Trim to window
        if len(self._history) > self._window_size:
            self._history = self._history[-self._window_size:]

        dv_dt = self.compute_dv_dt()
        oscillating = self.detect_oscillation()

        # Determine state
        if oscillating:
            s = "oscillating"
            rec = "System oscillating — consider reducing perturbation temperature"
        elif dv_dt > 0.1:
            s = "degrading"
            rec = "System degrading — consider pausing or simplifying formation"
        elif dv_dt < -0.1:
            s = "improving"
            rec = "System improving — continue current approach"
        else:
            s = "stable"
            rec = "System stable"

        return StabilityReading(
            v_x=v_x, dv_dt=dv_dt, oscillating=oscillating,
            state=s, recommendation=rec,
        )


def build_state_vector(
    runs: list[object],
    learnings_store: object | None = None,
) -> StateVector:
    """Construct a StateVector from accumulated run data."""
    if not runs:
        return StateVector()

    total = len(runs)
    completed = sum(1 for r in runs if getattr(r, "status", "") == "completed")
    paused = sum(1 for r in runs if getattr(r, "status", "") == "paused")
    budget_exceeded = sum(1 for r in runs if getattr(r, "status", "") == "budget_exceeded")

    success_rate = completed / total if total else 0.0
    pause_rate = paused / total if total else 0.0
    budget_efficiency = 1.0 - (budget_exceeded / total) if total else 1.0

    # Signal rate from runs
    total_signals = sum(len(getattr(r, "signals", [])) for r in runs)
    signal_rate = total_signals / total if total else 0.0

    # Learning metrics
    learning_growth = 0.0
    contradiction_rate = 0.0
    if learnings_store is not None:
        load_all = getattr(learnings_store, "_load_all", None)
        if load_all:
            entries = load_all()
            learning_growth = len(entries) / max(total, 1)
            if entries:
                contradicted = sum(1 for e in entries if getattr(e, "times_contradicted", 0) > 0)
                contradiction_rate = contradicted / len(entries)

    return StateVector(
        success_rate=success_rate,
        learning_growth=learning_growth,
        pause_rate=pause_rate,
        budget_efficiency=budget_efficiency,
        contradiction_rate=contradiction_rate,
        signal_rate=signal_rate,
    )
