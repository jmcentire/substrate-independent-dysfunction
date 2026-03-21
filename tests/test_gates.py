"""Tests for stage gates — deterministic pass/fail logic."""

from __future__ import annotations

import pytest

from swarm.config import SwarmConfig
from swarm.gates import (
    diagnose_gate,
    integrate_gate,
    locate_gate,
    review_gate,
    verify_gate,
)
from swarm.schemas import (
    DiagnoseResult,
    FileLocation,
    IntegrateResult,
    LocateResult,
    ReviewVerdict,
    VerifyResult,
)


def _config(**kwargs) -> SwarmConfig:
    return SwarmConfig(**kwargs)


class TestDiagnoseGate:
    def test_pass(self):
        r = DiagnoseResult(
            task_type="feature", complexity="simple", confidence=0.8,
            language="python", summary="x", reasoning="x",
        )
        gate = diagnose_gate(r, _config())
        assert gate.passed is True

    def test_epic_passes_for_decomposition(self):
        r = DiagnoseResult(
            task_type="feature", complexity="epic", confidence=0.9,
            language="python", summary="x", reasoning="x",
        )
        gate = diagnose_gate(r, _config())
        assert gate.passed is True
        assert "decomposition" in gate.reason.lower()

    def test_fail_low_confidence(self):
        r = DiagnoseResult(
            task_type="feature", complexity="simple", confidence=0.3,
            language="python", summary="x", reasoning="x",
        )
        gate = diagnose_gate(r, _config())
        assert gate.passed is False
        assert "Confidence" in gate.reason

    def test_custom_threshold(self):
        r = DiagnoseResult(
            task_type="feature", complexity="simple", confidence=0.5,
            language="python", summary="x", reasoning="x",
        )
        gate = diagnose_gate(r, _config(diagnose_min_confidence=0.4))
        assert gate.passed is True


class TestLocateGate:
    def test_pass(self):
        r = LocateResult(
            files=[FileLocation(path="a.py", relevance="primary", reasoning="x")],
            confidence=0.8, search_strategy="grep",
        )
        gate = locate_gate(r, _config())
        assert gate.passed is True

    def test_fail_no_files(self):
        r = LocateResult(files=[], confidence=0.8, search_strategy="grep")
        gate = locate_gate(r, _config())
        assert gate.passed is False

    def test_fail_low_confidence(self):
        r = LocateResult(
            files=[FileLocation(path="a.py", relevance="primary", reasoning="x")],
            confidence=0.3, search_strategy="grep",
        )
        gate = locate_gate(r, _config())
        assert gate.passed is False

    def test_fail_too_many_files(self):
        files = [FileLocation(path=f"f{i}.py", relevance="primary", reasoning="x") for i in range(35)]
        r = LocateResult(files=files, confidence=0.8, search_strategy="grep")
        gate = locate_gate(r, _config())
        assert gate.passed is False
        assert "Too many" in gate.reason


class TestVerifyGate:
    def test_pass_all_green(self):
        r = VerifyResult(compiles=True, lint_clean=True, tests_passing=5, tests_total=5)
        gate = verify_gate(r)
        assert gate.passed is True

    def test_fail_compile(self):
        r = VerifyResult(compiles=False, lint_clean=True, tests_passing=5, tests_total=5)
        gate = verify_gate(r)
        assert gate.passed is False
        assert "Compilation" in gate.reason

    def test_fail_tests(self):
        r = VerifyResult(compiles=True, lint_clean=True, tests_passing=3, tests_total=5)
        gate = verify_gate(r)
        assert gate.passed is False
        assert "Tests" in gate.reason

    def test_pass_no_tests(self):
        r = VerifyResult(compiles=True, lint_clean=True, tests_passing=0, tests_total=0)
        gate = verify_gate(r)
        assert gate.passed is True


class TestReviewGate:
    def test_pass_approve(self):
        v = ReviewVerdict(
            verdict="approve", confidence=0.9, issues=[], reasoning="Clean",
        )
        gate = review_gate(v, _config())
        assert gate.passed is True

    def test_fail_reject(self):
        v = ReviewVerdict(
            verdict="reject", confidence=0.9, issues=["Bug found"], reasoning="Bad",
        )
        gate = review_gate(v, _config())
        assert gate.passed is False
        assert "rejected" in gate.reason

    def test_fail_low_confidence(self):
        v = ReviewVerdict(
            verdict="approve", confidence=0.5, issues=[], reasoning="Maybe ok",
        )
        gate = review_gate(v, _config())
        assert gate.passed is False
        assert "confidence" in gate.reason

    def test_pause_on_escalation(self):
        v = ReviewVerdict(
            verdict="approve", confidence=0.9, issues=[], reasoning="OK",
            escalation="Need human check",
        )
        gate = review_gate(v, _config())
        assert gate.passed is True
        assert gate.paused is True


class TestIntegrateGate:
    def test_pass_coherent(self):
        r = IntegrateResult(coherent=True, confidence=0.9, reasoning="All good")
        gate = integrate_gate(r, _config())
        assert gate.passed is True

    def test_fail_incoherent(self):
        r = IntegrateResult(
            coherent=False, issues=["Type mismatch"], confidence=0.9, reasoning="Bad",
        )
        gate = integrate_gate(r, _config())
        assert gate.passed is False
        assert "Type mismatch" in gate.reason

    def test_fail_low_confidence(self):
        r = IntegrateResult(coherent=True, confidence=0.5, reasoning="Unsure")
        gate = integrate_gate(r, _config())
        assert gate.passed is False
