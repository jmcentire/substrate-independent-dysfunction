"""Tests for BudgetTracker — dollar/token tracking."""

from __future__ import annotations

import pytest

from swarm.budget import (
    BudgetExceeded,
    BudgetTracker,
    MODEL_PRICING,
    pricing_for_model,
)


class TestPricingForModel:
    def test_exact_match(self):
        inp, out = pricing_for_model("claude-haiku-4-5-20251001")
        assert inp == 0.80
        assert out == 4.00

    def test_prefix_match(self):
        inp, out = pricing_for_model("claude-sonnet-4-5-20250929")
        assert inp == 3.00

    def test_unknown_model(self):
        inp, out = pricing_for_model("unknown-model")
        assert inp == 0.80  # Defaults to Haiku


class TestBudgetTracker:
    def test_initial_state(self):
        bt = BudgetTracker()
        assert bt.project_spend == 0.0
        assert bt.project_tokens == (0, 0)

    def test_set_model_pricing(self):
        bt = BudgetTracker()
        bt.set_model_pricing("claude-sonnet-4-5-20250929")
        assert bt.input_cost_per_million == 3.00
        assert bt.output_cost_per_million == 15.00

    def test_set_model_pricing_switches_on_model_change(self):
        bt = BudgetTracker()
        bt.set_model_pricing("claude-opus-4-6")
        assert bt.input_cost_per_million == 15.0
        bt.set_model_pricing("claude-sonnet-4-5-20250929")
        assert bt.input_cost_per_million == 3.0  # Updated for per-stage routing

    def test_tokens_to_dollars(self):
        bt = BudgetTracker()
        bt.set_model_pricing("claude-haiku-4-5-20251001")
        cost = bt.tokens_to_dollars(1_000_000, 1_000_000)
        assert cost == pytest.approx(4.80)

    def test_record_tokens_within_budget(self):
        bt = BudgetTracker(per_project_cap=1.0)
        bt.set_model_pricing("claude-haiku-4-5-20251001")
        result = bt.record_tokens(100, 50)
        assert result is True
        assert bt.project_spend > 0
        assert bt.project_tokens == (100, 50)

    def test_record_tokens_exceeds_project_cap(self):
        bt = BudgetTracker(per_project_cap=0.0001)
        bt.set_model_pricing("claude-opus-4-6")
        result = bt.record_tokens(100_000, 50_000)
        assert result is False

    def test_record_tokens_exceeds_daily_cap(self):
        bt = BudgetTracker(per_project_cap=100.0, daily_cap=0.0001)
        bt.set_model_pricing("claude-opus-4-6")
        result = bt.record_tokens(100_000, 50_000)
        assert result is False

    def test_start_project_resets(self):
        bt = BudgetTracker()
        bt.set_model_pricing("claude-haiku-4-5-20251001")
        bt.record_tokens(1000, 500)
        assert bt.project_spend > 0
        bt.start_project()
        assert bt.project_spend == 0.0
        assert bt.project_tokens == (0, 0)

    def test_daily_spend_accumulates(self):
        bt = BudgetTracker()
        bt.set_model_pricing("claude-haiku-4-5-20251001")
        bt.record_tokens(1000, 500)
        spend1 = bt.daily_spend
        bt.start_project()
        bt.record_tokens(1000, 500)
        assert bt.daily_spend == pytest.approx(spend1 * 2)

    def test_budget_exceeded_exception(self):
        exc = BudgetExceeded("test")
        assert str(exc) == "test"
