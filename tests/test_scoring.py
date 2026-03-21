"""Tests for scoring.py — Score, CompositeScore, registry, built-in functions."""

from __future__ import annotations

import pytest

from swarm.scoring import (
    CompositeScore,
    Score,
    ScoreRegistry,
    coherence_score,
    confidence_score,
    default_registry,
    diff_size_score,
    guardian_score,
    issue_count_score,
    risk_count_score,
    coverage_score,
)


# ── Score construction ──────────────────────────────────────────────


class TestScore:
    def test_score_creation(self):
        s = Score(name="test", value=0.5, weight=1.0)
        assert s.name == "test"
        assert s.value == 0.5
        assert s.weight == 1.0

    def test_score_is_frozen(self):
        s = Score(name="x", value=0.5)
        with pytest.raises(AttributeError):
            s.value = 0.9  # type: ignore

    def test_score_default_weight(self):
        s = Score(name="x", value=0.5)
        assert s.weight == 1.0


# ── CompositeScore ──────────────────────────────────────────────────


class TestCompositeScore:
    def test_single_score(self):
        cs = CompositeScore([Score(name="a", value=0.8, weight=1.0)])
        assert cs.value == pytest.approx(0.8)

    def test_weighted_average(self):
        cs = CompositeScore([
            Score(name="a", value=1.0, weight=2.0),
            Score(name="b", value=0.0, weight=2.0),
        ])
        assert cs.value == pytest.approx(0.5)

    def test_unequal_weights(self):
        cs = CompositeScore([
            Score(name="a", value=1.0, weight=3.0),
            Score(name="b", value=0.0, weight=1.0),
        ])
        assert cs.value == pytest.approx(0.75)

    def test_empty_scores(self):
        cs = CompositeScore([])
        assert cs.value == 0.0

    def test_zero_weights(self):
        cs = CompositeScore([Score(name="a", value=0.5, weight=0.0)])
        assert cs.value == 0.0

    def test_repr(self):
        cs = CompositeScore([Score(name="a", value=0.8)])
        assert "CompositeScore" in repr(cs)


# ── Built-in score functions ────────────────────────────────────────


class TestConfidenceScore:
    def test_extracts_confidence(self):
        class R:
            confidence = 0.85
        s = confidence_score(R())
        assert s.value == 0.85
        assert s.name == "confidence"

    def test_missing_confidence(self):
        s = confidence_score(object())
        assert s.value == 0.0


class TestDiffSizeScore:
    def test_small_diff(self):
        class R:
            total_diff_lines = 10
        s = diff_size_score(R())
        assert s.value > 0.9

    def test_large_diff(self):
        class R:
            total_diff_lines = 500
        s = diff_size_score(R())
        assert s.value == 0.0

    def test_zero_diff(self):
        s = diff_size_score(object())
        assert s.value == 1.0


class TestTestCoverageScore:
    def test_full_coverage(self):
        class R:
            tests_passing = 10
            tests_total = 10
        s = coverage_score(R())
        assert s.value == 1.0

    def test_partial_coverage(self):
        class R:
            tests_passing = 5
            tests_total = 10
        s = coverage_score(R())
        assert s.value == 0.5

    def test_no_tests(self):
        s = coverage_score(object())
        assert s.value == 0.0


class TestRiskCountScore:
    def test_no_risks(self):
        class R:
            risks = []
        s = risk_count_score(R())
        assert s.value == 1.0

    def test_many_risks(self):
        class R:
            risks = ["a", "b", "c", "d", "e", "f"]
        s = risk_count_score(R())
        assert s.value == 0.0  # Capped at 0

    def test_some_risks(self):
        class R:
            risks = ["a", "b"]
        s = risk_count_score(R())
        assert s.value == pytest.approx(0.6)


class TestCoherenceScore:
    def test_coherent_high_confidence(self):
        class R:
            coherent = True
            confidence = 0.9
        s = coherence_score(R())
        assert s.value == 0.9

    def test_incoherent(self):
        class R:
            coherent = False
            confidence = 0.9
        s = coherence_score(R())
        assert s.value == 0.0


class TestGuardianScore:
    def test_passed(self):
        class R:
            passed = True
        s = guardian_score(R())
        assert s.value == 1.0

    def test_failed(self):
        class R:
            passed = False
        s = guardian_score(R())
        assert s.value == 0.0


class TestIssueCountScore:
    def test_no_issues(self):
        class R:
            issues = []
        s = issue_count_score(R())
        assert s.value == 1.0

    def test_some_issues(self):
        class R:
            issues = ["a", "b"]
        s = issue_count_score(R())
        assert s.value == pytest.approx(0.7)


# ── ScoreRegistry ───────────────────────────────────────────────────


class TestScoreRegistry:
    def test_register_and_score(self):
        reg = ScoreRegistry()
        reg.register("test_stage", confidence_score)
        cs = reg.score("test_stage", type("R", (), {"confidence": 0.8})())
        assert cs.value == pytest.approx(0.8)

    def test_has_scores_for(self):
        reg = ScoreRegistry()
        assert not reg.has_scores_for("x")
        reg.register("x", confidence_score)
        assert reg.has_scores_for("x")

    def test_registered_stages(self):
        reg = ScoreRegistry()
        reg.register("a", confidence_score)
        reg.register("b", confidence_score)
        assert set(reg.registered_stages) == {"a", "b"}

    def test_missing_stage_returns_empty(self):
        reg = ScoreRegistry()
        cs = reg.score("missing", object())
        assert cs.value == 0.0


# ── Default registry ───────────────────────────────────────────────


class TestDefaultRegistry:
    def test_covers_all_stages(self):
        reg = default_registry()
        expected = {"diagnose", "decompose", "architect", "locate", "execute",
                    "test", "verify", "review", "integrate", "submit"}
        assert set(reg.registered_stages) == expected

    def test_each_stage_has_functions(self):
        reg = default_registry()
        for stage in reg.registered_stages:
            assert reg.has_scores_for(stage)
