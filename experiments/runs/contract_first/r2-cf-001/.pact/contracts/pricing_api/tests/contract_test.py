"""
Contract tests for pricing_api (v1)
Pricing API Endpoints — Oak HTTP router for rental unit pricing.

Tests verify the API's behavior at boundaries (inputs/outputs) with
the pricing engine dependency fully mocked.

Run: pytest contract_test.py -v
"""
import json
import re
from datetime import date, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
from copy import deepcopy

import pytest

# ---------------------------------------------------------------------------
# Import the component under test
# ---------------------------------------------------------------------------
from src.pricing_api import (
    get_quote,
    get_rates,
    update_rate_configuration,
    upsert_fee,
    PricingEngine,
    UnitNotFoundError,
    RatesNotConfiguredError,
    CurrencyMismatchError,
    InvalidNightRangeError,
    OverlappingSeasonsError,
    NegativeRateError,
    InvalidPercentageConfigError,
    MaxFeesExceededError,
)


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

def _money(amount_cents: int, currency: str = "USD") -> dict:
    return {"amount_cents": amount_cents, "currency": currency}


def _nightly_line_item(dt: str, amount_cents: int, source: str = "base") -> dict:
    return {
        "type": "nightly_rate",
        "label": f"Nightly rate ({dt})",
        "amount": _money(amount_cents),
        "metadata": {"date": dt, "rate_source": source},
    }


def _fee_line_item(fee_type: str, label: str, amount_cents: int, is_pct: bool = False, pct_bp: int = 0) -> dict:
    return {
        "type": fee_type if fee_type in ("cleaning_fee", "pet_fee", "extra_guest_fee", "custom_fee") else "custom_fee",
        "label": label,
        "amount": _money(amount_cents),
        "metadata": {
            "fee_type": fee_type,
            "fee_label": label,
            "is_percentage": is_pct,
            "percentage_basis_points": pct_bp,
        },
    }


def _tax_line_item(name: str, amount_cents: int, bp: int = 1200) -> dict:
    return {
        "type": "tax",
        "label": name,
        "amount": _money(amount_cents),
        "metadata": {"tax_name": name, "rate_basis_points": bp},
    }


def _discount_line_item(min_nights: int, bp: int, amount_cents: int) -> dict:
    return {
        "type": "length_of_stay_discount",
        "label": f"{bp/100:.1f}% length-of-stay discount",
        "amount": _money(amount_cents),
        "metadata": {"min_nights": min_nights, "discount_basis_points": bp},
    }


def _make_quote_response(
    unit_id: str,
    start: str,
    end: str,
    guests: int,
    nightly_cents: int = 15000,
    cleaning_fee_cents: int = 0,
    tax_bp: int = 0,
    discount_bp: int = 0,
    currency: str = "USD",
):
    """Build a realistic QuoteResponse dict for mocking."""
    d_start = date.fromisoformat(start)
    d_end = date.fromisoformat(end)
    num_nights = (d_end - d_start).days

    line_items = []
    subtotal = 0
    for i in range(num_nights):
        dt = (d_start + timedelta(days=i)).isoformat()
        line_items.append(_nightly_line_item(dt, nightly_cents))
        subtotal += nightly_cents

    fees_total = 0
    if cleaning_fee_cents:
        line_items.append(_fee_line_item("cleaning_fee", "Cleaning Fee", cleaning_fee_cents))
        fees_total += cleaning_fee_cents

    taxes_total = 0
    if tax_bp:
        tax_amount = int(subtotal * tax_bp / 10000)
        line_items.append(_tax_line_item("Occupancy Tax", tax_amount, tax_bp))
        taxes_total += tax_amount

    discounts_total = 0
    if discount_bp:
        disc_amount = -int(subtotal * discount_bp / 10000)
        line_items.append(_discount_line_item(num_nights, discount_bp, disc_amount))
        discounts_total += disc_amount

    total = subtotal + fees_total + taxes_total + discounts_total

    return {
        "unit_id": unit_id,
        "start": start,
        "end": end,
        "guests": guests,
        "num_nights": num_nights,
        "line_items": line_items,
        "subtotal": _money(subtotal, currency),
        "fees_total": _money(fees_total, currency),
        "taxes_total": _money(taxes_total, currency),
        "discounts_total": _money(discounts_total, currency),
        "total": _money(total, currency),
    }


def _make_rates_response(unit_id, start, end, nightly_cents=15000, currency="USD"):
    d_start = date.fromisoformat(start)
    d_end = date.fromisoformat(end)
    rates = []
    for i in range((d_end - d_start).days):
        dt = (d_start + timedelta(days=i)).isoformat()
        rates.append({"date": dt, "amount": _money(nightly_cents, currency), "source": "base"})
    return {"unit_id": unit_id, "start": start, "end": end, "rates": rates}


def _make_rate_configuration(
    unit_id="unit-001",
    base_cents=15000,
    currency="USD",
    seasonal_rules=None,
    date_specific_rates=None,
    length_of_stay_discounts=None,
    min_nights=1,
    max_nights=730,
):
    return {
        "unit_id": unit_id,
        "base_nightly_rate": _money(base_cents, currency),
        "seasonal_rules": seasonal_rules or [],
        "date_specific_rates": date_specific_rates or [],
        "length_of_stay_discounts": length_of_stay_discounts or [],
        "currency": currency,
        "min_nights": min_nights,
        "max_nights": max_nights,
    }


def _make_fees_response(unit_id, fees):
    return {"unit_id": unit_id, "fees": fees}


def _fee_def(fee_type, label, amount_cents, currency="USD", is_pct=False, pct_bp=0,
             per_night=False, per_guest_above=0, enabled=True):
    return {
        "fee_type": fee_type,
        "label": label,
        "amount": _money(amount_cents, currency),
        "is_percentage": is_pct,
        "percentage_basis_points": pct_bp,
        "applies_per_night": per_night,
        "applies_per_guest_above": per_guest_above,
        "enabled": enabled,
    }


META_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def _response_meta(currency="USD"):
    return {"currency": currency, "generated_at": "2025-08-01T00:00:00.000Z"}


@pytest.fixture
def mock_engine():
    """Create a fresh mock PricingEngine for each test."""
    engine = MagicMock(spec=PricingEngine)
    return engine


# ===================================================================
# GET /pricing/:unit_id/quote
# ===================================================================


class TestGetQuoteHappyPath:
    """Happy-path tests for get_quote."""

    def test_single_night_stay(self, mock_engine):
        """Single-night stay with 1 guest returns correct envelope."""
        quote_data = _make_quote_response("unit-001", "2025-08-01", "2025-08-02", 1)
        mock_engine.compute_quote.return_value = quote_data

        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-02", guests=1)

        assert result["data"]["num_nights"] == 1, "Expected 1 night for single-night stay"
        nightly_items = [li for li in result["data"]["line_items"] if li["type"] == "nightly_rate"]
        assert len(nightly_items) == 1, "Expected exactly 1 nightly_rate line item"
        assert "meta" in result, "Response must contain meta field"
        assert "data" in result, "Response must contain data field"
        mock_engine.compute_quote.assert_called_once()

    def test_multi_night_with_guests_fees_taxes_discount(self, mock_engine):
        """7-night stay with 4 guests including fees, taxes, and discount."""
        quote_data = _make_quote_response(
            "unit-002", "2025-07-01", "2025-07-08", 4,
            nightly_cents=20000,
            cleaning_fee_cents=10000,
            tax_bp=1200,
            discount_bp=500,
        )
        mock_engine.compute_quote.return_value = quote_data

        result = get_quote(mock_engine, unit_id="unit-002", start="2025-07-01", end="2025-07-08", guests=4)

        data = result["data"]
        assert data["num_nights"] == 7, "Expected 7 nights"
        nightly_items = [li for li in data["line_items"] if li["type"] == "nightly_rate"]
        assert len(nightly_items) == 7, "Expected 7 nightly_rate line items"
        assert data["discounts_total"]["amount_cents"] <= 0, "Discounts total must be non-positive"
        assert data["subtotal"]["amount_cents"] >= 0, "Subtotal must be non-negative"

        # Arithmetic invariant
        expected_total = (
            data["subtotal"]["amount_cents"]
            + data["fees_total"]["amount_cents"]
            + data["taxes_total"]["amount_cents"]
            + data["discounts_total"]["amount_cents"]
        )
        assert data["total"]["amount_cents"] == expected_total, (
            f"Total {data['total']['amount_cents']} != "
            f"subtotal({data['subtotal']['amount_cents']}) + fees({data['fees_total']['amount_cents']}) "
            f"+ taxes({data['taxes_total']['amount_cents']}) + discounts({data['discounts_total']['amount_cents']})"
        )


class TestGetQuoteErrors:
    """Error-case tests for get_quote covering all 7 error codes."""

    def test_invalid_date_format_start(self, mock_engine):
        """start param in wrong format -> 400 INVALID_DATE_FORMAT."""
        result = get_quote(mock_engine, unit_id="unit-001", start="08-01-2025", end="2025-08-02", guests=1)

        assert result["error"]["code"] == "INVALID_DATE_FORMAT"
        assert result.get("_http_status", 400) == 400 or "error" in result
        mock_engine.compute_quote.assert_not_called()

    def test_invalid_date_format_end(self, mock_engine):
        """end param in wrong format -> 400 INVALID_DATE_FORMAT."""
        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-01", end="Aug 2 2025", guests=1)

        assert result["error"]["code"] == "INVALID_DATE_FORMAT"
        mock_engine.compute_quote.assert_not_called()

    def test_invalid_date_range_start_after_end(self, mock_engine):
        """start > end -> 400 INVALID_DATE_RANGE."""
        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-05", end="2025-08-01", guests=1)

        assert result["error"]["code"] == "INVALID_DATE_RANGE"
        mock_engine.compute_quote.assert_not_called()

    def test_invalid_date_range_same_day(self, mock_engine):
        """start == end -> 400 INVALID_DATE_RANGE."""
        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-01", guests=1)

        assert result["error"]["code"] == "INVALID_DATE_RANGE"
        mock_engine.compute_quote.assert_not_called()

    def test_invalid_date_range_exceeds_730_days(self, mock_engine):
        """Range > 730 days -> 400 INVALID_DATE_RANGE."""
        result = get_quote(mock_engine, unit_id="unit-001", start="2025-01-01", end="2027-02-01", guests=1)

        assert result["error"]["code"] == "INVALID_DATE_RANGE"
        mock_engine.compute_quote.assert_not_called()

    def test_invalid_guests_zero(self, mock_engine):
        """guests=0 -> 400 INVALID_GUESTS."""
        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-02", guests=0)

        assert result["error"]["code"] == "INVALID_GUESTS"
        mock_engine.compute_quote.assert_not_called()

    def test_invalid_guests_over_100(self, mock_engine):
        """guests=101 -> 400 INVALID_GUESTS."""
        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-02", guests=101)

        assert result["error"]["code"] == "INVALID_GUESTS"
        mock_engine.compute_quote.assert_not_called()

    def test_missing_query_params_no_start(self, mock_engine):
        """Missing start param -> 400 MISSING_QUERY_PARAMS."""
        result = get_quote(mock_engine, unit_id="unit-001", start=None, end="2025-08-02", guests=1)

        assert result["error"]["code"] == "MISSING_QUERY_PARAMS"
        mock_engine.compute_quote.assert_not_called()

    def test_unit_not_found(self, mock_engine):
        """Engine raises UnitNotFoundError -> 404 UNIT_NOT_FOUND."""
        mock_engine.compute_quote.side_effect = UnitNotFoundError("nonexistent-unit")

        result = get_quote(mock_engine, unit_id="nonexistent-unit", start="2025-08-01", end="2025-08-02", guests=1)

        assert result["error"]["code"] == "UNIT_NOT_FOUND"

    def test_rates_not_configured(self, mock_engine):
        """Engine raises RatesNotConfiguredError -> 422 RATES_NOT_CONFIGURED."""
        mock_engine.compute_quote.side_effect = RatesNotConfiguredError("unit-001")

        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-02", guests=1)

        assert result["error"]["code"] == "RATES_NOT_CONFIGURED"

    def test_engine_error(self, mock_engine):
        """Engine raises RuntimeError -> 500 INTERNAL_ERROR."""
        mock_engine.compute_quote.side_effect = RuntimeError("unexpected failure")

        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-02", guests=1)

        assert result["error"]["code"] == "INTERNAL_ERROR"


class TestGetQuoteEdgeCases:
    """Edge-case tests for get_quote."""

    def test_leap_year_feb28_to_mar1(self, mock_engine):
        """Stay from Feb 28 to Mar 1 in 2028 (leap year) yields 2 nights."""
        quote_data = _make_quote_response("unit-001", "2028-02-28", "2028-03-01", 1)
        mock_engine.compute_quote.return_value = quote_data

        result = get_quote(mock_engine, unit_id="unit-001", start="2028-02-28", end="2028-03-01", guests=1)

        assert result["data"]["num_nights"] == 2, "2028 is leap year: Feb 28 & Feb 29 = 2 nights"

    def test_year_boundary_dec31_to_jan2(self, mock_engine):
        """Stay spanning year boundary Dec 31 to Jan 2."""
        quote_data = _make_quote_response("unit-001", "2025-12-31", "2026-01-02", 2)
        mock_engine.compute_quote.return_value = quote_data

        result = get_quote(mock_engine, unit_id="unit-001", start="2025-12-31", end="2026-01-02", guests=2)

        assert result["data"]["num_nights"] == 2

    def test_max_range_730_days_accepted(self, mock_engine):
        """Exactly 730-day range should be accepted."""
        start = "2025-01-01"
        end_date = date(2025, 1, 1) + timedelta(days=730)
        end = end_date.isoformat()
        quote_data = _make_quote_response("unit-001", start, end, 1)
        mock_engine.compute_quote.return_value = quote_data

        result = get_quote(mock_engine, unit_id="unit-001", start=start, end=end, guests=1)

        assert result["data"]["num_nights"] == 730

    def test_empty_unit_id_returns_error(self, mock_engine):
        """Empty unit_id should be rejected with a 400 error."""
        result = get_quote(mock_engine, unit_id="", start="2025-08-01", end="2025-08-02", guests=1)

        assert "error" in result, "Empty unit_id should produce an error response"


# ===================================================================
# GET /pricing/:unit_id/rates
# ===================================================================


class TestGetRatesHappyPath:
    """Happy-path tests for get_rates."""

    def test_three_night_range(self, mock_engine):
        """3-night range returns 3 rate entries sorted by date."""
        rates_data = _make_rates_response("unit-001", "2025-08-01", "2025-08-04")
        mock_engine.get_nightly_rates.return_value = rates_data

        result = get_rates(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-04")

        data = result["data"]
        assert len(data["rates"]) == 3, "Expected 3 rate entries"
        dates = [r["date"] for r in data["rates"]]
        assert dates == sorted(dates), "Rates must be sorted by date ascending"
        currencies = {r["amount"]["currency"] for r in data["rates"]}
        assert len(currencies) == 1, "All rates must share the same currency"
        assert result["meta"]["currency"] in currencies

    def test_single_night(self, mock_engine):
        """Single night range returns exactly 1 rate."""
        rates_data = _make_rates_response("unit-001", "2025-08-01", "2025-08-02")
        mock_engine.get_nightly_rates.return_value = rates_data

        result = get_rates(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-02")

        assert len(result["data"]["rates"]) == 1
        assert result["data"]["rates"][0]["date"] == "2025-08-01"


class TestGetRatesErrors:
    """Error-case tests for get_rates covering all 6 error codes."""

    def test_invalid_date_format(self, mock_engine):
        result = get_rates(mock_engine, unit_id="unit-001", start="2025-08-01", end="not-a-date")
        assert result["error"]["code"] == "INVALID_DATE_FORMAT"
        mock_engine.get_nightly_rates.assert_not_called()

    def test_invalid_date_range(self, mock_engine):
        result = get_rates(mock_engine, unit_id="unit-001", start="2025-08-05", end="2025-08-01")
        assert result["error"]["code"] == "INVALID_DATE_RANGE"
        mock_engine.get_nightly_rates.assert_not_called()

    def test_missing_query_params(self, mock_engine):
        result = get_rates(mock_engine, unit_id="unit-001", start="2025-08-01", end=None)
        assert result["error"]["code"] == "MISSING_QUERY_PARAMS"
        mock_engine.get_nightly_rates.assert_not_called()

    def test_unit_not_found(self, mock_engine):
        mock_engine.get_nightly_rates.side_effect = UnitNotFoundError("nonexistent")
        result = get_rates(mock_engine, unit_id="nonexistent", start="2025-08-01", end="2025-08-02")
        assert result["error"]["code"] == "UNIT_NOT_FOUND"

    def test_rates_not_configured(self, mock_engine):
        mock_engine.get_nightly_rates.side_effect = RatesNotConfiguredError("unit-001")
        result = get_rates(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-02")
        assert result["error"]["code"] == "RATES_NOT_CONFIGURED"

    def test_engine_error(self, mock_engine):
        mock_engine.get_nightly_rates.side_effect = RuntimeError("boom")
        result = get_rates(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-02")
        assert result["error"]["code"] == "INTERNAL_ERROR"


# ===================================================================
# PUT /pricing/:unit_id/rates
# ===================================================================


class TestUpdateRateConfigHappyPath:
    """Happy-path tests for update_rate_configuration."""

    def test_update_base_tier(self, mock_engine):
        """Update base tier returns merged config."""
        merged = _make_rate_configuration(base_cents=15000)
        mock_engine.update_rate_configuration.return_value = merged

        body = {"override_tier": "base", "base_nightly_rate": _money(15000)}
        result = update_rate_configuration(mock_engine, unit_id="unit-001", body=body)

        assert result["data"]["unit_id"] == "unit-001"
        assert result["data"]["base_nightly_rate"]["amount_cents"] == 15000
        assert "meta" in result

    def test_update_seasonal_tier(self, mock_engine):
        """Update seasonal tier with a seasonal rule."""
        seasonal = [{
            "name": "Peak Summer",
            "start_month_day": "06-01",
            "end_month_day": "08-31",
            "nightly_rate": _money(25000),
        }]
        merged = _make_rate_configuration(seasonal_rules=seasonal)
        mock_engine.update_rate_configuration.return_value = merged

        body = {"override_tier": "seasonal", "seasonal_rules": seasonal}
        result = update_rate_configuration(mock_engine, unit_id="unit-001", body=body)

        assert len(result["data"]["seasonal_rules"]) == 1
        assert result["data"]["seasonal_rules"][0]["name"] == "Peak Summer"

    def test_min_nights_equals_max_nights(self, mock_engine):
        """min_nights == max_nights is valid (fixed-length stay)."""
        merged = _make_rate_configuration(min_nights=7, max_nights=7)
        mock_engine.update_rate_configuration.return_value = merged

        body = {"override_tier": "base", "min_nights": 7, "max_nights": 7}
        result = update_rate_configuration(mock_engine, unit_id="unit-001", body=body)

        assert result["data"]["min_nights"] == 7
        assert result["data"]["max_nights"] == 7


class TestUpdateRateConfigErrors:
    """Error-case tests for update_rate_configuration covering all error codes."""

    def test_validation_error_missing_override_tier(self, mock_engine):
        body = {"base_nightly_rate": _money(15000)}
        result = update_rate_configuration(mock_engine, unit_id="unit-001", body=body)
        assert result["error"]["code"] == "VALIDATION_ERROR"
        mock_engine.update_rate_configuration.assert_not_called()

    def test_invalid_json(self, mock_engine):
        result = update_rate_configuration(mock_engine, unit_id="unit-001", body="not valid json {{{")
        assert result["error"]["code"] == "INVALID_JSON"
        mock_engine.update_rate_configuration.assert_not_called()

    def test_unit_not_found(self, mock_engine):
        mock_engine.update_rate_configuration.side_effect = UnitNotFoundError("nonexistent")
        body = {"override_tier": "seasonal", "seasonal_rules": []}
        result = update_rate_configuration(mock_engine, unit_id="nonexistent", body=body)
        assert result["error"]["code"] == "UNIT_NOT_FOUND"

    def test_currency_mismatch(self, mock_engine):
        mock_engine.update_rate_configuration.side_effect = CurrencyMismatchError("EUR vs USD")
        body = {"override_tier": "base", "currency": "EUR", "base_nightly_rate": _money(15000, "EUR")}
        result = update_rate_configuration(mock_engine, unit_id="unit-001", body=body)
        assert result["error"]["code"] == "CURRENCY_MISMATCH"

    def test_invalid_night_range(self, mock_engine):
        mock_engine.update_rate_configuration.side_effect = InvalidNightRangeError("min > max")
        body = {"override_tier": "base", "min_nights": 10, "max_nights": 3}
        result = update_rate_configuration(mock_engine, unit_id="unit-001", body=body)
        assert result["error"]["code"] == "INVALID_NIGHT_RANGE"

    def test_overlapping_seasons(self, mock_engine):
        mock_engine.update_rate_configuration.side_effect = OverlappingSeasonsError("overlap")
        body = {
            "override_tier": "seasonal",
            "seasonal_rules": [
                {"name": "A", "start_month_day": "06-01", "end_month_day": "08-31", "nightly_rate": _money(20000)},
                {"name": "B", "start_month_day": "07-15", "end_month_day": "09-30", "nightly_rate": _money(25000)},
            ],
        }
        result = update_rate_configuration(mock_engine, unit_id="unit-001", body=body)
        assert result["error"]["code"] == "OVERLAPPING_SEASONS"

    def test_negative_rate(self, mock_engine):
        mock_engine.update_rate_configuration.side_effect = NegativeRateError("negative")
        body = {"override_tier": "base", "base_nightly_rate": _money(-100)}
        result = update_rate_configuration(mock_engine, unit_id="unit-001", body=body)
        assert result["error"]["code"] == "NEGATIVE_RATE"

    def test_engine_error(self, mock_engine):
        mock_engine.update_rate_configuration.side_effect = RuntimeError("boom")
        body = {"override_tier": "base", "base_nightly_rate": _money(15000)}
        result = update_rate_configuration(mock_engine, unit_id="unit-001", body=body)
        assert result["error"]["code"] == "INTERNAL_ERROR"


# ===================================================================
# POST /pricing/:unit_id/fees
# ===================================================================


class TestUpsertFeeHappyPath:
    """Happy-path tests for upsert_fee."""

    def test_flat_cleaning_fee(self, mock_engine):
        """Upsert a flat cleaning fee."""
        fees = [_fee_def("cleaning_fee", "Cleaning Fee", 7500)]
        mock_engine.upsert_fee.return_value = _make_fees_response("unit-001", fees)

        body = {"fee_type": "cleaning_fee", "label": "Cleaning Fee", "amount": _money(7500)}
        result = upsert_fee(mock_engine, unit_id="unit-001", body=body)

        data = result["data"]
        assert data["unit_id"] == "unit-001"
        cleaning = [f for f in data["fees"] if f["fee_type"] == "cleaning_fee"]
        assert len(cleaning) == 1, "Should contain exactly one cleaning_fee"
        assert cleaning[0]["amount"]["amount_cents"] == 7500

    def test_percentage_service_fee(self, mock_engine):
        """Upsert a percentage-based service fee."""
        fees = [_fee_def("service_fee", "Service Fee", 0, is_pct=True, pct_bp=1000)]
        mock_engine.upsert_fee.return_value = _make_fees_response("unit-001", fees)

        body = {
            "fee_type": "service_fee", "label": "Service Fee",
            "amount": _money(0), "is_percentage": True, "percentage_basis_points": 1000,
        }
        result = upsert_fee(mock_engine, unit_id="unit-001", body=body)

        svc = [f for f in result["data"]["fees"] if f["fee_type"] == "service_fee"]
        assert svc[0]["is_percentage"] is True
        assert svc[0]["percentage_basis_points"] == 1000

    def test_per_night_per_guest_fee(self, mock_engine):
        """Upsert an extra guest fee that applies per-night above guest threshold."""
        fees = [_fee_def("extra_guest_fee", "Extra Guest Fee", 2500, per_night=True, per_guest_above=2)]
        mock_engine.upsert_fee.return_value = _make_fees_response("unit-001", fees)

        body = {
            "fee_type": "extra_guest_fee", "label": "Extra Guest Fee",
            "amount": _money(2500), "applies_per_night": True, "applies_per_guest_above": 2,
        }
        result = upsert_fee(mock_engine, unit_id="unit-001", body=body)

        egf = [f for f in result["data"]["fees"] if f["fee_type"] == "extra_guest_fee"]
        assert egf[0]["applies_per_night"] is True
        assert egf[0]["applies_per_guest_above"] == 2


class TestUpsertFeeEdgeCases:
    """Edge-case tests for upsert_fee."""

    def test_upsert_update_existing_fee(self, mock_engine):
        """Upserting same fee_type updates rather than duplicating."""
        fees = [_fee_def("cleaning_fee", "Updated Cleaning Fee", 10000)]
        mock_engine.upsert_fee.return_value = _make_fees_response("unit-001", fees)

        body = {"fee_type": "cleaning_fee", "label": "Updated Cleaning Fee", "amount": _money(10000)}
        result = upsert_fee(mock_engine, unit_id="unit-001", body=body)

        cleaning_fees = [f for f in result["data"]["fees"] if f["fee_type"] == "cleaning_fee"]
        assert len(cleaning_fees) == 1, "Upsert must not create duplicate entries"
        assert cleaning_fees[0]["label"] == "Updated Cleaning Fee"
        assert cleaning_fees[0]["amount"]["amount_cents"] == 10000

    def test_disabled_fee(self, mock_engine):
        """Upserting a disabled fee stores it with enabled=false."""
        fees = [_fee_def("pet_fee", "Pet Fee", 5000, enabled=False)]
        mock_engine.upsert_fee.return_value = _make_fees_response("unit-001", fees)

        body = {"fee_type": "pet_fee", "label": "Pet Fee", "amount": _money(5000), "enabled": False}
        result = upsert_fee(mock_engine, unit_id="unit-001", body=body)

        pet = [f for f in result["data"]["fees"] if f["fee_type"] == "pet_fee"]
        assert pet[0]["enabled"] is False


class TestUpsertFeeErrors:
    """Error-case tests for upsert_fee covering all 7 error codes."""

    def test_validation_error_bad_fee_type(self, mock_engine):
        body = {"fee_type": "Invalid-Fee-Type!", "label": "Bad Fee", "amount": _money(100)}
        result = upsert_fee(mock_engine, unit_id="unit-001", body=body)
        assert result["error"]["code"] == "VALIDATION_ERROR"
        mock_engine.upsert_fee.assert_not_called()

    def test_invalid_json(self, mock_engine):
        result = upsert_fee(mock_engine, unit_id="unit-001", body="{broken json")
        assert result["error"]["code"] == "INVALID_JSON"
        mock_engine.upsert_fee.assert_not_called()

    def test_unit_not_found(self, mock_engine):
        mock_engine.upsert_fee.side_effect = UnitNotFoundError("nonexistent")
        body = {"fee_type": "cleaning_fee", "label": "Cleaning Fee", "amount": _money(7500)}
        result = upsert_fee(mock_engine, unit_id="nonexistent", body=body)
        assert result["error"]["code"] == "UNIT_NOT_FOUND"

    def test_currency_mismatch(self, mock_engine):
        mock_engine.upsert_fee.side_effect = CurrencyMismatchError("GBP vs USD")
        body = {"fee_type": "cleaning_fee", "label": "Cleaning Fee", "amount": _money(7500, "GBP")}
        result = upsert_fee(mock_engine, unit_id="unit-001", body=body)
        assert result["error"]["code"] == "CURRENCY_MISMATCH"

    def test_invalid_percentage_config(self, mock_engine):
        mock_engine.upsert_fee.side_effect = InvalidPercentageConfigError("bp is 0")
        body = {
            "fee_type": "service_fee", "label": "Service Fee", "amount": _money(0),
            "is_percentage": True, "percentage_basis_points": 0,
        }
        result = upsert_fee(mock_engine, unit_id="unit-001", body=body)
        assert result["error"]["code"] == "INVALID_PERCENTAGE_CONFIG"

    def test_max_fees_exceeded(self, mock_engine):
        mock_engine.upsert_fee.side_effect = MaxFeesExceededError("max 50")
        body = {"fee_type": "custom_fee_51", "label": "Fee 51", "amount": _money(100)}
        result = upsert_fee(mock_engine, unit_id="unit-001", body=body)
        assert result["error"]["code"] == "MAX_FEES_EXCEEDED"

    def test_engine_error(self, mock_engine):
        mock_engine.upsert_fee.side_effect = RuntimeError("boom")
        body = {"fee_type": "cleaning_fee", "label": "Cleaning Fee", "amount": _money(7500)}
        result = upsert_fee(mock_engine, unit_id="unit-001", body=body)
        assert result["error"]["code"] == "INTERNAL_ERROR"


# ===================================================================
# Contract Invariant Tests
# ===================================================================


class TestInvariants:
    """Cross-cutting invariant tests from the contract."""

    def test_all_monetary_values_are_integer_cents(self, mock_engine):
        """All amount_cents values in a quote must be integers, never floats."""
        quote_data = _make_quote_response(
            "unit-001", "2025-08-01", "2025-08-04", 2,
            nightly_cents=15000, cleaning_fee_cents=5000, tax_bp=1000, discount_bp=0,
        )
        mock_engine.compute_quote.return_value = quote_data

        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-04", guests=2)
        data = result["data"]

        # Check summary amounts
        for field in ("subtotal", "fees_total", "taxes_total", "discounts_total", "total"):
            val = data[field]["amount_cents"]
            assert isinstance(val, int), f"{field}.amount_cents must be int, got {type(val)}"

        # Check all line items
        for li in data["line_items"]:
            val = li["amount"]["amount_cents"]
            assert isinstance(val, int), f"Line item {li['label']} amount_cents must be int, got {type(val)}"

    def test_currency_consistency_in_quote(self, mock_engine):
        """All Money objects in a quote response share the same currency as meta.currency."""
        quote_data = _make_quote_response(
            "unit-001", "2025-08-01", "2025-08-04", 2,
            nightly_cents=15000, cleaning_fee_cents=5000, tax_bp=1200,
        )
        mock_engine.compute_quote.return_value = quote_data

        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-04", guests=2)
        data = result["data"]
        meta_currency = result["meta"]["currency"]

        # Check summary fields
        for field in ("subtotal", "fees_total", "taxes_total", "discounts_total", "total"):
            assert data[field]["currency"] == meta_currency, (
                f"{field}.currency '{data[field]['currency']}' != meta.currency '{meta_currency}'"
            )

        # Check all line items
        for li in data["line_items"]:
            assert li["amount"]["currency"] == meta_currency, (
                f"Line item '{li['label']}' currency mismatch"
            )

    def test_quote_total_arithmetic_invariant(self, mock_engine):
        """total = subtotal + fees_total + taxes_total + discounts_total."""
        quote_data = _make_quote_response(
            "unit-001", "2025-08-01", "2025-08-08", 3,
            nightly_cents=20000, cleaning_fee_cents=8000, tax_bp=1200, discount_bp=500,
        )
        mock_engine.compute_quote.return_value = quote_data

        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-08", guests=3)
        data = result["data"]

        computed = (
            data["subtotal"]["amount_cents"]
            + data["fees_total"]["amount_cents"]
            + data["taxes_total"]["amount_cents"]
            + data["discounts_total"]["amount_cents"]
        )
        assert data["total"]["amount_cents"] == computed, (
            f"Total {data['total']['amount_cents']} != computed sum {computed}"
        )

    def test_discounts_total_is_non_positive(self, mock_engine):
        """discounts_total.amount_cents must always be <= 0."""
        quote_data = _make_quote_response(
            "unit-001", "2025-08-01", "2025-08-08", 1,
            nightly_cents=15000, discount_bp=500,
        )
        mock_engine.compute_quote.return_value = quote_data

        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-08", guests=1)

        assert result["data"]["discounts_total"]["amount_cents"] <= 0, (
            "discounts_total must be non-positive"
        )

    def test_success_response_envelope_shape(self, mock_engine):
        """Success responses have data and meta with currency and generated_at."""
        quote_data = _make_quote_response("unit-001", "2025-08-01", "2025-08-02", 1)
        mock_engine.compute_quote.return_value = quote_data

        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-02", guests=1)

        assert "data" in result, "Success response must have 'data' key"
        assert "meta" in result, "Success response must have 'meta' key"
        meta = result["meta"]
        assert "currency" in meta, "meta must have 'currency' key"
        assert "generated_at" in meta, "meta must have 'generated_at' key"
        assert META_PATTERN.match(meta["generated_at"]), (
            f"meta.generated_at '{meta['generated_at']}' must be ISO 8601 UTC"
        )

    def test_error_response_envelope_shape(self, mock_engine):
        """Error responses have error with code and message."""
        # Trigger an error by providing empty unit_id
        result = get_quote(mock_engine, unit_id="", start="2025-08-01", end="2025-08-02", guests=1)

        assert "error" in result, "Error response must have 'error' key"
        err = result["error"]
        assert "code" in err, "error must have 'code' key"
        assert "message" in err, "error must have 'message' key"
        assert isinstance(err["message"], str) and len(err["message"]) > 0, (
            "error.message must be a non-empty string"
        )

    def test_date_range_semantics_inclusive_start_exclusive_end(self, mock_engine):
        """start is inclusive (first night), end is exclusive (checkout). num_nights = end - start."""
        quote_data = _make_quote_response("unit-001", "2025-08-01", "2025-08-04", 1)
        mock_engine.compute_quote.return_value = quote_data

        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-04", guests=1)
        data = result["data"]

        assert data["num_nights"] == 3, "Aug 1 to Aug 4 = 3 nights"
        assert data["start"] == "2025-08-01"
        assert data["end"] == "2025-08-04"
        nightly_items = [li for li in data["line_items"] if li["type"] == "nightly_rate"]
        assert len(nightly_items) == 3, "Must have exactly 3 nightly_rate line items"

    def test_upsert_fee_idempotent(self, mock_engine):
        """Calling upsert_fee twice with same data yields same result, no duplicates."""
        fees = [_fee_def("cleaning_fee", "Cleaning Fee", 7500)]
        mock_engine.upsert_fee.return_value = _make_fees_response("unit-001", fees)

        body = {"fee_type": "cleaning_fee", "label": "Cleaning Fee", "amount": _money(7500)}

        result1 = upsert_fee(mock_engine, unit_id="unit-001", body=body)
        result2 = upsert_fee(mock_engine, unit_id="unit-001", body=body)

        # Both calls should produce equivalent fee lists
        fees1 = result1["data"]["fees"]
        fees2 = result2["data"]["fees"]
        assert len(fees1) == len(fees2), "Idempotent upsert must not change fee count"

        cleaning1 = [f for f in fees1 if f["fee_type"] == "cleaning_fee"]
        cleaning2 = [f for f in fees2 if f["fee_type"] == "cleaning_fee"]
        assert len(cleaning1) == 1, "Must have exactly one cleaning_fee (no duplicates)"
        assert len(cleaning2) == 1, "Must have exactly one cleaning_fee (no duplicates)"
        assert cleaning1[0]["amount"]["amount_cents"] == cleaning2[0]["amount"]["amount_cents"]

    def test_rates_response_count_matches_date_range(self, mock_engine):
        """GET /rates returns exactly (end - start) entries."""
        rates_data = _make_rates_response("unit-001", "2025-08-01", "2025-08-06")
        mock_engine.get_nightly_rates.return_value = rates_data

        result = get_rates(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-06")

        expected_count = (date(2025, 8, 6) - date(2025, 8, 1)).days
        assert len(result["data"]["rates"]) == expected_count, (
            f"Expected {expected_count} rates, got {len(result['data']['rates'])}"
        )


# ===================================================================
# Schema / Envelope Shape Validation
# ===================================================================


class TestEnvelopeSchemas:
    """Verify exact key sets and types in response envelopes."""

    def test_quote_response_envelope_keys(self, mock_engine):
        quote_data = _make_quote_response("unit-001", "2025-08-01", "2025-08-02", 1)
        mock_engine.compute_quote.return_value = quote_data

        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-02", guests=1)

        assert set(result.keys()) >= {"data", "meta"}
        data = result["data"]
        required_keys = {"unit_id", "start", "end", "guests", "num_nights",
                         "line_items", "subtotal", "fees_total", "taxes_total",
                         "discounts_total", "total"}
        assert required_keys <= set(data.keys()), f"Missing keys in data: {required_keys - set(data.keys())}"

    def test_rates_response_envelope_keys(self, mock_engine):
        rates_data = _make_rates_response("unit-001", "2025-08-01", "2025-08-02")
        mock_engine.get_nightly_rates.return_value = rates_data

        result = get_rates(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-02")

        assert set(result.keys()) >= {"data", "meta"}
        data = result["data"]
        assert {"unit_id", "start", "end", "rates"} <= set(data.keys())

    def test_rate_config_response_envelope_keys(self, mock_engine):
        merged = _make_rate_configuration()
        mock_engine.update_rate_configuration.return_value = merged

        body = {"override_tier": "base", "base_nightly_rate": _money(15000)}
        result = update_rate_configuration(mock_engine, unit_id="unit-001", body=body)

        assert set(result.keys()) >= {"data", "meta"}
        data = result["data"]
        required = {"unit_id", "base_nightly_rate", "seasonal_rules", "date_specific_rates",
                     "length_of_stay_discounts", "currency", "min_nights", "max_nights"}
        assert required <= set(data.keys()), f"Missing keys: {required - set(data.keys())}"

    def test_fees_response_envelope_keys(self, mock_engine):
        fees = [_fee_def("cleaning_fee", "Cleaning Fee", 7500)]
        mock_engine.upsert_fee.return_value = _make_fees_response("unit-001", fees)

        body = {"fee_type": "cleaning_fee", "label": "Cleaning Fee", "amount": _money(7500)}
        result = upsert_fee(mock_engine, unit_id="unit-001", body=body)

        assert set(result.keys()) >= {"data", "meta"}
        assert {"unit_id", "fees"} <= set(result["data"].keys())

    def test_error_envelope_keys(self, mock_engine):
        """Error envelopes have {error: {code, message}}."""
        result = get_quote(mock_engine, unit_id="unit-001", start="bad", end="2025-08-02", guests=1)

        assert "error" in result
        assert {"code", "message"} <= set(result["error"].keys())

    def test_meta_generated_at_format(self, mock_engine):
        """meta.generated_at must be ISO 8601 UTC ending with Z."""
        quote_data = _make_quote_response("unit-001", "2025-08-01", "2025-08-02", 1)
        mock_engine.compute_quote.return_value = quote_data

        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-02", guests=1)

        ts = result["meta"]["generated_at"]
        assert META_PATTERN.match(ts), f"generated_at '{ts}' does not match ISO 8601 UTC pattern"

    def test_money_type_shape(self, mock_engine):
        """Money objects always have amount_cents (int) and currency (3-letter uppercase)."""
        quote_data = _make_quote_response("unit-001", "2025-08-01", "2025-08-02", 1)
        mock_engine.compute_quote.return_value = quote_data

        result = get_quote(mock_engine, unit_id="unit-001", start="2025-08-01", end="2025-08-02", guests=1)
        data = result["data"]

        currency_re = re.compile(r"^[A-Z]{3}$")
        for field in ("subtotal", "fees_total", "taxes_total", "discounts_total", "total"):
            m = data[field]
            assert "amount_cents" in m, f"{field} missing amount_cents"
            assert "currency" in m, f"{field} missing currency"
            assert isinstance(m["amount_cents"], int), f"{field}.amount_cents must be int"
            assert currency_re.match(m["currency"]), f"{field}.currency '{m['currency']}' invalid"
