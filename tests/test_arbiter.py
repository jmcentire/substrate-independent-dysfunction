"""Tests for arbiter.py — competition mode candidate selection."""

from __future__ import annotations

import pytest

from swarm.arbiter import Arbiter, Candidate
from swarm.scoring import CompositeScore, Score, ScoreRegistry, confidence_score, diff_size_score


def _make_registry():
    reg = ScoreRegistry()
    reg.register("execute", confidence_score)
    reg.register("execute", diff_size_score)
    return reg


def _make_candidate(index, confidence=0.8, diff_lines=10):
    class FakeResult:
        pass
    r = FakeResult()
    r.confidence = confidence
    r.total_diff_lines = diff_lines
    return Candidate(index=index, execute_result=r)


class TestCandidate:
    def test_default_not_disqualified(self):
        c = Candidate(index=0)
        assert not c.disqualified

    def test_disqualify(self):
        c = Candidate(index=0)
        c.disqualified = True
        c.disqualify_reason = "failed tests"
        assert c.disqualified
        assert c.disqualify_reason == "failed tests"


class TestArbiter:
    def test_select_best_candidate(self):
        reg = _make_registry()
        arbiter = Arbiter(reg)
        c1 = _make_candidate(0, confidence=0.7)
        c2 = _make_candidate(1, confidence=0.9)
        winner = arbiter.select([c1, c2])
        assert winner is not None
        assert winner.index == 1  # Higher confidence

    def test_select_with_disqualified(self):
        reg = _make_registry()
        arbiter = Arbiter(reg)
        c1 = _make_candidate(0, confidence=0.9)
        c2 = _make_candidate(1, confidence=0.7)
        arbiter.disqualify(c1, "failed")
        winner = arbiter.select([c1, c2])
        assert winner is not None
        assert winner.index == 1

    def test_all_disqualified_returns_none(self):
        reg = _make_registry()
        arbiter = Arbiter(reg)
        c1 = _make_candidate(0)
        c2 = _make_candidate(1)
        arbiter.disqualify(c1, "failed")
        arbiter.disqualify(c2, "failed")
        assert arbiter.select([c1, c2]) is None

    def test_empty_candidates(self):
        reg = _make_registry()
        arbiter = Arbiter(reg)
        assert arbiter.select([]) is None

    def test_tie_breaks_by_index(self):
        reg = _make_registry()
        arbiter = Arbiter(reg)
        c1 = _make_candidate(0, confidence=0.8, diff_lines=10)
        c2 = _make_candidate(1, confidence=0.8, diff_lines=10)
        winner = arbiter.select([c1, c2])
        assert winner is not None
        assert winner.index == 0  # Lower index wins tie

    def test_disqualify_sets_reason(self):
        reg = _make_registry()
        arbiter = Arbiter(reg)
        c = _make_candidate(0)
        arbiter.disqualify(c, "compile failure")
        assert c.disqualified
        assert c.disqualify_reason == "compile failure"

    def test_single_candidate(self):
        reg = _make_registry()
        arbiter = Arbiter(reg)
        c = _make_candidate(0, confidence=0.5)
        winner = arbiter.select([c])
        assert winner is c

    def test_scoring_prefers_small_diff(self):
        reg = _make_registry()
        arbiter = Arbiter(reg)
        c1 = _make_candidate(0, confidence=0.8, diff_lines=400)
        c2 = _make_candidate(1, confidence=0.8, diff_lines=10)
        winner = arbiter.select([c1, c2])
        assert winner is not None
        assert winner.index == 1  # Smaller diff

    def test_preexisting_composite_score(self):
        """If composite_score already set, arbiter uses it."""
        reg = _make_registry()
        arbiter = Arbiter(reg)
        c1 = _make_candidate(0, confidence=0.5)
        c1.composite_score = CompositeScore([Score(name="manual", value=0.95)])
        c2 = _make_candidate(1, confidence=0.9)
        winner = arbiter.select([c1, c2])
        assert winner is not None
        assert winner.index == 0  # Pre-set high score wins

    def test_three_candidates(self):
        reg = _make_registry()
        arbiter = Arbiter(reg)
        c1 = _make_candidate(0, confidence=0.5)
        c2 = _make_candidate(1, confidence=0.9)
        c3 = _make_candidate(2, confidence=0.7)
        winner = arbiter.select([c1, c2, c3])
        assert winner.index == 1

    def test_disqualify_middle_candidate(self):
        reg = _make_registry()
        arbiter = Arbiter(reg)
        c1 = _make_candidate(0, confidence=0.5)
        c2 = _make_candidate(1, confidence=0.9)
        c3 = _make_candidate(2, confidence=0.7)
        arbiter.disqualify(c2, "bad")
        winner = arbiter.select([c1, c2, c3])
        assert winner.index == 2

    def test_no_execute_result(self):
        reg = _make_registry()
        arbiter = Arbiter(reg)
        c = Candidate(index=0)
        winner = arbiter.select([c])
        assert winner is c

    def test_mixed_none_and_results(self):
        reg = _make_registry()
        arbiter = Arbiter(reg)
        c1 = Candidate(index=0)  # No execute_result
        c2 = _make_candidate(1, confidence=0.8)
        winner = arbiter.select([c1, c2])
        assert winner.index == 1  # Has actual score
