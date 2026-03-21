"""Tests for SignalBus, ContinueDecision, and gate pause utilities."""

from __future__ import annotations

import pytest

from swarm.manager import (
    ContinueDecision,
    SignalBus,
    check_break_point,
    should_pause_at_gate,
)
from swarm.schemas import FormationStage, GateResult


# ── SignalBus ────────────────────────────────────────────────────────


class TestSignalBus:
    def test_empty_bus_continues(self):
        bus = SignalBus()
        decision = bus.should_continue()
        assert decision.action == "continue"

    def test_single_consternation_continues(self):
        bus = SignalBus()
        bus.emit("diagnose", "consternation", "Something off")
        decision = bus.should_continue()
        assert decision.action == "continue"

    def test_two_consternations_pause(self):
        bus = SignalBus()
        bus.emit("diagnose", "consternation", "Something off")
        bus.emit("locate", "consternation", "Also weird")
        decision = bus.should_continue()
        assert decision.action == "pause"
        assert "systemic" in decision.reason

    def test_escalation_pauses(self):
        bus = SignalBus()
        bus.emit("review", "escalation", "Need help")
        decision = bus.should_continue()
        assert decision.action == "pause"
        assert "Escalation" in decision.reason

    def test_escalation_overrides_consternation(self):
        bus = SignalBus()
        bus.emit("diagnose", "consternation", "Hmm")
        bus.emit("review", "escalation", "Help!")
        decision = bus.should_continue()
        assert decision.action == "pause"
        assert "Escalation" in decision.reason

    def test_collect_from_result(self):
        bus = SignalBus()

        class FakeResult:
            consternation = "Something wrong"
            escalation = ""

        bus.collect_from_result("diagnose", FakeResult())
        assert len(bus.signals) == 1
        assert bus.signals[0].signal_type == "consternation"

    def test_collect_both_signals(self):
        bus = SignalBus()

        class FakeResult:
            consternation = "Hmm"
            escalation = "Help"

        bus.collect_from_result("review", FakeResult())
        assert len(bus.signals) == 2

    def test_collect_no_signals(self):
        bus = SignalBus()

        class FakeResult:
            consternation = ""
            escalation = ""

        bus.collect_from_result("diagnose", FakeResult())
        assert len(bus.signals) == 0

    def test_collect_from_object_without_fields(self):
        bus = SignalBus()
        bus.collect_from_result("verify", {"some": "dict"})
        assert len(bus.signals) == 0

    def test_clear(self):
        bus = SignalBus()
        bus.emit("diagnose", "consternation", "x")
        bus.emit("review", "escalation", "y")
        bus.clear()
        assert len(bus.signals) == 0
        decision = bus.should_continue()
        assert decision.action == "continue"

    def test_ignores_unknown_signal_type(self):
        bus = SignalBus()
        bus.emit("diagnose", "info", "Just FYI")
        assert len(bus.signals) == 0


# ── check_break_point ────────────────────────────────────────────────


class TestCheckBreakPoint:
    def test_pause(self):
        s = FormationStage(name="review", break_point="pause")
        assert check_break_point(s) is True

    def test_none(self):
        s = FormationStage(name="execute", break_point="none")
        assert check_break_point(s) is False


# ── should_pause_at_gate ─────────────────────────────────────────────


class TestShouldPauseAtGate:
    def test_pass_no_modifiers(self):
        gate = GateResult(passed=True, reason="OK")
        result = should_pause_at_gate(gate, 0.8, 0.7)
        assert result.passed is True
        assert result.paused is False

    def test_break_point_pauses(self):
        gate = GateResult(passed=True, reason="OK")
        result = should_pause_at_gate(gate, 0.8, 0.7, break_point=True)
        assert result.passed is True
        assert result.paused is True
        assert "Break point" in result.reason

    def test_consternation_pauses(self):
        gate = GateResult(passed=True, reason="OK")
        result = should_pause_at_gate(gate, 0.8, 0.7, has_consternation=True)
        assert result.passed is True
        assert result.paused is True
        assert "Consternation" in result.reason

    def test_borderline_fail_pauses(self):
        gate = GateResult(passed=False, reason="Threshold miss")
        result = should_pause_at_gate(gate, 0.65, 0.7)
        assert result.passed is False
        assert result.paused is True
        assert "Borderline" in result.reason
        assert result.timeout_minutes == 60

    def test_hard_fail_stays_failed(self):
        gate = GateResult(passed=False, reason="Way too low")
        result = should_pause_at_gate(gate, 0.3, 0.7)
        assert result.passed is False
        assert result.paused is False
