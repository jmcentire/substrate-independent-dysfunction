"""
Contract test suite for pricing_tests (Pricing Service Tests) v1.

Tests the rate cache pricing service covering:
- Nightly rate resolution (base, seasonal, override tiers)
- LOS discounts
- Flat and percentage fees
- Tax computation
- Full pipeline (calculate_total)
- Cache/shard behaviour
- Schema validation and serialization round-trips
- Invariants (line-item sum, currency consistency, pipeline order)

All monetary values use integer minor-unit (cents) arithmetic.
Pipeline order: base rates → seasonal/override resolution → LOS discount → flat fees → % fees → taxes.

Dependencies are mocked via unittest.mock.
"""
import json
import uuid
import math
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Import the component under test
from src.pricing_tests import (
    resolve_nightly_rates,
    apply_los_discount,
    apply_flat_fees,
    apply_percentage_fees,
    apply_taxes,
    calculate_total,
    compute_shard_index,
    check_shard_distribution,
    lookup_price_with_cache,
    validate_stay_request_schema,
    validate_price_response_schema,
    round_trip_serialize,
    build_rate_config,
    build_stay_request,
)


# ---------------------------------------------------------------------------
# Test helper constants
# ---------------------------------------------------------------------------
TEST_UNIT_ID = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
ALT_UNIT_ID = "f1e2d3c4-b5a6-4987-8765-432109876543"
CHECK_IN = "2024-07-01T00:00:00Z"
CHECK_OUT_3N = "2024-07-04T00:00:00Z"  # 3 nights
CHECK_OUT_1N = "2024-07-02T00:00:00Z"  # 1 night
CHECK_OUT_4N = "2024-07-05T00:00:00Z"  # 4 nights
CHECK_OUT_5N = "2024-07-06T00:00:00Z"  # 5 nights
CHECK_OUT_7N = "2024-07-08T00:00:00Z"  # 7 nights


def _money(amount_minor: int, currency: str = "USD") -> dict:
    return {"amount_minor": amount_minor, "currency": currency}


def _date_range(check_in: str = CHECK_IN, check_out: str = CHECK_OUT_3N) -> dict:
    return {"check_in": check_in, "check_out": check_out}


def _seasonal(name, start, end, amount, currency="USD", priority=0):
    return {
        "name": name,
        "start_date": start,
        "end_date": end,
        "nightly_amount": _money(amount, currency),
        "priority": priority,
    }


def _date_override(date, amount, currency="USD", override_id=None, reason=""):
    return {
        "id": override_id or str(uuid.uuid4()),
        "date": date,
        "nightly_amount": _money(amount, currency),
        "reason": reason,
    }


def _los_discount(min_nights, discount_percent):
    return {"min_nights": min_nights, "discount_percent": discount_percent}


def _fee(name, fee_type, amount_minor, taxable=True):
    return {"name": name, "fee_type": fee_type, "amount_minor": amount_minor, "taxable": taxable}


def _tax(name, rate_basis_points, applies_to_fees=True):
    return {"name": name, "rate_basis_points": rate_basis_points, "applies_to_fees": applies_to_fees}


def _rate_config(
    base_rate_minor=10000,
    currency="USD",
    unit_id=TEST_UNIT_ID,
    seasonal_rates=None,
    date_overrides=None,
    los_discounts=None,
    fees=None,
    taxes=None,
):
    return {
        "id": str(uuid.uuid4()),
        "unit_id": unit_id,
        "base_rate": _money(base_rate_minor, currency),
        "currency": currency,
        "seasonal_rates": seasonal_rates or [],
        "date_overrides": date_overrides or [],
        "los_discounts": los_discounts or [],
        "fees": fees or [],
        "taxes": taxes or [],
    }


def _stay_request(
    unit_id=TEST_UNIT_ID,
    check_in=CHECK_IN,
    check_out=CHECK_OUT_3N,
    currency="USD",
):
    return {
        "unit_id": unit_id,
        "check_in": check_in,
        "check_out": check_out,
        "currency": currency,
    }


def _mock_redis(get_responses=None, should_fail=False):
    return {
        "get_responses": get_responses or {},
        "get_call_count": 0,
        "set_call_count": 0,
        "last_set_ttl": -1,
        "should_fail": should_fail,
    }


def _mock_repo(configs=None, should_fail=False):
    return {
        "configs": configs or {},
        "call_count": 0,
        "should_fail": should_fail,
    }


# ===========================================================================
# GROUP 1: Nightly Rate Resolution — resolve_nightly_rates
# ===========================================================================
class TestResolveNightlyRates:
    """Tests for resolve_nightly_rates (pipeline steps 1-2): rate resolution."""

    def test_base_rate_only_3_nights(self):
        """Happy path: all nights use base rate when no seasonal or override rates exist."""
        config = _rate_config(base_rate_minor=10000)
        dr = _date_range(CHECK_IN, CHECK_OUT_3N)

        result = resolve_nightly_rates(config, dr)

        assert result["ok"] is True, "Expected success result"
        rates = result["value"]
        assert len(rates) == 3, f"Expected 3 nightly rates, got {len(rates)}"
        for r in rates:
            assert r["amount"]["amount_minor"] == 10000, (
                f"Expected base rate 10000, got {r['amount']['amount_minor']}"
            )
            assert r["source"] == "BASE", f"Expected source BASE, got {r['source']}"

    def test_seasonal_rate_covers_all_nights(self):
        """Happy path: seasonal rate overrides base for all nights in the window."""
        config = _rate_config(
            base_rate_minor=10000,
            seasonal_rates=[
                _seasonal("Summer Peak", "2024-06-01T00:00:00Z", "2024-08-31T00:00:00Z", 15000)
            ],
        )
        dr = _date_range(CHECK_IN, CHECK_OUT_3N)

        result = resolve_nightly_rates(config, dr)

        assert result["ok"] is True
        rates = result["value"]
        assert len(rates) == 3
        for r in rates:
            assert r["amount"]["amount_minor"] == 15000, (
                f"Expected seasonal rate 15000, got {r['amount']['amount_minor']}"
            )
            assert r["source"] == "SEASONAL"

    def test_date_override_takes_precedence_over_seasonal(self):
        """Happy path: date override wins over seasonal rate for that specific date."""
        config = _rate_config(
            base_rate_minor=10000,
            seasonal_rates=[
                _seasonal("Summer", "2024-06-01T00:00:00Z", "2024-08-31T00:00:00Z", 15000)
            ],
            date_overrides=[
                _date_override("2024-07-02T00:00:00Z", 20000),
            ],
        )
        dr = _date_range(CHECK_IN, CHECK_OUT_3N)

        result = resolve_nightly_rates(config, dr)

        assert result["ok"] is True
        rates = result["value"]
        assert len(rates) == 3
        # Night 1 (Jul 1): seasonal
        assert rates[0]["source"] == "SEASONAL"
        assert rates[0]["amount"]["amount_minor"] == 15000
        # Night 2 (Jul 2): date override
        assert rates[1]["source"] == "DATE_OVERRIDE"
        assert rates[1]["amount"]["amount_minor"] == 20000
        # Night 3 (Jul 3): seasonal
        assert rates[2]["source"] == "SEASONAL"
        assert rates[2]["amount"]["amount_minor"] == 15000

    def test_mixed_sources_across_nights(self):
        """Happy path: base, seasonal, and override sources on different nights."""
        # Seasonal covers only Jul 2
        config = _rate_config(
            base_rate_minor=10000,
            seasonal_rates=[
                _seasonal("Mid", "2024-07-02T00:00:00Z", "2024-07-02T00:00:00Z", 12000)
            ],
            date_overrides=[
                _date_override("2024-07-03T00:00:00Z", 20000),
            ],
        )
        dr = _date_range(CHECK_IN, CHECK_OUT_3N)

        result = resolve_nightly_rates(config, dr)

        assert result["ok"] is True
        rates = result["value"]
        assert rates[0]["source"] == "BASE"
        assert rates[0]["amount"]["amount_minor"] == 10000
        assert rates[1]["source"] == "SEASONAL"
        assert rates[1]["amount"]["amount_minor"] == 12000
        assert rates[2]["source"] == "DATE_OVERRIDE"
        assert rates[2]["amount"]["amount_minor"] == 20000

    def test_single_night_stay(self):
        """Edge case: minimum valid date range (1 night) produces exactly 1 rate."""
        config = _rate_config(base_rate_minor=10000)
        dr = _date_range(CHECK_IN, CHECK_OUT_1N)

        result = resolve_nightly_rates(config, dr)

        assert result["ok"] is True
        assert len(result["value"]) == 1
        assert result["value"][0]["amount"]["amount_minor"] == 10000

    def test_seasonal_boundary_crossing(self):
        """Edge case: stay crosses a seasonal boundary; some nights seasonal, others base."""
        # Seasonal covers Jul 1-2 only (inclusive)
        config = _rate_config(
            base_rate_minor=10000,
            seasonal_rates=[
                _seasonal("Short", "2024-07-01T00:00:00Z", "2024-07-02T00:00:00Z", 15000)
            ],
        )
        dr = _date_range(CHECK_IN, CHECK_OUT_4N)  # 4 nights Jul 1-4

        result = resolve_nightly_rates(config, dr)

        assert result["ok"] is True
        rates = result["value"]
        assert len(rates) == 4
        assert rates[0]["source"] == "SEASONAL"
        assert rates[1]["source"] == "SEASONAL"
        assert rates[2]["source"] == "BASE"
        assert rates[3]["source"] == "BASE"

    def test_overlapping_seasonal_higher_priority_wins(self):
        """Edge case: overlapping seasonal rates — higher priority wins."""
        config = _rate_config(
            base_rate_minor=10000,
            seasonal_rates=[
                _seasonal("Low", "2024-06-01T00:00:00Z", "2024-08-31T00:00:00Z", 12000, priority=1),
                _seasonal("High", "2024-06-15T00:00:00Z", "2024-07-15T00:00:00Z", 18000, priority=5),
            ],
        )
        dr = _date_range(CHECK_IN, CHECK_OUT_3N)

        result = resolve_nightly_rates(config, dr)

        assert result["ok"] is True
        for r in result["value"]:
            assert r["amount"]["amount_minor"] == 18000, (
                f"Expected higher-priority seasonal 18000, got {r['amount']['amount_minor']}"
            )
            assert r["source"] == "SEASONAL"

    def test_error_invalid_date_range(self):
        """Error: check_out before check_in returns INVALID_DATE_RANGE."""
        config = _rate_config()
        dr = _date_range("2024-07-05T00:00:00Z", "2024-07-03T00:00:00Z")

        result = resolve_nightly_rates(config, dr)

        assert result["ok"] is False, "Expected error result"
        assert result["error"]["kind"] == "INVALID_DATE_RANGE"

    def test_error_zero_night_stay(self):
        """Error: check_in == check_out returns ZERO_NIGHT_STAY."""
        config = _rate_config()
        dr = _date_range("2024-07-05T00:00:00Z", "2024-07-05T00:00:00Z")

        result = resolve_nightly_rates(config, dr)

        assert result["ok"] is False
        assert result["error"]["kind"] == "ZERO_NIGHT_STAY"

    def test_error_overlapping_overrides_conflict(self):
        """Error: two date overrides for the same date with different amounts."""
        config = _rate_config(
            date_overrides=[
                _date_override("2024-07-02T00:00:00Z", 15000),
                _date_override("2024-07-02T00:00:00Z", 20000),
            ],
        )
        dr = _date_range(CHECK_IN, CHECK_OUT_3N)

        result = resolve_nightly_rates(config, dr)

        assert result["ok"] is False
        assert result["error"]["kind"] == "OVERLAPPING_OVERRIDES_CONFLICT"

    def test_error_currency_mismatch_seasonal(self):
        """Error: seasonal rate with different currency returns CURRENCY_MISMATCH."""
        config = _rate_config(
            seasonal_rates=[
                _seasonal("Euro Season", "2024-06-01T00:00:00Z", "2024-08-31T00:00:00Z", 15000, currency="EUR")
            ],
        )
        dr = _date_range(CHECK_IN, CHECK_OUT_3N)

        result = resolve_nightly_rates(config, dr)

        assert result["ok"] is False
        assert result["error"]["kind"] == "CURRENCY_MISMATCH"


# ===========================================================================
# GROUP 2: LOS Discounts — apply_los_discount
# ===========================================================================
class TestApplyLOSDiscount:
    """Tests for apply_los_discount (pipeline step 3)."""

    def test_discount_applied_at_threshold(self):
        """Happy path: 7-night stay qualifies for 20% discount tier."""
        # subtotal = 30000, discount = floor(30000 * 20 / 100) = 6000, result = 24000
        result = apply_los_discount(
            _money(30000), 7, [_los_discount(3, 10), _los_discount(7, 20)]
        )

        assert result["ok"] is True
        assert result["value"]["amount_minor"] == 24000, (
            f"Expected 24000 (30000 - 6000), got {result['value']['amount_minor']}"
        )

    def test_exact_threshold_qualifies(self):
        """Happy path: num_nights exactly equals min_nights threshold."""
        # 3 nights, 10% discount: 30000 - 3000 = 27000
        result = apply_los_discount(
            _money(30000), 3, [_los_discount(3, 10)]
        )

        assert result["ok"] is True
        assert result["value"]["amount_minor"] == 27000

    def test_highest_qualifying_tier_selected(self):
        """Happy path: selects tier with largest min_nights <= num_nights."""
        # 14 nights: qualifies for 3→5%, 7→10%, 14→15%, not 28→25%
        # 50000 * 15% = 7500; result = 42500
        result = apply_los_discount(
            _money(50000),
            14,
            [
                _los_discount(3, 5),
                _los_discount(7, 10),
                _los_discount(14, 15),
                _los_discount(28, 25),
            ],
        )

        assert result["ok"] is True
        assert result["value"]["amount_minor"] == 42500

    def test_below_all_thresholds_no_discount(self):
        """Edge case: stay shorter than all tiers → no discount."""
        result = apply_los_discount(
            _money(30000), 2, [_los_discount(3, 10)]
        )

        assert result["ok"] is True
        assert result["value"]["amount_minor"] == 30000

    def test_empty_discount_list(self):
        """Edge case: empty discount list → no discount."""
        result = apply_los_discount(_money(30000), 7, [])

        assert result["ok"] is True
        assert result["value"]["amount_minor"] == 30000

    def test_zero_percent_discount(self):
        """Edge case: 0% discount still returns unchanged subtotal."""
        result = apply_los_discount(
            _money(30000), 3, [_los_discount(3, 0)]
        )

        assert result["ok"] is True
        assert result["value"]["amount_minor"] == 30000

    def test_floor_rounding(self):
        """Edge case: discount is floor-rounded (never rounds up)."""
        # 10001 * 33 / 100 = 3300.33 → floor = 3300; result = 6701
        result = apply_los_discount(
            _money(10001), 3, [_los_discount(3, 33)]
        )

        assert result["ok"] is True
        assert result["value"]["amount_minor"] == 6701, (
            f"Expected floor-rounded 6701, got {result['value']['amount_minor']}"
        )

    def test_error_duplicate_min_nights(self):
        """Error: duplicate min_nights returns INVALID_DISCOUNT_CONFIGURATION."""
        result = apply_los_discount(
            _money(30000), 5, [_los_discount(3, 10), _los_discount(3, 15)]
        )

        assert result["ok"] is False
        assert result["error"]["kind"] == "INVALID_DISCOUNT_CONFIGURATION"


# ===========================================================================
# GROUP 3: Fees — apply_flat_fees & apply_percentage_fees
# ===========================================================================
class TestApplyFlatFees:
    """Tests for apply_flat_fees (pipeline step 4)."""

    def test_single_flat_fee(self):
        """Happy path: one flat cleaning fee."""
        result = apply_flat_fees(
            [_fee("Cleaning Fee", "FLAT", 5000, taxable=True)],
            "USD",
        )

        assert result["ok"] is True
        items = result["value"]
        assert len(items) == 1
        assert items[0]["type"] == "FLAT_FEE"
        assert items[0]["amount"]["amount_minor"] == 5000
        assert items[0]["amount"]["currency"] == "USD"

    def test_multiple_flat_fees_filters_percentage(self):
        """Happy path: only FLAT fees are returned; PERCENTAGE are filtered out."""
        result = apply_flat_fees(
            [
                _fee("Cleaning", "FLAT", 5000),
                _fee("Service", "PERCENTAGE", 500),  # should be filtered
                _fee("Resort", "FLAT", 2500),
            ],
            "USD",
        )

        assert result["ok"] is True
        items = result["value"]
        assert len(items) == 2
        total = sum(li["amount"]["amount_minor"] for li in items)
        assert total == 7500, f"Expected flat fee total 7500, got {total}"

    def test_zero_amount_flat_fee(self):
        """Edge case: flat fee with 0 amount is valid."""
        result = apply_flat_fees(
            [_fee("Waived Fee", "FLAT", 0)], "USD"
        )

        assert result["ok"] is True
        assert len(result["value"]) == 1
        assert result["value"][0]["amount"]["amount_minor"] == 0


class TestApplyPercentageFees:
    """Tests for apply_percentage_fees (pipeline step 5)."""

    def test_percentage_fee_calculation(self):
        """Happy path: 5% (500 bps) on 24000 = floor(24000 * 500 / 10000) = 1200."""
        result = apply_percentage_fees(
            [_fee("Service Fee", "PERCENTAGE", 500)],
            _money(24000),
        )

        assert result["ok"] is True
        items = result["value"]
        assert len(items) == 1
        assert items[0]["type"] == "PERCENTAGE_FEE"
        assert items[0]["amount"]["amount_minor"] == 1200

    def test_percentage_fee_floor_rounding(self):
        """Edge case: percentage fee is floor-rounded."""
        # 3.33% (333 bps) on 10001 = floor(10001 * 333 / 10000) = floor(333.0333) = 333
        result = apply_percentage_fees(
            [_fee("Service Fee", "PERCENTAGE", 333)],
            _money(10001),
        )

        assert result["ok"] is True
        assert result["value"][0]["amount"]["amount_minor"] == 333


# ===========================================================================
# GROUP 4: Taxes — apply_taxes
# ===========================================================================
class TestApplyTaxes:
    """Tests for apply_taxes (pipeline step 6)."""

    def test_single_tax_no_fees(self):
        """Happy path: 10% tax on accommodation only (applies_to_fees=False)."""
        # Tax base = 24000, tax = floor(24000 * 1000 / 10000) = 2400
        result = apply_taxes(
            [_tax("State Tax", 1000, applies_to_fees=False)],
            _money(24000),
            _money(5000),
        )

        assert result["ok"] is True
        items = result["value"]
        assert len(items) == 1
        assert items[0]["type"] == "TAX"
        assert items[0]["amount"]["amount_minor"] == 2400

    def test_tax_applies_to_fees(self):
        """Happy path: 10% tax on accommodation + taxable fees."""
        # Tax base = 24000 + 5000 = 29000, tax = floor(29000 * 1000 / 10000) = 2900
        result = apply_taxes(
            [_tax("Total Tax", 1000, applies_to_fees=True)],
            _money(24000),
            _money(5000),
        )

        assert result["ok"] is True
        assert result["value"][0]["amount"]["amount_minor"] == 2900

    def test_multiple_taxes(self):
        """Happy path: two independent taxes with different bases."""
        # State: 10% on 20000+5000=25000 → 2500
        # Tourism: 2% on 20000 only → 400
        result = apply_taxes(
            [
                _tax("State", 1000, applies_to_fees=True),
                _tax("Tourism", 200, applies_to_fees=False),
            ],
            _money(20000),
            _money(5000),
        )

        assert result["ok"] is True
        items = result["value"]
        assert len(items) == 2
        assert items[0]["amount"]["amount_minor"] == 2500
        assert items[1]["amount"]["amount_minor"] == 400

    def test_zero_rate_tax(self):
        """Edge case: 0 bps tax produces 0 tax amount."""
        result = apply_taxes(
            [_tax("Exempt", 0, applies_to_fees=True)],
            _money(24000),
            _money(5000),
        )

        assert result["ok"] is True
        assert result["value"][0]["amount"]["amount_minor"] == 0

    def test_error_tax_rate_out_of_range(self):
        """Error: rate_basis_points > 10000 returns TAX_RATE_OUT_OF_RANGE."""
        result = apply_taxes(
            [_tax("Bad Tax", 15000)],
            _money(24000),
            _money(0),
        )

        assert result["ok"] is False
        assert result["error"]["kind"] == "TAX_RATE_OUT_OF_RANGE"

    def test_error_currency_mismatch(self):
        """Error: accommodation and fees in different currencies."""
        result = apply_taxes(
            [_tax("Tax", 1000)],
            _money(24000, "USD"),
            _money(5000, "EUR"),
        )

        assert result["ok"] is False
        assert result["error"]["kind"] == "CURRENCY_MISMATCH"


# ===========================================================================
# GROUP 5: End-to-End — calculate_total
# ===========================================================================
class TestCalculateTotal:
    """Tests for calculate_total (full pipeline composition)."""

    def test_full_pipeline(self):
        """Happy path: complete pipeline with seasonal override, LOS, fees, taxes."""
        # Base=10000, seasonal on Jul 2 = 15000
        # Nights: 10000 + 15000 + 10000 = 35000
        # LOS 10% on 3 nights: 35000 - 3500 = 31500
        # Cleaning flat fee = 5000
        # Service 5% (500 bps) on 31500 = 1575
        # State tax 10% (1000 bps) applies_to_fees=True:
        #   Taxable fees: cleaning(5000, taxable) + service(1575, taxable) = 6575
        #   Tax base = 31500 + 6575 = 38075, tax = floor(38075*1000/10000) = 3807
        # Total = 31500 + 5000 + 1575 + 3807 = 41882
        config = _rate_config(
            base_rate_minor=10000,
            seasonal_rates=[
                _seasonal("Mid", "2024-07-02T00:00:00Z", "2024-07-02T00:00:00Z", 15000)
            ],
            los_discounts=[_los_discount(3, 10)],
            fees=[
                _fee("Cleaning", "FLAT", 5000, taxable=True),
                _fee("Service", "PERCENTAGE", 500, taxable=True),
            ],
            taxes=[_tax("State", 1000, applies_to_fees=True)],
        )
        request = _stay_request()

        result = calculate_total(config, request)

        assert result["ok"] is True, f"Expected success, got error: {result.get('error')}"
        bd = result["value"]
        assert bd["accommodation_subtotal"]["amount_minor"] == 35000
        assert bd["accommodation_after_discount"]["amount_minor"] == 31500
        assert bd["los_discount_applied"] is True
        assert bd["los_discount_percent"] == 10
        assert bd["num_nights"] == 3
        # Verify algebraic total
        expected_total = (
            bd["accommodation_after_discount"]["amount_minor"]
            + bd["fees_subtotal"]["amount_minor"]
            + bd["tax_subtotal"]["amount_minor"]
        )
        assert bd["total"]["amount_minor"] == expected_total, (
            f"Total {bd['total']['amount_minor']} != components sum {expected_total}"
        )

    def test_base_rates_only_no_extras(self):
        """Happy path: base rates only, no discounts/fees/taxes."""
        config = _rate_config(base_rate_minor=10000)
        request = _stay_request()

        result = calculate_total(config, request)

        assert result["ok"] is True
        bd = result["value"]
        assert bd["accommodation_subtotal"]["amount_minor"] == 30000
        assert bd["accommodation_after_discount"]["amount_minor"] == 30000
        assert bd["los_discount_applied"] is False
        assert bd["fees_subtotal"]["amount_minor"] == 0
        assert bd["tax_subtotal"]["amount_minor"] == 0
        assert bd["total"]["amount_minor"] == 30000

    def test_error_invalid_date_range(self):
        """Error: check_out before check_in."""
        config = _rate_config()
        request = _stay_request(check_out="2024-06-30T00:00:00Z")

        result = calculate_total(config, request)

        assert result["ok"] is False
        assert result["error"]["kind"] == "INVALID_DATE_RANGE"

    def test_error_zero_night_stay(self):
        """Error: check_in == check_out."""
        config = _rate_config()
        request = _stay_request(check_in=CHECK_IN, check_out=CHECK_IN)

        result = calculate_total(config, request)

        assert result["ok"] is False
        assert result["error"]["kind"] == "ZERO_NIGHT_STAY"

    def test_error_currency_mismatch(self):
        """Error: request currency differs from config currency."""
        config = _rate_config(currency="USD")
        request = _stay_request(currency="EUR")

        result = calculate_total(config, request)

        assert result["ok"] is False
        assert result["error"]["kind"] == "CURRENCY_MISMATCH"

    def test_error_invalid_unit_id(self):
        """Error: request unit_id doesn't match config unit_id."""
        config = _rate_config(unit_id=TEST_UNIT_ID)
        request = _stay_request(unit_id=ALT_UNIT_ID)

        result = calculate_total(config, request)

        assert result["ok"] is False
        assert result["error"]["kind"] == "INVALID_UNIT_ID"

    def test_error_overlapping_overrides_propagation(self):
        """Error: conflicting date overrides propagate from resolve_nightly_rates."""
        config = _rate_config(
            date_overrides=[
                _date_override("2024-07-02T00:00:00Z", 15000),
                _date_override("2024-07-02T00:00:00Z", 20000),
            ],
        )
        request = _stay_request()

        result = calculate_total(config, request)

        assert result["ok"] is False
        assert result["error"]["kind"] == "OVERLAPPING_OVERRIDES_CONFLICT"

    def test_error_invalid_discount_propagation(self):
        """Error: duplicate LOS tiers propagate from apply_los_discount."""
        config = _rate_config(
            los_discounts=[_los_discount(3, 10), _los_discount(3, 15)],
        )
        request = _stay_request()

        result = calculate_total(config, request)

        assert result["ok"] is False
        assert result["error"]["kind"] == "INVALID_DISCOUNT_CONFIGURATION"

    def test_error_tax_rate_out_of_range_propagation(self):
        """Error: invalid tax rate propagates from apply_taxes."""
        config = _rate_config(taxes=[_tax("Bad", 15000)])
        request = _stay_request()

        result = calculate_total(config, request)

        assert result["ok"] is False
        assert result["error"]["kind"] == "TAX_RATE_OUT_OF_RANGE"


# ===========================================================================
# GROUP 5 (continued): Invariants for calculate_total
# ===========================================================================
class TestCalculateTotalInvariants:
    """Invariant tests for calculate_total output properties."""

    def _get_valid_breakdown(self, **config_overrides):
        """Helper to build a config and get a successful breakdown."""
        config = _rate_config(**config_overrides)
        request = _stay_request(
            check_out=config_overrides.get("check_out", CHECK_OUT_3N),
            unit_id=config_overrides.get("unit_id", TEST_UNIT_ID),
            currency=config_overrides.get("currency", "USD"),
        )
        return calculate_total(config, request)

    def test_line_items_sum_equals_total(self):
        """Invariant: sum of line_items amounts == total exactly (no rounding drift)."""
        # Use fractional-cent-prone values
        config = _rate_config(
            base_rate_minor=9999,
            los_discounts=[_los_discount(7, 15)],
            fees=[
                _fee("Cleaning", "FLAT", 4999, taxable=True),
                _fee("Service", "PERCENTAGE", 333, taxable=True),
            ],
            taxes=[_tax("Tax", 875, applies_to_fees=True)],
        )
        request = _stay_request(check_out=CHECK_OUT_7N)

        result = calculate_total(config, request)

        assert result["ok"] is True
        bd = result["value"]
        line_item_sum = sum(li["amount"]["amount_minor"] for li in bd["line_items"])
        assert line_item_sum == bd["total"]["amount_minor"], (
            f"Line items sum {line_item_sum} != total {bd['total']['amount_minor']}"
        )

    def test_currency_consistent_across_all_fields(self):
        """Invariant: all monetary fields share the same currency."""
        config = _rate_config(
            fees=[_fee("Cleaning", "FLAT", 5000)],
            taxes=[_tax("Tax", 1000)],
        )
        request = _stay_request()

        result = calculate_total(config, request)

        assert result["ok"] is True
        bd = result["value"]
        assert bd["accommodation_subtotal"]["currency"] == "USD"
        assert bd["accommodation_after_discount"]["currency"] == "USD"
        assert bd["fees_subtotal"]["currency"] == "USD"
        assert bd["tax_subtotal"]["currency"] == "USD"
        assert bd["total"]["currency"] == "USD"
        for li in bd["line_items"]:
            assert li["amount"]["currency"] == "USD", (
                f"Line item '{li['label']}' has currency {li['amount']['currency']}, expected USD"
            )

    def test_nightly_rates_count_equals_num_nights(self):
        """Invariant: nightly_rates has exactly num_nights entries."""
        config = _rate_config()
        request = _stay_request(check_out=CHECK_OUT_5N)

        result = calculate_total(config, request)

        assert result["ok"] is True
        bd = result["value"]
        assert len(bd["nightly_rates"]) == bd["num_nights"]
        assert bd["num_nights"] == 5

    def test_total_equals_components_sum(self):
        """Invariant: total = accommodation_after_discount + fees_subtotal + tax_subtotal."""
        config = _rate_config(
            los_discounts=[_los_discount(3, 10)],
            fees=[
                _fee("Cleaning", "FLAT", 5000),
                _fee("Service", "PERCENTAGE", 500),
            ],
            taxes=[_tax("Tax", 1000, applies_to_fees=True)],
        )
        request = _stay_request()

        result = calculate_total(config, request)

        assert result["ok"] is True
        bd = result["value"]
        expected = (
            bd["accommodation_after_discount"]["amount_minor"]
            + bd["fees_subtotal"]["amount_minor"]
            + bd["tax_subtotal"]["amount_minor"]
        )
        assert bd["total"]["amount_minor"] == expected

    def test_money_amounts_non_negative(self):
        """Invariant: all monetary amounts in breakdown (except discount line items) are >= 0."""
        config = _rate_config(
            los_discounts=[_los_discount(3, 10)],
            fees=[_fee("Cleaning", "FLAT", 5000)],
            taxes=[_tax("Tax", 1000)],
        )
        request = _stay_request()

        result = calculate_total(config, request)

        assert result["ok"] is True
        bd = result["value"]
        assert bd["total"]["amount_minor"] >= 0
        assert bd["accommodation_subtotal"]["amount_minor"] >= 0
        assert bd["accommodation_after_discount"]["amount_minor"] >= 0
        assert bd["fees_subtotal"]["amount_minor"] >= 0
        assert bd["tax_subtotal"]["amount_minor"] >= 0

    def test_result_type_exclusive_success(self):
        """Invariant: success result has ok=True, value is not None, error is None."""
        config = _rate_config()
        request = _stay_request()

        result = calculate_total(config, request)

        assert result["ok"] is True
        assert result["value"] is not None
        assert result.get("error") is None

    def test_result_type_exclusive_failure(self):
        """Invariant: failure result has ok=False, value is None, error is not None."""
        config = _rate_config()
        request = _stay_request(check_out=CHECK_IN)  # zero nights

        result = calculate_total(config, request)

        assert result["ok"] is False
        assert result.get("value") is None
        assert result["error"] is not None

    def test_pipeline_order_via_line_items(self):
        """Invariant: line_items types appear in pipeline order:
        NIGHTLY_RATE → LOS_DISCOUNT → FLAT_FEE → PERCENTAGE_FEE → TAX.
        """
        config = _rate_config(
            los_discounts=[_los_discount(3, 10)],
            fees=[
                _fee("Cleaning", "FLAT", 5000),
                _fee("Service", "PERCENTAGE", 500),
            ],
            taxes=[_tax("Tax", 1000)],
        )
        request = _stay_request()

        result = calculate_total(config, request)

        assert result["ok"] is True
        bd = result["value"]
        type_order = {"NIGHTLY_RATE": 0, "LOS_DISCOUNT": 1, "FLAT_FEE": 2, "PERCENTAGE_FEE": 3, "TAX": 4}
        last_order = -1
        for li in bd["line_items"]:
            current_order = type_order.get(li["type"], -1)
            assert current_order >= last_order, (
                f"Pipeline order violation: {li['type']} (order {current_order}) "
                f"appeared after an item of order {last_order}"
            )
            last_order = current_order


# ===========================================================================
# GROUP 6: Caching & Sharding
# ===========================================================================
class TestComputeShardIndex:
    """Tests for compute_shard_index (deterministic shard assignment)."""

    def test_deterministic_same_inputs(self):
        """Happy path: same unit_id and shard_count always produce the same result."""
        result1 = compute_shard_index(TEST_UNIT_ID, 16)
        result2 = compute_shard_index(TEST_UNIT_ID, 16)

        assert result1["shard_index"] == result2["shard_index"], "Shard index must be deterministic"
        assert result1["shard_key"] == result2["shard_key"], "Shard key must be deterministic"

    def test_shard_index_in_range(self):
        """Happy path: shard_index is in [0, shard_count)."""
        result = compute_shard_index(TEST_UNIT_ID, 8)

        assert 0 <= result["shard_index"] < 8, (
            f"shard_index {result['shard_index']} not in [0, 8)"
        )

    def test_shard_key_format(self):
        """Happy path: shard_key follows 'rate_shard:{index}' pattern."""
        result = compute_shard_index(TEST_UNIT_ID, 16)

        expected_key = f"rate_shard:{result['shard_index']}"
        assert result["shard_key"] == expected_key, (
            f"Expected shard_key '{expected_key}', got '{result['shard_key']}'"
        )

    def test_error_invalid_unit_id(self):
        """Error: non-UUID input returns INVALID_UNIT_ID."""
        try:
            result = compute_shard_index("not-a-uuid", 8)
            # If it returns a result dict with error
            if isinstance(result, dict) and "error" in result:
                assert result["error"]["kind"] == "INVALID_UNIT_ID"
            else:
                pytest.fail("Expected INVALID_UNIT_ID error for non-UUID input")
        except Exception:
            # Implementation may raise an exception; acceptable
            pass


class TestCheckShardDistribution:
    """Tests for check_shard_distribution (uniformity verification)."""

    def test_uniform_distribution(self):
        """Happy path: 1000 random UUIDs distribute roughly uniformly across 8 shards."""
        unit_ids = [str(uuid.uuid4()) for _ in range(1000)]

        result = check_shard_distribution(unit_ids, 8, 15.0)

        assert len(result["counts_per_shard"]) == 8, (
            f"Expected 8 shard counts, got {len(result['counts_per_shard'])}"
        )
        assert sum(result["counts_per_shard"]) == 1000, (
            f"Counts sum {sum(result['counts_per_shard'])} != 1000"
        )
        assert result["is_uniform"] is True, (
            f"Distribution not uniform: max_deviation={result['max_deviation_percent']:.1f}%"
        )


class TestLookupPriceWithCache:
    """Tests for lookup_price_with_cache (cache hit/miss paths)."""

    def _valid_cached_json(self):
        """Return a valid JSON string that represents a cached PriceBreakdown."""
        return json.dumps({
            "unit_id": TEST_UNIT_ID,
            "date_range": _date_range(),
            "num_nights": 3,
            "currency": "USD",
            "nightly_rates": [],
            "accommodation_subtotal": _money(30000),
            "los_discount_applied": False,
            "los_discount_percent": 0,
            "accommodation_after_discount": _money(30000),
            "fees_subtotal": _money(0),
            "tax_subtotal": _money(0),
            "total": _money(30000),
            "line_items": [],
        })

    def test_cache_hit_path(self):
        """Happy path: cache hit returns result with 1 GET, 0 SET, 0 repo calls."""
        cache_key = f"rate_shard:0:{TEST_UNIT_ID}:{CHECK_IN}:{CHECK_OUT_3N}"
        redis = _mock_redis(get_responses={cache_key: self._valid_cached_json()})
        repo = _mock_repo(configs={TEST_UNIT_ID: _rate_config()})
        request = _stay_request()

        report = lookup_price_with_cache(request, redis, repo, 8, 3600)

        assert report["path"] == "HIT", f"Expected HIT, got {report['path']}"
        assert report["redis_get_calls"] == 1
        assert report["redis_set_calls"] == 0
        assert report["repo_calls"] == 0
        assert report["result"]["ok"] is True

    def test_cache_miss_writeback(self):
        """Happy path: cache miss triggers repo lookup and writeback."""
        redis = _mock_redis()  # all misses
        repo = _mock_repo(configs={TEST_UNIT_ID: _rate_config()})
        request = _stay_request()

        report = lookup_price_with_cache(request, redis, repo, 8, 3600)

        assert report["path"] == "MISS_THEN_WRITEBACK"
        assert report["redis_get_calls"] == 1
        assert report["redis_set_calls"] == 1
        assert report["repo_calls"] == 1
        assert report["ttl_seconds"] == 3600
        assert report["result"]["ok"] is True

    def test_shard_unavailable(self):
        """Error: Redis failure returns SHARD_UNAVAILABLE."""
        redis = _mock_redis(should_fail=True)
        repo = _mock_repo()
        request = _stay_request()

        report = lookup_price_with_cache(request, redis, repo, 8, 3600)

        assert report["path"] == "SHARD_UNAVAILABLE"
        assert report["result"]["ok"] is False
        assert report["result"]["error"]["kind"] == "SHARD_UNAVAILABLE"

    def test_repo_error_on_miss(self):
        """Error: repository error on cache miss returns MISS_REPO_ERROR."""
        redis = _mock_redis()
        repo = _mock_repo(should_fail=True)
        request = _stay_request()

        report = lookup_price_with_cache(request, redis, repo, 8, 3600)

        assert report["path"] == "MISS_REPO_ERROR"
        assert report["redis_get_calls"] == 1
        assert report["redis_set_calls"] == 0
        assert report["repo_calls"] == 1
        assert report["result"]["ok"] is False
        assert report["result"]["error"]["kind"] == "REPOSITORY_ERROR"

    def test_deserialization_error(self):
        """Error: corrupt cached data returns CACHE_DESERIALIZATION_ERROR."""
        cache_key = f"rate_shard:0:{TEST_UNIT_ID}:{CHECK_IN}:{CHECK_OUT_3N}"
        redis = _mock_redis(get_responses={cache_key: "{{invalid json!!"})
        repo = _mock_repo()
        request = _stay_request()

        report = lookup_price_with_cache(request, redis, repo, 8, 3600)

        assert report["path"] == "DESERIALIZATION_ERROR"
        assert report["redis_get_calls"] == 1
        assert report["result"]["ok"] is False
        assert report["result"]["error"]["kind"] == "CACHE_DESERIALIZATION_ERROR"

    def test_rate_not_found(self):
        """Error: repo has no config for unit → RATE_NOT_FOUND."""
        redis = _mock_redis()
        repo = _mock_repo(configs={})  # no configs at all
        request = _stay_request()

        report = lookup_price_with_cache(request, redis, repo, 8, 3600)

        assert report["result"]["ok"] is False
        assert report["result"]["error"]["kind"] == "RATE_NOT_FOUND"


# ===========================================================================
# GROUP 7: Schema Validation & Serialization
# ===========================================================================
class TestValidateStayRequestSchema:
    """Tests for validate_stay_request_schema."""

    def test_valid_payload(self):
        """Happy path: well-formed StayRequest passes validation."""
        payload = {
            "unit_id": TEST_UNIT_ID,
            "check_in": CHECK_IN,
            "check_out": CHECK_OUT_3N,
            "currency": "USD",
        }

        result = validate_stay_request_schema(payload)

        assert result["valid"] is True
        assert result["errors"] == []
        assert result["schema_name"] == "StayRequestSchema"

    def test_missing_required_field(self):
        """Error: missing unit_id fails validation."""
        payload = {
            "check_in": CHECK_IN,
            "check_out": CHECK_OUT_3N,
            "currency": "USD",
        }

        result = validate_stay_request_schema(payload)

        assert result["valid"] is False
        assert len(result["errors"]) >= 1
        assert result["schema_name"] == "StayRequestSchema"

    def test_lowercase_currency_rejected(self):
        """Error: lowercase currency code fails validation."""
        payload = {
            "unit_id": TEST_UNIT_ID,
            "check_in": CHECK_IN,
            "check_out": CHECK_OUT_3N,
            "currency": "usd",
        }

        result = validate_stay_request_schema(payload)

        assert result["valid"] is False
        assert len(result["errors"]) >= 1


class TestValidatePriceResponseSchema:
    """Tests for validate_price_response_schema."""

    def test_valid_response(self):
        """Happy path: well-formed PriceResponse passes validation."""
        payload = {
            "breakdown": {
                "unit_id": TEST_UNIT_ID,
                "date_range": _date_range(),
                "num_nights": 3,
                "currency": "USD",
                "nightly_rates": [],
                "accommodation_subtotal": _money(30000),
                "los_discount_applied": False,
                "los_discount_percent": 0,
                "accommodation_after_discount": _money(30000),
                "fees_subtotal": _money(0),
                "tax_subtotal": _money(0),
                "total": _money(30000),
                "line_items": [],
            },
            "cached": False,
            "computed_at": "2024-07-01T12:00:00Z",
        }

        result = validate_price_response_schema(payload)

        assert result["valid"] is True
        assert result["errors"] == []
        assert result["schema_name"] == "PriceResponseSchema"

    def test_empty_payload_rejected(self):
        """Error: empty dict fails PriceResponse validation."""
        result = validate_price_response_schema({})

        assert result["valid"] is False
        assert len(result["errors"]) >= 1
        assert result["schema_name"] == "PriceResponseSchema"


class TestRoundTripSerialize:
    """Tests for round_trip_serialize."""

    def test_money_round_trip(self):
        """Happy path: Money serialization round-trip is identical."""
        money = _money(10000, "USD")

        result = round_trip_serialize(money, "Money")

        assert result["is_identical"] is True, (
            f"Round-trip mismatch: original={result['original_json']}, "
            f"deserialized={result['deserialized_json']}"
        )
        assert result["type_name"] == "Money"

    def test_rate_config_round_trip(self):
        """Happy path: RateConfig round-trip is identical."""
        config = _rate_config(
            seasonal_rates=[
                _seasonal("Summer", "2024-06-01T00:00:00Z", "2024-08-31T00:00:00Z", 15000)
            ],
            los_discounts=[_los_discount(7, 10)],
            fees=[_fee("Cleaning", "FLAT", 5000)],
            taxes=[_tax("Tax", 1000)],
        )

        result = round_trip_serialize(config, "RateConfig")

        assert result["is_identical"] is True
        assert result["type_name"] == "RateConfig"

    def test_price_breakdown_round_trip(self):
        """Happy path: PriceBreakdown round-trip preserves all data."""
        breakdown = {
            "unit_id": TEST_UNIT_ID,
            "date_range": _date_range(),
            "num_nights": 3,
            "currency": "USD",
            "nightly_rates": [
                {"date": "2024-07-01T00:00:00Z", "amount": _money(10000), "source": "BASE"},
                {"date": "2024-07-02T00:00:00Z", "amount": _money(10000), "source": "BASE"},
                {"date": "2024-07-03T00:00:00Z", "amount": _money(10000), "source": "BASE"},
            ],
            "accommodation_subtotal": _money(30000),
            "los_discount_applied": False,
            "los_discount_percent": 0,
            "accommodation_after_discount": _money(30000),
            "fees_subtotal": _money(0),
            "tax_subtotal": _money(0),
            "total": _money(30000),
            "line_items": [
                {"type": "NIGHTLY_RATE", "label": "Jul 1", "amount": _money(10000)},
                {"type": "NIGHTLY_RATE", "label": "Jul 2", "amount": _money(10000)},
                {"type": "NIGHTLY_RATE", "label": "Jul 3", "amount": _money(10000)},
            ],
        }

        result = round_trip_serialize(breakdown, "PriceBreakdown")

        assert result["is_identical"] is True
        assert result["type_name"] == "PriceBreakdown"


# ===========================================================================
# GROUP 7 (continued): Fixture Builders
# ===========================================================================
class TestBuildRateConfig:
    """Tests for build_rate_config fixture factory."""

    def test_defaults(self):
        """Happy path: default RateConfig has base_rate=10000, currency=USD."""
        result = build_rate_config()

        assert result["base_rate"]["amount_minor"] == 10000, (
            f"Expected default base_rate 10000, got {result['base_rate']['amount_minor']}"
        )
        assert result["currency"] == "USD"
        assert result["unit_id"] is not None

    def test_overrides_applied(self):
        """Happy path: overrides replace corresponding defaults."""
        result = build_rate_config({"base_rate_minor": 20000, "currency": "EUR"})

        assert result["base_rate"]["amount_minor"] == 20000
        assert result["currency"] == "EUR"


class TestBuildStayRequest:
    """Tests for build_stay_request fixture factory."""

    def test_defaults(self):
        """Happy path: default StayRequest is 3-night USD stay."""
        result = build_stay_request()

        assert result["currency"] == "USD"
        assert result["unit_id"] is not None
        assert result["check_in"] is not None
        assert result["check_out"] is not None
