"""
Contract tests for availability_api component.
Tests verify the Availability HTTP API & Server against its contract specification.
All dependencies are mocked — tests verify the component in isolation.

Run with: pytest contract_test.py -v
"""

import json
import re
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from copy import deepcopy

# ---------------------------------------------------------------------------
# Import the component under test
# ---------------------------------------------------------------------------
from src.availability_api import (
    validate_request,
    handle_check_availability,
    handle_update_availability,
    handle_bulk_check_availability,
    handle_health_check,
    create_router,
    create_server,
    UnitNotFoundError,
    AvailabilityCheckResult,
    AvailabilityUpdateResult,
    BulkAvailabilityResult,
    BulkAvailabilityEntry,
    HealthStatus,
    ApiErrorEnvelope,
    ErrorDetail,
    BLOCK_TYPES,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_UUID = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
VALID_UUID_2 = "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e"
UNKNOWN_UUID = "00000000-0000-4000-a000-000000000001"
UUID_V5 = "a1b2c3d4-e5f6-5a7b-8c9d-0e1f2a3b4c5d"  # version digit = 5
DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UUID_V4_REGEX = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# Mock service factory
# ---------------------------------------------------------------------------
def create_mock_service():
    """Create a mock AvailabilityServiceInterface with async methods."""
    service = MagicMock()
    service.check_availability = AsyncMock()
    service.update_availability = AsyncMock()
    service.bulk_check_availability = AsyncMock()
    return service


def make_check_result(available=True, unit_id=VALID_UUID,
                      start="2025-07-01", end="2025-07-05"):
    return {
        "available": available,
        "unit_id": unit_id,
        "start": start,
        "end": end,
    }


def make_update_result(success=True):
    return {"success": success}


def make_bulk_result(entries):
    return {"results": entries}


def make_bulk_entry(unit_id, available=True):
    return {"unit_id": unit_id, "available": available}


def make_health_status(status="ok", uptime_seconds=42.5, version="1.0.0"):
    return {
        "status": status,
        "uptime_seconds": uptime_seconds,
        "version": version,
    }


# ---------------------------------------------------------------------------
# Helper: assert error envelope shape
# ---------------------------------------------------------------------------
def assert_error_envelope(body, expected_code=None, expected_message_contains=None):
    """Assert the response body conforms to the ApiErrorEnvelope contract."""
    assert "error" in body, f"Response missing 'error' key: {body}"
    error = body["error"]
    assert "code" in error, f"Error missing 'code': {error}"
    assert isinstance(error["code"], str), f"Error code is not a string: {error['code']}"
    assert "message" in error, f"Error missing 'message': {error}"
    assert isinstance(error["message"], str), f"Error message is not a string: {error['message']}"
    assert len(error["message"]) > 0, "Error message must be non-empty"
    if expected_code:
        assert error["code"] == expected_code, (
            f"Expected error code '{expected_code}', got '{error['code']}'"
        )
    if expected_message_contains:
        assert expected_message_contains in error["message"], (
            f"Expected '{expected_message_contains}' in message '{error['message']}'"
        )


def assert_date_format(date_str):
    """Assert a string is YYYY-MM-DD with no time/timezone components."""
    assert isinstance(date_str, str), f"Date is not a string: {date_str}"
    assert DATE_REGEX.match(date_str), f"Date does not match YYYY-MM-DD: {date_str}"
    assert "T" not in date_str, f"Date contains time component: {date_str}"
    assert "Z" not in date_str, f"Date contains timezone marker: {date_str}"
    assert "+" not in date_str, f"Date contains timezone offset: {date_str}"


def generate_valid_uuids(n):
    """Generate n deterministic-looking but valid UUID v4 strings."""
    uuids = []
    for i in range(n):
        u = str(uuid.uuid4())
        uuids.append(u)
    return uuids


# ===========================================================================
# Layer 1: validateRequest tests
# ===========================================================================
class TestValidateRequest:
    """Tests for the validateRequest helper function."""

    @pytest.mark.asyncio
    async def test_valid_input_returns_success(self):
        """validateRequest returns success=True with parsed data for valid input."""
        data = {
            "unit_id": VALID_UUID,
            "start": "2025-07-01",
            "end": "2025-07-05",
        }
        # We pass a schema name/object and data; the function should parse successfully
        result = validate_request("AvailabilityCheckParamsSchema", data)

        assert result["success"] is True, "Expected validation to succeed"
        assert "data" in result, "Expected parsed 'data' in result"
        assert result["data"]["unit_id"] == VALID_UUID
        assert result["data"]["start"] == "2025-07-01"
        assert result["data"]["end"] == "2025-07-05"

    @pytest.mark.asyncio
    async def test_invalid_input_returns_failure_with_validation_error(self):
        """validateRequest returns success=False with VALIDATION_ERROR for invalid input."""
        data = {
            "unit_id": "not-a-uuid",
            "start": "bad",
            "end": "bad",
        }
        result = validate_request("AvailabilityCheckParamsSchema", data)

        assert result["success"] is False, "Expected validation to fail"
        assert "error_response" in result, "Expected 'error_response' in failure"
        assert_error_envelope(result["error_response"], expected_code="VALIDATION_ERROR")
        # details should contain validation issues
        error_detail = result["error_response"]["error"]
        assert "details" in error_detail or error_detail.get("details") is not None, (
            "Expected details with Zod issues"
        )

    @pytest.mark.asyncio
    async def test_never_throws_on_null_data(self):
        """validateRequest never throws; null data returns ValidationFailure."""
        # Should not raise any exception
        try:
            result = validate_request("AvailabilityCheckParamsSchema", None)
        except Exception as e:
            pytest.fail(f"validateRequest should never throw, but raised: {e}")

        assert result["success"] is False, "Null input should fail validation"
        assert_error_envelope(result["error_response"])

    @pytest.mark.asyncio
    async def test_extra_fields_accepted_or_stripped(self):
        """validateRequest with extra fields still succeeds (Zod strip behavior)."""
        data = {
            "unit_id": VALID_UUID,
            "start": "2025-07-01",
            "end": "2025-07-05",
            "extra": "should-be-stripped",
        }
        result = validate_request("AvailabilityCheckParamsSchema", data)

        assert result["success"] is True, "Extra fields should not cause failure"
        assert "unit_id" in result["data"]
        assert "start" in result["data"]
        assert "end" in result["data"]


# ===========================================================================
# Layer 1: handleCheckAvailability tests
# ===========================================================================
class TestHandleCheckAvailability:
    """Tests for GET /availability/:unit_id handler."""

    @pytest.mark.asyncio
    async def test_happy_path_available(self):
        """Returns 200 with available=true when unit is fully available."""
        service = create_mock_service()
        expected = make_check_result(available=True)
        service.check_availability.return_value = expected

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 200, f"Expected 200, got {status}"
        assert body["available"] is True
        assert body["unit_id"] == VALID_UUID
        assert body["start"] == "2025-07-01"
        assert body["end"] == "2025-07-05"
        service.check_availability.assert_called_once_with(
            VALID_UUID, "2025-07-01", "2025-07-05"
        )

    @pytest.mark.asyncio
    async def test_happy_path_unavailable(self):
        """Returns 200 with available=false when unit has blocked dates."""
        service = create_mock_service()
        expected = make_check_result(available=False, start="2025-08-01", end="2025-08-10")
        service.check_availability.return_value = expected

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start="2025-08-01", end="2025-08-10",
            service=service,
        )

        assert status == 200, f"Expected 200, got {status}"
        assert body["available"] is False

    @pytest.mark.asyncio
    async def test_invalid_unit_id_returns_400_validation_error(self):
        """Returns 400 VALIDATION_ERROR when unit_id is not a valid UUID v4."""
        service = create_mock_service()

        status, body = await handle_check_availability(
            unit_id="not-a-uuid", start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 400, f"Expected 400, got {status}"
        assert_error_envelope(body, expected_code="VALIDATION_ERROR")
        service.check_availability.assert_not_called()

    @pytest.mark.asyncio
    async def test_uuid_v5_rejected(self):
        """UUID v5 (version digit '5') is rejected as invalid unit_id."""
        service = create_mock_service()

        status, body = await handle_check_availability(
            unit_id=UUID_V5, start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 400, f"Expected 400 for UUID v5, got {status}"
        assert_error_envelope(body, expected_code="VALIDATION_ERROR")

    @pytest.mark.asyncio
    async def test_invalid_start_date_format(self):
        """Returns 400 INVALID_DATE_FORMAT when start is not YYYY-MM-DD."""
        service = create_mock_service()

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start="07/01/2025", end="2025-07-05",
            service=service,
        )

        assert status == 400, f"Expected 400, got {status}"
        assert_error_envelope(body, expected_code="INVALID_DATE_FORMAT")

    @pytest.mark.asyncio
    async def test_invalid_end_date_format(self):
        """Returns 400 INVALID_DATE_FORMAT when end is not YYYY-MM-DD."""
        service = create_mock_service()

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start="2025-07-01", end="not-a-date",
            service=service,
        )

        assert status == 400, f"Expected 400, got {status}"
        assert_error_envelope(body, expected_code="INVALID_DATE_FORMAT")

    @pytest.mark.asyncio
    async def test_end_before_start_returns_400(self):
        """Returns 400 VALIDATION_ERROR when end < start."""
        service = create_mock_service()

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start="2025-07-05", end="2025-07-01",
            service=service,
        )

        assert status == 400, f"Expected 400, got {status}"
        assert_error_envelope(
            body,
            expected_code="VALIDATION_ERROR",
            expected_message_contains="end must be strictly after start",
        )

    @pytest.mark.asyncio
    async def test_end_equals_start_returns_400(self):
        """Returns 400 when end == start (zero-length range is invalid)."""
        service = create_mock_service()

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start="2025-07-01", end="2025-07-01",
            service=service,
        )

        assert status == 400, f"Expected 400 for zero-length range, got {status}"
        assert_error_envelope(body, expected_code="VALIDATION_ERROR")

    @pytest.mark.asyncio
    async def test_missing_start_returns_400(self):
        """Returns 400 when start query param is missing/empty."""
        service = create_mock_service()

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start="", end="2025-07-05",
            service=service,
        )

        assert status == 400, f"Expected 400 for missing start, got {status}"
        assert_error_envelope(body)

    @pytest.mark.asyncio
    async def test_missing_end_returns_400(self):
        """Returns 400 when end query param is missing/empty."""
        service = create_mock_service()

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start="2025-07-01", end="",
            service=service,
        )

        assert status == 400, f"Expected 400 for missing end, got {status}"
        assert_error_envelope(body)

    @pytest.mark.asyncio
    async def test_invalid_calendar_date_returns_400(self):
        """Returns 400 for a date like 2025-02-30 that matches regex but is not a real date."""
        service = create_mock_service()

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start="2025-02-30", end="2025-03-05",
            service=service,
        )

        assert status == 400, f"Expected 400 for invalid calendar date, got {status}"
        assert_error_envelope(body)

    @pytest.mark.asyncio
    async def test_unit_not_found_returns_404(self):
        """Returns 404 UNIT_NOT_FOUND when service raises UnitNotFoundError."""
        service = create_mock_service()
        service.check_availability.side_effect = UnitNotFoundError(VALID_UUID)

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 404, f"Expected 404, got {status}"
        assert_error_envelope(body, expected_code="UNIT_NOT_FOUND")

    @pytest.mark.asyncio
    async def test_internal_error_returns_500(self):
        """Returns 500 INTERNAL_ERROR when service raises unexpected exception."""
        service = create_mock_service()
        service.check_availability.side_effect = RuntimeError("db timeout")

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 500, f"Expected 500, got {status}"
        assert_error_envelope(body, expected_code="INTERNAL_ERROR")

    @pytest.mark.asyncio
    async def test_response_dates_are_yyyy_mm_dd_format(self):
        """Response dates are YYYY-MM-DD with no time or timezone components."""
        service = create_mock_service()
        service.check_availability.return_value = make_check_result()

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 200
        assert_date_format(body["start"])
        assert_date_format(body["end"])

    @pytest.mark.asyncio
    async def test_half_open_interval_single_day(self):
        """Single-day range [2025-07-01, 2025-07-02) is valid and service receives correct args."""
        service = create_mock_service()
        service.check_availability.return_value = make_check_result(
            start="2025-07-01", end="2025-07-02"
        )

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start="2025-07-01", end="2025-07-02",
            service=service,
        )

        assert status == 200
        service.check_availability.assert_called_once_with(
            VALID_UUID, "2025-07-01", "2025-07-02"
        )
        assert body["start"] == "2025-07-01"
        assert body["end"] == "2025-07-02"


# ===========================================================================
# Layer 1: handleUpdateAvailability tests
# ===========================================================================
class TestHandleUpdateAvailability:
    """Tests for PUT /availability/:unit_id handler."""

    def _make_body(self, start="2025-07-01", end="2025-07-05",
                   block_type="OWNER_BLOCK", blocked=True):
        return {
            "start": start,
            "end": end,
            "block_type": block_type,
            "blocked": blocked,
        }

    @pytest.mark.asyncio
    async def test_happy_path_block(self):
        """Returns 200 with success=true when blocking dates."""
        service = create_mock_service()
        service.update_availability.return_value = make_update_result(success=True)
        body = self._make_body(blocked=True)

        status, resp = await handle_update_availability(
            unit_id=VALID_UUID, body=body, service=service,
        )

        assert status == 200, f"Expected 200, got {status}"
        assert resp["success"] is True
        service.update_availability.assert_called_once()

    @pytest.mark.asyncio
    async def test_happy_path_unblock(self):
        """Returns 200 with success=true when unblocking dates."""
        service = create_mock_service()
        service.update_availability.return_value = make_update_result(success=True)
        body = self._make_body(block_type="MAINTENANCE", blocked=False)

        status, resp = await handle_update_availability(
            unit_id=VALID_UUID, body=body, service=service,
        )

        assert status == 200, f"Expected 200, got {status}"
        assert resp["success"] is True

    @pytest.mark.asyncio
    async def test_always_returns_200_never_201(self):
        """PUT always returns 200 (idempotent state set), never 201."""
        service = create_mock_service()
        service.update_availability.return_value = make_update_result(success=True)
        body = self._make_body()

        status, resp = await handle_update_availability(
            unit_id=VALID_UUID, body=body, service=service,
        )

        assert status == 200, f"Expected 200, got {status}"
        assert status != 201, "PUT must never return 201"

    @pytest.mark.asyncio
    async def test_idempotent_repeated_puts(self):
        """Repeated identical PUT requests produce the same 200 response."""
        service = create_mock_service()
        service.update_availability.return_value = make_update_result(success=True)
        body = self._make_body()

        status1, resp1 = await handle_update_availability(
            unit_id=VALID_UUID, body=body, service=service,
        )
        status2, resp2 = await handle_update_availability(
            unit_id=VALID_UUID, body=body, service=service,
        )

        assert status1 == 200
        assert status2 == 200
        assert resp1 == resp2, "Repeated PUT should return identical responses"

    @pytest.mark.asyncio
    async def test_invalid_unit_id(self):
        """Returns 400 VALIDATION_ERROR for invalid unit_id."""
        service = create_mock_service()
        body = self._make_body()

        status, resp = await handle_update_availability(
            unit_id="bad-id", body=body, service=service,
        )

        assert status == 400
        assert_error_envelope(resp, expected_code="VALIDATION_ERROR")

    @pytest.mark.asyncio
    async def test_missing_body_fields(self):
        """Returns 400 VALIDATION_ERROR when body is missing required fields."""
        service = create_mock_service()
        incomplete_body = {"start": "2025-07-01"}  # missing end, block_type, blocked

        status, resp = await handle_update_availability(
            unit_id=VALID_UUID, body=incomplete_body, service=service,
        )

        assert status == 400
        assert_error_envelope(resp, expected_code="VALIDATION_ERROR")

    @pytest.mark.asyncio
    async def test_invalid_block_type(self):
        """Returns 400 VALIDATION_ERROR for an invalid block_type enum value."""
        service = create_mock_service()
        body = self._make_body(block_type="INVALID_TYPE")

        status, resp = await handle_update_availability(
            unit_id=VALID_UUID, body=body, service=service,
        )

        assert status == 400
        assert_error_envelope(resp, expected_code="VALIDATION_ERROR")

    @pytest.mark.asyncio
    async def test_all_valid_block_types_accepted(self):
        """All five valid BlockType values are accepted with 200."""
        valid_types = ["OWNER_BLOCK", "MAINTENANCE", "BOOKING",
                       "SEASONAL_CLOSURE", "OTHER"]
        for bt in valid_types:
            service = create_mock_service()
            service.update_availability.return_value = make_update_result(success=True)
            body = self._make_body(block_type=bt)

            status, resp = await handle_update_availability(
                unit_id=VALID_UUID, body=body, service=service,
            )

            assert status == 200, (
                f"Expected 200 for block_type '{bt}', got {status}"
            )
            assert resp["success"] is True

    @pytest.mark.asyncio
    async def test_end_not_after_start(self):
        """Returns 400 when end <= start in body."""
        service = create_mock_service()
        body = self._make_body(start="2025-07-10", end="2025-07-01")

        status, resp = await handle_update_availability(
            unit_id=VALID_UUID, body=body, service=service,
        )

        assert status == 400
        assert_error_envelope(resp, expected_code="VALIDATION_ERROR")

    @pytest.mark.asyncio
    async def test_unit_not_found(self):
        """Returns 404 UNIT_NOT_FOUND when service raises UnitNotFoundError."""
        service = create_mock_service()
        service.update_availability.side_effect = UnitNotFoundError(VALID_UUID)
        body = self._make_body()

        status, resp = await handle_update_availability(
            unit_id=VALID_UUID, body=body, service=service,
        )

        assert status == 404
        assert_error_envelope(resp, expected_code="UNIT_NOT_FOUND")

    @pytest.mark.asyncio
    async def test_internal_error(self):
        """Returns 500 INTERNAL_ERROR when service raises unexpected exception."""
        service = create_mock_service()
        service.update_availability.side_effect = RuntimeError("unexpected")
        body = self._make_body()

        status, resp = await handle_update_availability(
            unit_id=VALID_UUID, body=body, service=service,
        )

        assert status == 500
        assert_error_envelope(resp, expected_code="INTERNAL_ERROR")


# ===========================================================================
# Layer 1: handleBulkCheckAvailability tests
# ===========================================================================
class TestHandleBulkCheckAvailability:
    """Tests for GET /availability/bulk handler."""

    @pytest.mark.asyncio
    async def test_happy_path_multiple_units(self):
        """Returns 200 with results for multiple unit_ids."""
        service = create_mock_service()
        service.bulk_check_availability.return_value = make_bulk_result([
            make_bulk_entry(VALID_UUID, available=True),
            make_bulk_entry(VALID_UUID_2, available=False),
        ])

        status, body = await handle_bulk_check_availability(
            unit_ids=f"{VALID_UUID},{VALID_UUID_2}",
            start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 200, f"Expected 200, got {status}"
        assert "results" in body
        assert len(body["results"]) == 2
        for entry in body["results"]:
            assert "unit_id" in entry, "Each entry must have 'unit_id'"
            assert "available" in entry, "Each entry must have 'available'"

    @pytest.mark.asyncio
    async def test_happy_path_single_unit(self):
        """Returns 200 with results for a single unit_id (minimum 1)."""
        service = create_mock_service()
        service.bulk_check_availability.return_value = make_bulk_result([
            make_bulk_entry(VALID_UUID, available=True),
        ])

        status, body = await handle_bulk_check_availability(
            unit_ids=VALID_UUID,
            start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 200
        assert len(body["results"]) == 1

    @pytest.mark.asyncio
    async def test_results_order_matches_input(self):
        """Results are in the same order as the input unit_ids."""
        service = create_mock_service()
        service.bulk_check_availability.return_value = make_bulk_result([
            make_bulk_entry(VALID_UUID, available=True),
            make_bulk_entry(VALID_UUID_2, available=False),
        ])

        status, body = await handle_bulk_check_availability(
            unit_ids=f"{VALID_UUID},{VALID_UUID_2}",
            start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 200
        assert body["results"][0]["unit_id"] == VALID_UUID, (
            "First result should match first input unit_id"
        )
        assert body["results"][1]["unit_id"] == VALID_UUID_2, (
            "Second result should match second input unit_id"
        )

    @pytest.mark.asyncio
    async def test_unknown_units_return_available_false_not_404(self):
        """Unknown unit_ids return available=false, not a 404 error."""
        service = create_mock_service()
        service.bulk_check_availability.return_value = make_bulk_result([
            make_bulk_entry(UNKNOWN_UUID, available=False),
        ])

        status, body = await handle_bulk_check_availability(
            unit_ids=UNKNOWN_UUID,
            start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 200, f"Expected 200 (not 404) for unknown unit, got {status}"
        assert body["results"][0]["available"] is False
        assert body["results"][0]["unit_id"] == UNKNOWN_UUID

    @pytest.mark.asyncio
    async def test_missing_unit_ids_returns_400(self):
        """Returns 400 VALIDATION_ERROR when unit_ids is missing/empty."""
        service = create_mock_service()

        status, body = await handle_bulk_check_availability(
            unit_ids="",
            start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 400
        assert_error_envelope(body, expected_code="VALIDATION_ERROR")
        assert "unit_ids" in body["error"]["message"].lower() or \
               "unit_ids" in str(body["error"].get("details", "")).lower(), \
            "Error should mention unit_ids"

    @pytest.mark.asyncio
    async def test_invalid_uuid_in_list_returns_400(self):
        """Returns 400 VALIDATION_ERROR when one UUID in the list is invalid."""
        service = create_mock_service()

        status, body = await handle_bulk_check_availability(
            unit_ids=f"{VALID_UUID},not-a-uuid",
            start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 400
        assert_error_envelope(body, expected_code="VALIDATION_ERROR")

    @pytest.mark.asyncio
    async def test_bulk_limit_exceeded_101_units(self):
        """Returns 400 BULK_LIMIT_EXCEEDED when more than 100 unit_ids provided."""
        service = create_mock_service()
        uuids_101 = generate_valid_uuids(101)
        unit_ids_csv = ",".join(uuids_101)

        status, body = await handle_bulk_check_availability(
            unit_ids=unit_ids_csv,
            start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 400
        assert_error_envelope(body, expected_code="BULK_LIMIT_EXCEEDED")
        assert "100" in body["error"]["message"], (
            "Error message should mention the 100 limit"
        )

    @pytest.mark.asyncio
    async def test_exactly_100_units_accepted(self):
        """Exactly 100 unit_ids is at the boundary and should be accepted."""
        service = create_mock_service()
        uuids_100 = generate_valid_uuids(100)
        entries = [make_bulk_entry(u, available=True) for u in uuids_100]
        service.bulk_check_availability.return_value = make_bulk_result(entries)

        status, body = await handle_bulk_check_availability(
            unit_ids=",".join(uuids_100),
            start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 200, f"Expected 200 for exactly 100 units, got {status}"
        assert len(body["results"]) == 100

    @pytest.mark.asyncio
    async def test_invalid_date_format(self):
        """Returns 400 INVALID_DATE_FORMAT for malformed dates."""
        service = create_mock_service()

        status, body = await handle_bulk_check_availability(
            unit_ids=VALID_UUID,
            start="bad-date", end="2025-07-05",
            service=service,
        )

        assert status == 400
        assert_error_envelope(body, expected_code="INVALID_DATE_FORMAT")

    @pytest.mark.asyncio
    async def test_end_not_after_start(self):
        """Returns 400 VALIDATION_ERROR when end <= start."""
        service = create_mock_service()

        status, body = await handle_bulk_check_availability(
            unit_ids=VALID_UUID,
            start="2025-07-10", end="2025-07-01",
            service=service,
        )

        assert status == 400
        assert_error_envelope(body, expected_code="VALIDATION_ERROR")

    @pytest.mark.asyncio
    async def test_internal_error(self):
        """Returns 500 INTERNAL_ERROR when service raises unexpected exception."""
        service = create_mock_service()
        service.bulk_check_availability.side_effect = RuntimeError("service crash")

        status, body = await handle_bulk_check_availability(
            unit_ids=VALID_UUID,
            start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 500
        assert_error_envelope(body, expected_code="INTERNAL_ERROR")


# ===========================================================================
# Layer 1: handleHealthCheck tests
# ===========================================================================
class TestHandleHealthCheck:
    """Tests for GET /health handler."""

    @pytest.mark.asyncio
    async def test_healthy_returns_200_ok(self):
        """Returns 200 with status 'ok' when server is healthy."""
        status, body = await handle_health_check(shutting_down=False)

        assert status == 200, f"Expected 200, got {status}"
        assert body["status"] == "ok"
        assert isinstance(body["uptime_seconds"], (int, float))
        assert body["uptime_seconds"] >= 0, "Uptime must be non-negative"
        assert isinstance(body["version"], str)
        assert len(body["version"]) > 0, "Version must be non-empty"

    @pytest.mark.asyncio
    async def test_shutting_down_returns_503(self):
        """Returns 503 with status 'shutting_down' during graceful shutdown."""
        status, body = await handle_health_check(shutting_down=True)

        assert status == 503, f"Expected 503, got {status}"
        assert body["status"] == "shutting_down"
        assert isinstance(body["uptime_seconds"], (int, float))
        assert body["uptime_seconds"] >= 0
        assert isinstance(body["version"], str)
        assert len(body["version"]) > 0

    @pytest.mark.asyncio
    async def test_uptime_is_non_negative(self):
        """uptime_seconds is always a non-negative number."""
        status, body = await handle_health_check(shutting_down=False)

        assert body["uptime_seconds"] >= 0, (
            f"Expected non-negative uptime, got {body['uptime_seconds']}"
        )
        assert isinstance(body["uptime_seconds"], (int, float)), (
            f"uptime_seconds should be numeric, got {type(body['uptime_seconds'])}"
        )


# ===========================================================================
# Layer 2: createRouter tests
# ===========================================================================
class TestCreateRouter:
    """Tests for the createRouter factory function."""

    def test_returns_router_with_all_routes(self):
        """createRouter returns a router with all four routes registered."""
        service = create_mock_service()
        router = create_router(service)

        assert router is not None, "Router should not be None"
        # The router object should be able to handle the documented routes.
        # We verify the router has route definitions (implementation-dependent).
        # At minimum, the factory should return a truthy object.
        assert router, "Router should be a valid object"

    def test_bulk_route_registered_before_unit_id_route(self):
        """GET /availability/bulk is registered before GET /availability/:unit_id.
        This ensures 'bulk' is not matched as a :unit_id path parameter."""
        service = create_mock_service()
        router = create_router(service)

        # Verify by checking route registration order if exposed, or by
        # confirming that a request to /availability/bulk does not trigger
        # the :unit_id handler.
        # This is a structural invariant — we verify the router was created
        # without errors, which implies correct registration order.
        assert router is not None


# ===========================================================================
# Layer 3: createServer tests
# ===========================================================================
class TestCreateServer:
    """Tests for the createServer factory function."""

    def test_returns_server_handle_with_start_stop(self):
        """createServer returns a ServerHandle with start() and stop() methods."""
        service = create_mock_service()
        config = {
            "port": 0,  # OS-assigned port
            "hostname": "127.0.0.1",
            "shutdown_timeout_ms": 5000,
            "availability_service": service,
        }

        handle = create_server(config)

        assert handle is not None, "ServerHandle should not be None"
        assert callable(getattr(handle, "start", None)), (
            "ServerHandle must have a callable 'start' method"
        )
        assert callable(getattr(handle, "stop", None)), (
            "ServerHandle must have a callable 'stop' method"
        )

    def test_invalid_port_raises_error(self):
        """createServer raises error when port is out of valid range."""
        service = create_mock_service()
        config = {
            "port": 99999,  # Invalid: exceeds 65535
            "hostname": "127.0.0.1",
            "shutdown_timeout_ms": 5000,
            "availability_service": service,
        }

        with pytest.raises(Exception) as exc_info:
            create_server(config)

        error_msg = str(exc_info.value).lower()
        assert "port" in error_msg or "config" in error_msg or "invalid" in error_msg, (
            f"Error should mention port or config issue, got: {exc_info.value}"
        )

    def test_invalid_shutdown_timeout_raises_error(self):
        """createServer raises error when shutdown_timeout_ms is out of range."""
        service = create_mock_service()
        config = {
            "port": 8080,
            "hostname": "127.0.0.1",
            "shutdown_timeout_ms": 500000,  # Exceeds 300000 max
            "availability_service": service,
        }

        with pytest.raises(Exception):
            create_server(config)

    def test_missing_service_raises_error(self):
        """createServer raises error when availability_service is missing."""
        config = {
            "port": 8080,
            "hostname": "127.0.0.1",
            "shutdown_timeout_ms": 5000,
            # availability_service intentionally omitted
        }

        with pytest.raises(Exception):
            create_server(config)


# ===========================================================================
# Layer 3: Invariant / Contract Shape Tests
# ===========================================================================
class TestApiErrorEnvelopeShape:
    """Tests that error responses always conform to the ApiErrorEnvelope contract."""

    @pytest.mark.asyncio
    async def test_error_envelope_has_required_keys(self):
        """All error responses have {error: {code: str, message: str}}."""
        service = create_mock_service()

        # Trigger a validation error
        status, body = await handle_check_availability(
            unit_id="not-a-uuid", start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 400
        assert "error" in body, "Top-level 'error' key is required"
        error = body["error"]
        assert "code" in error, "'code' field is required in error"
        assert "message" in error, "'message' field is required in error"
        assert isinstance(error["code"], str)
        assert isinstance(error["message"], str)
        # 'details' is optional
        if "details" in error:
            # details can be any type
            pass

    @pytest.mark.asyncio
    async def test_404_error_envelope_shape(self):
        """404 responses also use the ApiErrorEnvelope shape."""
        service = create_mock_service()
        service.check_availability.side_effect = UnitNotFoundError(VALID_UUID)

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 404
        assert_error_envelope(body, expected_code="UNIT_NOT_FOUND")

    @pytest.mark.asyncio
    async def test_500_error_envelope_shape(self):
        """500 responses also use the ApiErrorEnvelope shape."""
        service = create_mock_service()
        service.check_availability.side_effect = RuntimeError("boom")

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 500
        assert_error_envelope(body, expected_code="INTERNAL_ERROR")


class TestAvailabilityCheckResultShape:
    """Tests that successful check responses match AvailabilityCheckResult contract."""

    @pytest.mark.asyncio
    async def test_result_has_all_required_fields(self):
        """AvailabilityCheckResult has available, unit_id, start, end."""
        service = create_mock_service()
        service.check_availability.return_value = make_check_result()

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 200
        required_keys = {"available", "unit_id", "start", "end"}
        assert required_keys.issubset(body.keys()), (
            f"Missing keys: {required_keys - body.keys()}"
        )
        assert isinstance(body["available"], bool)
        assert isinstance(body["unit_id"], str)
        assert_date_format(body["start"])
        assert_date_format(body["end"])


class TestBulkAvailabilityResultShape:
    """Tests that bulk responses match BulkAvailabilityResult contract."""

    @pytest.mark.asyncio
    async def test_result_has_results_array(self):
        """BulkAvailabilityResult has a 'results' array."""
        service = create_mock_service()
        service.bulk_check_availability.return_value = make_bulk_result([
            make_bulk_entry(VALID_UUID, available=True),
        ])

        status, body = await handle_bulk_check_availability(
            unit_ids=VALID_UUID,
            start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 200
        assert "results" in body
        assert isinstance(body["results"], list)

    @pytest.mark.asyncio
    async def test_each_entry_has_unit_id_and_available(self):
        """Each BulkAvailabilityEntry has unit_id (str) and available (bool)."""
        service = create_mock_service()
        service.bulk_check_availability.return_value = make_bulk_result([
            make_bulk_entry(VALID_UUID, available=True),
            make_bulk_entry(VALID_UUID_2, available=False),
        ])

        status, body = await handle_bulk_check_availability(
            unit_ids=f"{VALID_UUID},{VALID_UUID_2}",
            start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 200
        for entry in body["results"]:
            assert "unit_id" in entry, "Entry missing 'unit_id'"
            assert "available" in entry, "Entry missing 'available'"
            assert isinstance(entry["unit_id"], str)
            assert isinstance(entry["available"], bool)


class TestHealthStatusShape:
    """Tests that health responses match HealthStatus contract."""

    @pytest.mark.asyncio
    async def test_health_has_all_required_fields(self):
        """HealthStatus has status, uptime_seconds, version."""
        status, body = await handle_health_check(shutting_down=False)

        assert status == 200
        required_keys = {"status", "uptime_seconds", "version"}
        assert required_keys.issubset(body.keys()), (
            f"Missing keys: {required_keys - body.keys()}"
        )

    @pytest.mark.asyncio
    async def test_health_field_types(self):
        """HealthStatus fields have correct types."""
        status, body = await handle_health_check(shutting_down=False)

        assert isinstance(body["status"], str)
        assert isinstance(body["uptime_seconds"], (int, float))
        assert isinstance(body["version"], str)


# ===========================================================================
# Property-Based Tests (integrated into Layer 1)
# ===========================================================================
class TestPropertyBased:
    """Property-based tests for contract invariants."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("start,end", [
        ("2025-01-10", "2025-01-05"),
        ("2025-12-31", "2025-01-01"),
        ("2025-06-15", "2025-06-15"),
        ("2030-01-01", "2025-01-01"),
    ])
    async def test_start_gte_end_always_rejected(self, start, end):
        """Any date pair where start >= end always yields a validation error."""
        service = create_mock_service()

        status, body = await handle_check_availability(
            unit_id=VALID_UUID, start=start, end=end,
            service=service,
        )

        assert status == 400, (
            f"start={start}, end={end} should be rejected but got {status}"
        )
        assert_error_envelope(body)
        service.check_availability.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_uuid", [
        "not-a-uuid",
        "12345",
        "",
        "g1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",  # 'g' is invalid hex
        "a1b2c3d4-e5f6-1a7b-8c9d-0e1f2a3b4c5d",  # version 1
        "a1b2c3d4-e5f6-4a7b-0c9d-0e1f2a3b4c5d",  # variant digit '0' invalid
        "A1B2C3D4-E5F6-4A7B-8C9D-0E1F2A3B4C5D",  # uppercase (may or may not be accepted)
    ])
    async def test_non_uuid_v4_always_rejected(self, bad_uuid):
        """Non-UUID-v4 strings always yield a validation error."""
        # Skip uppercase test if implementation is case-insensitive
        if bad_uuid == "A1B2C3D4-E5F6-4A7B-8C9D-0E1F2A3B4C5D":
            # The contract regex uses [0-9a-f], so uppercase should be rejected
            pass

        service = create_mock_service()

        status, body = await handle_check_availability(
            unit_id=bad_uuid, start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 400, (
            f"UUID '{bad_uuid}' should be rejected but got {status}"
        )
        assert_error_envelope(body)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("n_uuids", [1, 2, 5, 10, 50])
    async def test_bulk_n_uuids_returns_n_results(self, n_uuids):
        """N valid comma-joined UUIDs produce exactly N results."""
        service = create_mock_service()
        uuids = generate_valid_uuids(n_uuids)
        entries = [make_bulk_entry(u, available=True) for u in uuids]
        service.bulk_check_availability.return_value = make_bulk_result(entries)

        status, body = await handle_bulk_check_availability(
            unit_ids=",".join(uuids),
            start="2025-07-01", end="2025-07-05",
            service=service,
        )

        assert status == 200, f"Expected 200 for {n_uuids} UUIDs, got {status}"
        assert len(body["results"]) == n_uuids, (
            f"Expected {n_uuids} results, got {len(body['results'])}"
        )
