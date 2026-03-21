"""Tests for well_formedness.py — completeness, liveness, dissipation."""

from __future__ import annotations

import pytest

from swarm.scoring import ScoreRegistry, confidence_score, default_registry
from swarm.well_formedness import WellFormednessChecker, WellFormednessReport


def _make_formation(stage_names):
    """Create a minimal formation-like object."""
    class Stage:
        def __init__(self, name):
            self.name = name
    class Formation:
        def __init__(self, stages, max_review_loops=2):
            self.stages = [Stage(n) for n in stages]
            self.max_review_loops = max_review_loops
    return Formation(stage_names)


def _make_budget(spend=0.0, cap=5.0):
    class Budget:
        project_spend = spend
        per_project_cap = cap
    return Budget()


class TestWellFormednessReport:
    def test_all_good(self):
        r = WellFormednessReport()
        assert r.well_formed

    def test_incomplete(self):
        r = WellFormednessReport(complete=False, violations=["missing scoring"])
        assert not r.well_formed

    def test_not_live(self):
        r = WellFormednessReport(live=False)
        assert not r.well_formed

    def test_not_dissipating(self):
        r = WellFormednessReport(dissipating=False)
        assert not r.well_formed


class TestCompleteness:
    def test_default_registry_is_complete(self):
        reg = default_registry()
        checker = WellFormednessChecker(reg)
        formation = _make_formation(["diagnose", "architect", "execute", "verify", "submit"])
        ok, violations = checker.check_completeness(formation)
        assert ok
        assert violations == []

    def test_missing_stage_scoring(self):
        reg = ScoreRegistry()
        reg.register("diagnose", confidence_score)
        checker = WellFormednessChecker(reg)
        formation = _make_formation(["diagnose", "architect"])
        ok, violations = checker.check_completeness(formation)
        assert not ok
        assert len(violations) == 1
        assert "architect" in violations[0]

    def test_mechanical_stages_skip_check(self):
        reg = ScoreRegistry()
        checker = WellFormednessChecker(reg)
        formation = _make_formation(["verify", "submit"])
        ok, violations = checker.check_completeness(formation)
        assert ok


class TestLiveness:
    def test_within_bounds(self):
        reg = default_registry()
        checker = WellFormednessChecker(reg, max_pipeline_duration=3600)
        formation = _make_formation(["diagnose"])
        ok, violations = checker.check_liveness(formation, elapsed=100)
        assert ok

    def test_duration_exceeded(self):
        reg = default_registry()
        checker = WellFormednessChecker(reg, max_pipeline_duration=100)
        formation = _make_formation(["diagnose"])
        ok, violations = checker.check_liveness(formation, elapsed=200)
        assert not ok
        assert "duration" in violations[0].lower()

    def test_review_loops_exceeded(self):
        reg = default_registry()
        checker = WellFormednessChecker(reg, max_review_loops=3)
        formation = _make_formation(["diagnose"])
        ok, violations = checker.check_liveness(formation, review_loops=5)
        assert not ok

    def test_formation_max_loops_too_high(self):
        reg = default_registry()
        checker = WellFormednessChecker(reg, max_review_loops=3)
        formation = _make_formation(["diagnose"])
        formation.max_review_loops = 10
        ok, violations = checker.check_liveness(formation)
        assert not ok


class TestDissipation:
    def test_under_budget(self):
        reg = default_registry()
        checker = WellFormednessChecker(reg)
        budget = _make_budget(spend=1.0, cap=5.0)
        ok, violations = checker.check_dissipation(budget)
        assert ok

    def test_over_budget(self):
        reg = default_registry()
        checker = WellFormednessChecker(reg)
        budget = _make_budget(spend=6.0, cap=5.0)
        ok, violations = checker.check_dissipation(budget)
        assert not ok
        assert "budget" in violations[0].lower()


class TestCheckAll:
    def test_all_pass(self):
        reg = default_registry()
        checker = WellFormednessChecker(reg)
        formation = _make_formation(["diagnose", "verify", "submit"])
        budget = _make_budget()
        report = checker.check_all(formation, budget, elapsed=10)
        assert report.well_formed

    def test_multiple_failures(self):
        reg = ScoreRegistry()
        checker = WellFormednessChecker(reg, max_pipeline_duration=10)
        formation = _make_formation(["diagnose", "architect"])
        budget = _make_budget(spend=10.0, cap=5.0)
        report = checker.check_all(formation, budget, elapsed=100)
        assert not report.well_formed
        assert len(report.violations) >= 2
