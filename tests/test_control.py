"""Tests for control.py — StateVector, StabilityMonitor, V(x)/dV/dt."""

from __future__ import annotations

import time

import pytest

from swarm.control import (
    WEIGHTS,
    StabilityMonitor,
    StabilityReading,
    StateVector,
    build_state_vector,
)


class TestStateVector:
    def test_defaults(self):
        sv = StateVector()
        assert sv.success_rate == 0.0
        assert sv.pause_rate == 0.0
        assert sv.timestamp > 0

    def test_custom_values(self):
        sv = StateVector(success_rate=0.8, pause_rate=0.1)
        assert sv.success_rate == 0.8
        assert sv.pause_rate == 0.1


class TestStabilityMonitor:
    def test_compute_v_zero_state(self):
        mon = StabilityMonitor()
        sv = StateVector()
        v = mon.compute_v(sv)
        assert v == 0.0

    def test_compute_v_all_good(self):
        mon = StabilityMonitor()
        sv = StateVector(success_rate=1.0, budget_efficiency=1.0)
        v = mon.compute_v(sv)
        # success_rate * -2.0 + budget_efficiency * -1.0 = -3.0
        assert v < 0

    def test_compute_v_all_bad(self):
        mon = StabilityMonitor()
        sv = StateVector(pause_rate=1.0, contradiction_rate=1.0,
                         signal_rate=1.0, review_reject_rate=1.0)
        v = mon.compute_v(sv)
        assert v > 0

    def test_dv_dt_no_history(self):
        mon = StabilityMonitor()
        assert mon.compute_dv_dt() == 0.0

    def test_dv_dt_single_point(self):
        mon = StabilityMonitor()
        mon._history = [(1.0, 0.5)]
        assert mon.compute_dv_dt() == 0.0

    def test_dv_dt_increasing(self):
        mon = StabilityMonitor()
        mon._history = [(1.0, 0.0), (2.0, 1.0), (3.0, 2.0)]
        slope = mon.compute_dv_dt()
        assert slope > 0

    def test_dv_dt_decreasing(self):
        mon = StabilityMonitor()
        mon._history = [(1.0, 2.0), (2.0, 1.0), (3.0, 0.0)]
        slope = mon.compute_dv_dt()
        assert slope < 0

    def test_dv_dt_flat(self):
        mon = StabilityMonitor()
        mon._history = [(1.0, 1.0), (2.0, 1.0), (3.0, 1.0)]
        slope = mon.compute_dv_dt()
        assert abs(slope) < 0.01

    def test_detect_oscillation_no_history(self):
        mon = StabilityMonitor()
        assert not mon.detect_oscillation()

    def test_detect_oscillation_true(self):
        mon = StabilityMonitor()
        # Values: 0, 1, 0, 1, 0, 1 → deltas: +1, -1, +1, -1, +1 → 4 sign flips
        mon._history = [
            (1, 0), (2, 1), (3, 0), (4, 1), (5, 0), (6, 1),
        ]
        assert mon.detect_oscillation()

    def test_detect_oscillation_false(self):
        mon = StabilityMonitor()
        mon._history = [(1, 0), (2, 1), (3, 2), (4, 3)]
        assert not mon.detect_oscillation()

    def test_record_returns_reading(self):
        mon = StabilityMonitor()
        sv = StateVector(success_rate=0.8)
        reading = mon.record(sv)
        assert isinstance(reading, StabilityReading)
        assert reading.state in ("stable", "degrading", "improving", "oscillating")

    def test_record_stable(self):
        mon = StabilityMonitor()
        sv = StateVector()
        reading = mon.record(sv)
        assert reading.state == "stable"

    def test_record_degrading(self):
        mon = StabilityMonitor()
        # Build history with increasing V(x)
        for i in range(5):
            sv = StateVector(
                timestamp=float(i),
                pause_rate=i * 0.2,
            )
            reading = mon.record(sv)
        # Last reading should show degrading if slope > 0.1
        assert reading.dv_dt >= 0

    def test_window_trimming(self):
        mon = StabilityMonitor(window_size=5)
        for i in range(10):
            sv = StateVector(timestamp=float(i))
            mon.record(sv)
        assert len(mon.history) <= 5

    def test_history_property(self):
        mon = StabilityMonitor()
        sv = StateVector(timestamp=1.0)
        mon.record(sv)
        h = mon.history
        assert len(h) == 1
        # Property returns copy
        h.clear()
        assert len(mon.history) == 1


class TestBuildStateVector:
    def test_empty_runs(self):
        sv = build_state_vector([])
        assert sv.success_rate == 0.0

    def test_all_completed(self):
        class FakeRun:
            status = "completed"
            signals = []
        sv = build_state_vector([FakeRun(), FakeRun()])
        assert sv.success_rate == 1.0
        assert sv.pause_rate == 0.0

    def test_mixed_statuses(self):
        runs = []
        for status in ["completed", "failed", "paused", "completed"]:
            r = type("R", (), {"status": status, "signals": []})()
            runs.append(r)
        sv = build_state_vector(runs)
        assert sv.success_rate == 0.5
        assert sv.pause_rate == 0.25

    def test_budget_exceeded(self):
        class R:
            status = "budget_exceeded"
            signals = []
        sv = build_state_vector([R()])
        assert sv.budget_efficiency == 0.0

    def test_with_signals(self):
        class R:
            status = "completed"
            signals = ["a", "b"]
        sv = build_state_vector([R(), R()])
        assert sv.signal_rate == 2.0

    def test_with_learnings_store(self):
        class FakeEntry:
            times_contradicted = 1
        class FakeStore:
            def _load_all(self):
                return [FakeEntry(), FakeEntry()]
        class R:
            status = "completed"
            signals = []
        sv = build_state_vector([R()], FakeStore())
        assert sv.learning_growth == 2.0
        assert sv.contradiction_rate == 1.0
