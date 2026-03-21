"""Tests for perturbation.py — temperature, annealing, phase modifiers."""

from __future__ import annotations

import pytest

from swarm.perturbation import (
    PHASE_TEMPERATURE,
    PerturbationEngine,
    PerturbationState,
    Temperature,
)


class TestTemperature:
    def test_creation(self):
        t = Temperature(value=0.5)
        assert t.value == 0.5

    def test_clamp_above_ceiling(self):
        t = Temperature(value=1.5, ceiling=1.0)
        t.clamp()
        assert t.value == 1.0

    def test_clamp_below_floor(self):
        t = Temperature(value=0.01, floor=0.1)
        t.clamp()
        assert t.value == 0.1

    def test_clamp_within_bounds(self):
        t = Temperature(value=0.5, floor=0.1, ceiling=1.0)
        t.clamp()
        assert t.value == 0.5


class TestPerturbationEngine:
    def test_initial_temperature(self):
        engine = PerturbationEngine(initial_temp=0.5)
        assert engine.temperature == 0.5

    def test_anneal_success_cools(self):
        engine = PerturbationEngine(initial_temp=0.5, cool_rate=0.9)
        engine.anneal_success()
        assert engine.temperature == pytest.approx(0.45)

    def test_anneal_failure_heats(self):
        engine = PerturbationEngine(initial_temp=0.5, heat_rate=1.3)
        engine.anneal_failure()
        assert engine.temperature == pytest.approx(0.65)

    def test_consecutive_successes_tracked(self):
        engine = PerturbationEngine()
        engine.anneal_success()
        engine.anneal_success()
        assert engine.state.consecutive_successes == 2
        assert engine.state.consecutive_failures == 0

    def test_consecutive_failures_tracked(self):
        engine = PerturbationEngine()
        engine.anneal_failure()
        engine.anneal_failure()
        assert engine.state.consecutive_failures == 2
        assert engine.state.consecutive_successes == 0

    def test_success_resets_failure_count(self):
        engine = PerturbationEngine()
        engine.anneal_failure()
        engine.anneal_failure()
        engine.anneal_success()
        assert engine.state.consecutive_failures == 0
        assert engine.state.consecutive_successes == 1

    def test_failure_resets_success_count(self):
        engine = PerturbationEngine()
        engine.anneal_success()
        engine.anneal_success()
        engine.anneal_failure()
        assert engine.state.consecutive_successes == 0
        assert engine.state.consecutive_failures == 1

    def test_floor_enforced(self):
        engine = PerturbationEngine(initial_temp=0.15, cool_rate=0.1, floor=0.1)
        engine.anneal_success()
        assert engine.temperature >= 0.1

    def test_ceiling_enforced(self):
        engine = PerturbationEngine(initial_temp=0.9, heat_rate=2.0, ceiling=1.0)
        engine.anneal_failure()
        assert engine.temperature <= 1.0

    def test_stage_temperature(self):
        engine = PerturbationEngine(initial_temp=1.0)
        # architect has modifier 0.7
        t = engine.stage_temperature("architect")
        assert t == pytest.approx(0.7)

    def test_stage_temperature_unknown_stage(self):
        engine = PerturbationEngine(initial_temp=1.0)
        t = engine.stage_temperature("unknown")
        assert t == pytest.approx(0.5)  # Default modifier

    def test_recommended_competitors_no_competition(self):
        engine = PerturbationEngine()
        assert engine.recommended_competitors("execute", 0) == 0
        assert engine.recommended_competitors("execute", 1) == 1

    def test_recommended_competitors_high_temp(self):
        engine = PerturbationEngine(initial_temp=1.0)
        # execute modifier = 0.4, so stage_temp = 0.4 * 1.0 = 0.4, not >= 0.8
        # Need very high temp for stage_temp >= 0.8
        engine._state.temperature.value = 2.0  # stage_temp = 0.8
        count = engine.recommended_competitors("execute", 2)
        assert count >= 2

    def test_recommended_competitors_low_temp(self):
        engine = PerturbationEngine(initial_temp=0.1)
        # Very low temp → may reduce competitors
        count = engine.recommended_competitors("execute", 3)
        assert count >= 2  # Never below 2

    def test_should_perturb_high_temp(self):
        engine = PerturbationEngine(initial_temp=1.0)
        assert engine.should_perturb("architect")  # 1.0 * 0.7 = 0.7 > 0.5

    def test_should_perturb_low_temp(self):
        engine = PerturbationEngine(initial_temp=0.1)
        assert not engine.should_perturb("submit")  # 0.1 * 0.1 = 0.01 < 0.5

    def test_multiple_anneal_cycles(self):
        engine = PerturbationEngine(initial_temp=0.5, cool_rate=0.9, heat_rate=1.3)
        for _ in range(5):
            engine.anneal_success()
        temp_after_cool = engine.temperature
        for _ in range(5):
            engine.anneal_failure()
        temp_after_heat = engine.temperature
        assert temp_after_heat > temp_after_cool

    def test_phase_temperature_dict_completeness(self):
        expected = {"diagnose", "architect", "locate", "execute",
                    "test", "verify", "review", "integrate", "submit"}
        assert set(PHASE_TEMPERATURE.keys()) == expected
