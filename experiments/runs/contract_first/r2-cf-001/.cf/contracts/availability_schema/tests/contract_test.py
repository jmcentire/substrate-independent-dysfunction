"""
Contract tests for availability_schema component.
Tests the Availability Database Schema & Repository layer.

Verifies:
- getBlocksForUnit: querying blocks by unit and date range
- createBlock: inserting new availability blocks
- deleteBlock: removing blocks by ID
- getBlocksForUnits: bulk querying blocks for multiple units
- Error mapping from PostgreSQL error codes to domain errors
- Invariants: no overlaps, date ordering, type constraints, parameterized queries
"""

import re
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timezone

# Import the component under test
from src.availability_schema import (
    getBlocksForUnit,
    createBlock,
    deleteBlock,
    getBlocksForUnits,
    AvailabilityBlock,
    AvailabilityBlockRow,
    CreateBlockInput,
    DateRange,
    UnitBlocksMap,
    BlockOverlapError,
    BlockNotFoundError,
    InvalidDateRangeError,
    InvalidBlockTypeError,
    DatabaseError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_V4_REGEX = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

ISO_DATE_REGEX = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$"
)


def make_uuid() -> str:
    """Generate a lowercase UUID v4 string."""
    return str(uuid.uuid4())


def make_block_row(
    *,
    id: str = None,
    unit_id: str = None,
    start_date: str = "2024-06-01",
    end_date: str = "2024-06-10",
    block_type: str = "reserved",
    created_at: str = None,
) -> dict:
    """Create an AvailabilityBlockRow-like dict (snake_case, mimicking DB driver output)."""
    return {
        "id": id or make_uuid(),
        "unit_id": unit_id or make_uuid(),
        "start_date": start_date,
        "end_date": end_date,
        "block_type": block_type,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }


def make_db_client(*, query_result=None, query_side_effect=None):
    """Create a mock database client with configurable query behaviour."""
    client = MagicMock()
    client.query = AsyncMock()
    if query_side_effect is not None:
        client.query.side_effect = query_side_effect
    elif query_result is not None:
        client.query.return_value = query_result
    else:
        client.query.return_value = {"rows": [], "rowCount": 0}
    return client


def pg_error(code: str, message: str = "PG error"):
    """Create an exception mimicking a PostgreSQL driver error with a code attribute."""
    err = Exception(message)
    err.code = code  # type: ignore[attr-defined]
    err.pgCode = code  # type: ignore[attr-defined]
    return err


# ===================================================================
# 1. HAPPY PATH TESTS
# ===================================================================


class TestGetBlocksForUnitHappyPath:
    """Happy-path tests for getBlocksForUnit."""

    @pytest.mark.asyncio
    async def test_returns_overlapping_blocks_sorted_by_start_date(self):
        """hp_get_blocks_for_unit_found: returns matching blocks sorted ascending."""
        unit_id = make_uuid()
        row1 = make_block_row(unit_id=unit_id, start_date="2024-06-01", end_date="2024-06-05")
        row2 = make_block_row(unit_id=unit_id, start_date="2024-06-08", end_date="2024-06-12")
        db = make_db_client(query_result={"rows": [row1, row2], "rowCount": 2})

        result = await getBlocksForUnit(
            db,
            unit_id,
            DateRange(start="2024-06-01", end="2024-06-15"),
        )

        assert isinstance(result, list), "Result should be a list"
        assert len(result) == 2, f"Expected 2 blocks, got {len(result)}"
        for block in result:
            assert block.unitId == unit_id, "Each block unitId must match queried unit"
            assert block.id is not None, "Block id must be populated"
            assert block.createdAt is not None, "Block createdAt must be populated"
        assert result[0].startDate <= result[1].startDate, (
            "Blocks must be sorted by startDate ascending"
        )

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_overlap(self):
        """hp_get_blocks_for_unit_empty: empty list for non-overlapping range."""
        unit_id = make_uuid()
        db = make_db_client(query_result={"rows": [], "rowCount": 0})

        result = await getBlocksForUnit(
            db,
            unit_id,
            DateRange(start="2025-01-01", end="2025-01-31"),
        )

        assert result == [], "Should return empty list when no blocks overlap"
        db.query.assert_called_once()


class TestCreateBlockHappyPath:
    """Happy-path tests for createBlock."""

    @pytest.mark.asyncio
    async def test_creates_reserved_block_with_generated_id_and_timestamp(self):
        """hp_create_block_success: returns fully-populated block."""
        unit_id = make_uuid()
        generated_id = make_uuid()
        created_at = datetime.now(timezone.utc).isoformat()
        row = make_block_row(
            id=generated_id,
            unit_id=unit_id,
            start_date="2024-07-01",
            end_date="2024-07-10",
            block_type="reserved",
            created_at=created_at,
        )
        db = make_db_client(query_result={"rows": [row], "rowCount": 1})

        block_input = CreateBlockInput(
            unitId=unit_id,
            startDate="2024-07-01",
            endDate="2024-07-10",
            blockType="reserved",
        )
        result = await createBlock(db, block_input)

        assert UUID_V4_REGEX.match(result.id), f"id must be UUID v4, got {result.id}"
        assert result.unitId == unit_id
        assert result.startDate == "2024-07-01"
        assert result.endDate == "2024-07-10"
        assert result.blockType == "reserved"
        assert result.createdAt == created_at

    @pytest.mark.asyncio
    async def test_creates_maintenance_block(self):
        """hp_create_block_maintenance: blockType='maintenance' works."""
        unit_id = make_uuid()
        row = make_block_row(unit_id=unit_id, block_type="maintenance")
        db = make_db_client(query_result={"rows": [row], "rowCount": 1})

        result = await createBlock(
            db,
            CreateBlockInput(
                unitId=unit_id,
                startDate="2024-07-01",
                endDate="2024-07-10",
                blockType="maintenance",
            ),
        )
        assert result.blockType == "maintenance"

    @pytest.mark.asyncio
    async def test_creates_owner_hold_block(self):
        """hp_create_block_owner_hold: blockType='owner_hold' works."""
        unit_id = make_uuid()
        row = make_block_row(unit_id=unit_id, block_type="owner_hold")
        db = make_db_client(query_result={"rows": [row], "rowCount": 1})

        result = await createBlock(
            db,
            CreateBlockInput(
                unitId=unit_id,
                startDate="2024-07-01",
                endDate="2024-07-10",
                blockType="owner_hold",
            ),
        )
        assert result.blockType == "owner_hold"


class TestDeleteBlockHappyPath:
    """Happy-path tests for deleteBlock."""

    @pytest.mark.asyncio
    async def test_returns_true_when_block_deleted(self):
        """hp_delete_block_success: returns True when row is deleted."""
        block_id = make_uuid()
        db = make_db_client(query_result={"rows": [], "rowCount": 1})

        result = await deleteBlock(db, block_id)

        assert result is True, "deleteBlock should return True when block is deleted"
        db.query.assert_called_once()


class TestGetBlocksForUnitsHappyPath:
    """Happy-path tests for getBlocksForUnits."""

    @pytest.mark.asyncio
    async def test_returns_map_grouped_by_unit_id(self):
        """hp_get_blocks_for_units_multiple: groups blocks by unitId."""
        uid1 = make_uuid()
        uid2 = make_uuid()
        rows = [
            make_block_row(unit_id=uid1, start_date="2024-06-01", end_date="2024-06-05"),
            make_block_row(unit_id=uid1, start_date="2024-06-08", end_date="2024-06-10"),
            make_block_row(unit_id=uid2, start_date="2024-06-03", end_date="2024-06-07"),
        ]
        db = make_db_client(query_result={"rows": rows, "rowCount": 3})

        result = await getBlocksForUnits(
            db,
            [uid1, uid2],
            DateRange(start="2024-06-01", end="2024-06-15"),
        )

        assert uid1 in result.entries, f"Map must contain key {uid1}"
        assert uid2 in result.entries, f"Map must contain key {uid2}"
        assert len(result.entries[uid1]) == 2, f"Unit1 should have 2 blocks"
        assert len(result.entries[uid2]) == 1, f"Unit2 should have 1 block"
        for block in result.entries[uid1]:
            assert block.unitId == uid1
        for block in result.entries[uid2]:
            assert block.unitId == uid2
        # Verify sort order within each unit
        blocks_u1 = result.entries[uid1]
        assert blocks_u1[0].startDate <= blocks_u1[1].startDate

    @pytest.mark.asyncio
    async def test_units_with_no_blocks_have_empty_list(self):
        """hp_get_blocks_for_units_some_empty: empty list for unit without blocks."""
        uid1 = make_uuid()
        uid2 = make_uuid()
        rows = [
            make_block_row(unit_id=uid1, start_date="2024-06-01", end_date="2024-06-05"),
        ]
        db = make_db_client(query_result={"rows": rows, "rowCount": 1})

        result = await getBlocksForUnits(
            db,
            [uid1, uid2],
            DateRange(start="2024-06-01", end="2024-06-15"),
        )

        assert uid1 in result.entries
        assert uid2 in result.entries
        assert len(result.entries[uid1]) == 1
        assert result.entries[uid2] == [], "Unit with no blocks should have empty list"


# ===================================================================
# 2. EDGE CASE TESTS
# ===================================================================


class TestEdgeCases:
    """Edge case tests for boundary conditions and mapping."""

    @pytest.mark.asyncio
    async def test_single_day_range_returns_matching_block(self):
        """edge_get_blocks_single_day_range: start == end works."""
        unit_id = make_uuid()
        row = make_block_row(
            unit_id=unit_id, start_date="2024-06-10", end_date="2024-06-20"
        )
        db = make_db_client(query_result={"rows": [row], "rowCount": 1})

        result = await getBlocksForUnit(
            db,
            unit_id,
            DateRange(start="2024-06-15", end="2024-06-15"),
        )

        assert len(result) == 1, "Single-day range that overlaps a block should return it"

    @pytest.mark.asyncio
    async def test_create_single_day_block(self):
        """edge_create_block_single_day: startDate == endDate."""
        unit_id = make_uuid()
        row = make_block_row(
            unit_id=unit_id, start_date="2024-06-15", end_date="2024-06-15"
        )
        db = make_db_client(query_result={"rows": [row], "rowCount": 1})

        result = await createBlock(
            db,
            CreateBlockInput(
                unitId=unit_id,
                startDate="2024-06-15",
                endDate="2024-06-15",
                blockType="reserved",
            ),
        )

        assert result.startDate == result.endDate == "2024-06-15"

    @pytest.mark.asyncio
    async def test_row_to_domain_mapping_snake_to_camel(self):
        """edge_row_to_domain_mapping: snake_case DB row → camelCase domain model."""
        unit_id = make_uuid()
        block_id = make_uuid()
        created_at = "2024-06-01T12:00:00+00:00"
        row = {
            "id": block_id,
            "unit_id": unit_id,
            "start_date": "2024-07-01",
            "end_date": "2024-07-10",
            "block_type": "maintenance",
            "created_at": created_at,
        }
        db = make_db_client(query_result={"rows": [row], "rowCount": 1})

        result = await createBlock(
            db,
            CreateBlockInput(
                unitId=unit_id,
                startDate="2024-07-01",
                endDate="2024-07-10",
                blockType="maintenance",
            ),
        )

        assert result.id == block_id, "row.id → block.id"
        assert result.unitId == unit_id, "row.unit_id → block.unitId"
        assert result.startDate == "2024-07-01", "row.start_date → block.startDate"
        assert result.endDate == "2024-07-10", "row.end_date → block.endDate"
        assert result.blockType == "maintenance", "row.block_type → block.blockType"
        assert result.createdAt == created_at, "row.created_at → block.createdAt"

    @pytest.mark.asyncio
    async def test_boundary_overlap_inclusive_ranges(self):
        """edge_date_boundary_overlap: block endDate == range.start is included (inclusive)."""
        unit_id = make_uuid()
        row = make_block_row(
            unit_id=unit_id, start_date="2024-06-10", end_date="2024-06-15"
        )
        db = make_db_client(query_result={"rows": [row], "rowCount": 1})

        result = await getBlocksForUnit(
            db,
            unit_id,
            DateRange(start="2024-06-15", end="2024-06-20"),
        )

        assert len(result) == 1, (
            "Block whose endDate==range.start must be included (inclusive ranges)"
        )

    @pytest.mark.asyncio
    async def test_get_blocks_for_units_single_unit(self):
        """edge_get_blocks_for_units_single_unit: single unitId in list."""
        uid = make_uuid()
        row = make_block_row(unit_id=uid)
        db = make_db_client(query_result={"rows": [row], "rowCount": 1})

        result = await getBlocksForUnits(
            db, [uid], DateRange(start="2024-06-01", end="2024-06-30")
        )

        assert len(result.entries) == 1
        assert uid in result.entries

    @pytest.mark.asyncio
    async def test_get_blocks_for_units_duplicate_ids(self):
        """edge_duplicate_unit_ids_in_bulk: duplicates handled gracefully."""
        uid = make_uuid()
        row = make_block_row(unit_id=uid)
        db = make_db_client(query_result={"rows": [row], "rowCount": 1})

        result = await getBlocksForUnits(
            db, [uid, uid], DateRange(start="2024-06-01", end="2024-06-30")
        )

        assert uid in result.entries, "Duplicate unitId still appears as key"


# ===================================================================
# 3. ERROR CASE TESTS
# ===================================================================


class TestGetBlocksForUnitErrors:
    """Error case tests for getBlocksForUnit."""

    @pytest.mark.asyncio
    async def test_invalid_date_range_end_before_start(self):
        """err_get_blocks_invalid_date_range: end < start → InvalidDateRangeError."""
        db = make_db_client()

        with pytest.raises(InvalidDateRangeError) as exc_info:
            await getBlocksForUnit(
                db,
                make_uuid(),
                DateRange(start="2024-06-30", end="2024-06-01"),
            )

        err = exc_info.value
        assert err.start == "2024-06-30", "Error should contain the start date"
        assert err.end == "2024-06-01", "Error should contain the end date"
        assert "end" in err.message.lower() and "start" in err.message.lower()

    @pytest.mark.asyncio
    async def test_database_connection_failure(self):
        """err_get_blocks_db_connection_failure: DB down → DatabaseError."""
        db = make_db_client(query_side_effect=ConnectionError("Connection refused"))

        with pytest.raises(DatabaseError) as exc_info:
            await getBlocksForUnit(
                db,
                make_uuid(),
                DateRange(start="2024-06-01", end="2024-06-30"),
            )

        assert exc_info.value.message, "DatabaseError should have a message"

    @pytest.mark.asyncio
    async def test_unexpected_query_error(self):
        """err_get_blocks_db_query_error: unexpected PG error → DatabaseError."""
        db = make_db_client(query_side_effect=pg_error("42P01", "relation does not exist"))

        with pytest.raises(DatabaseError) as exc_info:
            await getBlocksForUnit(
                db,
                make_uuid(),
                DateRange(start="2024-06-01", end="2024-06-30"),
            )

        assert exc_info.value.message, "DatabaseError should have a message"


class TestCreateBlockErrors:
    """Error case tests for createBlock."""

    @pytest.mark.asyncio
    async def test_overlap_23P01_raises_block_overlap_error(self):
        """err_create_block_overlap_23P01: exclusion violation → BlockOverlapError."""
        unit_id = make_uuid()
        db = make_db_client(
            query_side_effect=pg_error("23P01", "conflicting key value violates exclusion constraint")
        )

        with pytest.raises(BlockOverlapError) as exc_info:
            await createBlock(
                db,
                CreateBlockInput(
                    unitId=unit_id,
                    startDate="2024-07-01",
                    endDate="2024-07-10",
                    blockType="reserved",
                ),
            )

        err = exc_info.value
        assert err.pgCode == "23P01", f"pgCode must be 23P01, got {err.pgCode}"
        assert err.unitId == unit_id
        assert err.startDate == "2024-07-01"
        assert err.endDate == "2024-07-10"
        assert "overlap" in err.message.lower()

    @pytest.mark.asyncio
    async def test_invalid_date_range_end_before_start(self):
        """err_create_block_invalid_date_range: endDate < startDate."""
        db = make_db_client()

        with pytest.raises(InvalidDateRangeError) as exc_info:
            await createBlock(
                db,
                CreateBlockInput(
                    unitId=make_uuid(),
                    startDate="2024-06-30",
                    endDate="2024-01-01",
                    blockType="reserved",
                ),
            )

        err = exc_info.value
        assert err.start == "2024-06-30"
        assert err.end == "2024-01-01"

    @pytest.mark.asyncio
    async def test_invalid_block_type(self):
        """err_create_block_invalid_type: unknown blockType → InvalidBlockTypeError."""
        db = make_db_client()

        with pytest.raises(InvalidBlockTypeError) as exc_info:
            await createBlock(
                db,
                CreateBlockInput(
                    unitId=make_uuid(),
                    startDate="2024-07-01",
                    endDate="2024-07-10",
                    blockType="vacation",
                ),
            )

        err = exc_info.value
        assert err.providedType == "vacation"
        assert "reserved" in err.allowedTypes
        assert "maintenance" in err.allowedTypes
        assert "owner_hold" in err.allowedTypes

    @pytest.mark.asyncio
    async def test_database_connection_failure(self):
        """err_create_block_db_connection_failure: DB down → DatabaseError."""
        db = make_db_client(query_side_effect=ConnectionError("Connection refused"))

        with pytest.raises(DatabaseError):
            await createBlock(
                db,
                CreateBlockInput(
                    unitId=make_uuid(),
                    startDate="2024-07-01",
                    endDate="2024-07-10",
                    blockType="reserved",
                ),
            )


class TestDeleteBlockErrors:
    """Error case tests for deleteBlock."""

    @pytest.mark.asyncio
    async def test_block_not_found(self):
        """err_delete_block_not_found: missing blockId → BlockNotFoundError."""
        block_id = make_uuid()
        db = make_db_client(query_result={"rows": [], "rowCount": 0})

        with pytest.raises(BlockNotFoundError) as exc_info:
            await deleteBlock(db, block_id)

        err = exc_info.value
        assert err.blockId == block_id, "Error must contain the missing blockId"
        assert "not found" in err.message.lower()

    @pytest.mark.asyncio
    async def test_database_connection_failure(self):
        """err_delete_block_db_connection_failure: DB down → DatabaseError."""
        db = make_db_client(query_side_effect=ConnectionError("Connection refused"))

        with pytest.raises(DatabaseError):
            await deleteBlock(db, make_uuid())


class TestGetBlocksForUnitsErrors:
    """Error case tests for getBlocksForUnits."""

    @pytest.mark.asyncio
    async def test_empty_unit_ids_raises_error(self):
        """err_get_blocks_for_units_empty_list: empty list → error."""
        db = make_db_client()

        with pytest.raises((InvalidDateRangeError, ValueError)) as exc_info:
            await getBlocksForUnits(
                db,
                [],
                DateRange(start="2024-06-01", end="2024-06-30"),
            )

        # The error message should mention unitIds
        err_msg = str(exc_info.value).lower()
        assert "unit" in err_msg or "empty" in err_msg or "at least" in err_msg

    @pytest.mark.asyncio
    async def test_invalid_date_range(self):
        """err_get_blocks_for_units_invalid_date_range: end < start."""
        db = make_db_client()

        with pytest.raises(InvalidDateRangeError):
            await getBlocksForUnits(
                db,
                [make_uuid()],
                DateRange(start="2024-12-31", end="2024-01-01"),
            )

    @pytest.mark.asyncio
    async def test_database_failure(self):
        """err_get_blocks_for_units_db_failure: DB down → DatabaseError."""
        db = make_db_client(query_side_effect=ConnectionError("Connection refused"))

        with pytest.raises(DatabaseError):
            await getBlocksForUnits(
                db,
                [make_uuid()],
                DateRange(start="2024-06-01", end="2024-06-30"),
            )


# ===================================================================
# 4. INVARIANT TESTS
# ===================================================================


class TestInvariants:
    """Tests for system invariants defined in the contract."""

    @pytest.mark.asyncio
    async def test_no_overlapping_blocks_same_unit(self):
        """inv_no_overlap_same_unit: second overlapping createBlock → BlockOverlapError."""
        unit_id = make_uuid()
        row = make_block_row(unit_id=unit_id, start_date="2024-07-01", end_date="2024-07-10")

        # First call succeeds, second raises PG 23P01
        db = make_db_client(
            query_side_effect=[
                {"rows": [row], "rowCount": 1},
                pg_error("23P01", "exclusion constraint violated"),
            ]
        )

        # First create should succeed
        result1 = await createBlock(
            db,
            CreateBlockInput(
                unitId=unit_id,
                startDate="2024-07-01",
                endDate="2024-07-10",
                blockType="reserved",
            ),
        )
        assert result1 is not None

        # Second create with overlapping range should fail
        with pytest.raises(BlockOverlapError) as exc_info:
            await createBlock(
                db,
                CreateBlockInput(
                    unitId=unit_id,
                    startDate="2024-07-05",
                    endDate="2024-07-15",
                    blockType="maintenance",
                ),
            )
        assert exc_info.value.pgCode == "23P01"

    @pytest.mark.asyncio
    async def test_end_date_gte_start_date_enforced_before_db(self):
        """inv_end_gte_start: endDate < startDate rejected without DB call."""
        db = make_db_client()

        with pytest.raises(InvalidDateRangeError):
            await createBlock(
                db,
                CreateBlockInput(
                    unitId=make_uuid(),
                    startDate="2024-07-10",
                    endDate="2024-07-01",
                    blockType="reserved",
                ),
            )

        db.query.assert_not_called(), "No DB call should be made for invalid date range"

    @pytest.mark.asyncio
    async def test_block_type_must_be_allowed_variant(self):
        """inv_block_type_check_constraint: invalid type → InvalidBlockTypeError."""
        db = make_db_client()

        with pytest.raises(InvalidBlockTypeError) as exc_info:
            await createBlock(
                db,
                CreateBlockInput(
                    unitId=make_uuid(),
                    startDate="2024-07-01",
                    endDate="2024-07-10",
                    blockType="unknown",
                ),
            )

        allowed = exc_info.value.allowedTypes
        assert set(allowed) == {"reserved", "maintenance", "owner_hold"}, (
            f"Allowed types must be exactly the 3 variants, got {allowed}"
        )

    @pytest.mark.asyncio
    async def test_all_requested_unit_ids_present_in_map(self):
        """inv_all_unit_ids_present_in_map: every requested unitId appears as key."""
        uid1 = make_uuid()
        uid2 = make_uuid()
        uid3 = make_uuid()
        rows = [
            make_block_row(unit_id=uid2, start_date="2024-06-05", end_date="2024-06-10"),
        ]
        db = make_db_client(query_result={"rows": rows, "rowCount": 1})

        result = await getBlocksForUnits(
            db,
            [uid1, uid2, uid3],
            DateRange(start="2024-06-01", end="2024-06-30"),
        )

        assert len(result.entries) == 3, f"Map should have 3 keys, got {len(result.entries)}"
        for uid in [uid1, uid2, uid3]:
            assert uid in result.entries, f"unitId {uid} must be present in map"
        assert result.entries[uid1] == [], "Unit without blocks must have empty list"
        assert len(result.entries[uid2]) == 1, "Unit with blocks must have them"
        assert result.entries[uid3] == [], "Unit without blocks must have empty list"

    @pytest.mark.asyncio
    async def test_results_sorted_by_start_date_ascending(self):
        """inv_results_sorted_by_start_date: blocks returned in startDate order."""
        unit_id = make_uuid()
        rows = [
            make_block_row(unit_id=unit_id, start_date="2024-06-01", end_date="2024-06-03"),
            make_block_row(unit_id=unit_id, start_date="2024-06-05", end_date="2024-06-07"),
            make_block_row(unit_id=unit_id, start_date="2024-06-10", end_date="2024-06-12"),
        ]
        db = make_db_client(query_result={"rows": rows, "rowCount": 3})

        result = await getBlocksForUnit(
            db, unit_id, DateRange(start="2024-06-01", end="2024-06-30")
        )

        assert len(result) == 3
        assert result[0].startDate <= result[1].startDate <= result[2].startDate, (
            "Blocks must be sorted by startDate ascending"
        )

    @pytest.mark.asyncio
    async def test_inclusive_date_ranges_boundary(self):
        """inv_inclusive_date_ranges: boundary dates are included (inclusive-inclusive)."""
        unit_id = make_uuid()
        # Block exactly on boundary
        row = make_block_row(
            unit_id=unit_id, start_date="2024-06-15", end_date="2024-06-15"
        )
        db = make_db_client(query_result={"rows": [row], "rowCount": 1})

        result = await getBlocksForUnit(
            db, unit_id, DateRange(start="2024-06-15", end="2024-06-15")
        )

        assert len(result) == 1, (
            "Block on exact boundary date must be included (inclusive-inclusive)"
        )

    @pytest.mark.asyncio
    async def test_parameterized_queries_no_interpolation(self):
        """inv_parameterized_queries: input passed as parameters, not interpolated."""
        unit_id = make_uuid()
        db = make_db_client(query_result={"rows": [], "rowCount": 0})

        await getBlocksForUnit(
            db, unit_id, DateRange(start="2024-06-01", end="2024-06-30")
        )

        db.query.assert_called_once()
        call_args = db.query.call_args

        # The query should use parameterized placeholders
        # Check that the unitId is passed as a parameter, not embedded in SQL
        if call_args.args:
            query_str = call_args.args[0]
            params = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("params", [])
        else:
            query_str = call_args.kwargs.get("query", call_args.kwargs.get("sql", ""))
            params = call_args.kwargs.get("params", call_args.kwargs.get("values", []))

        # The unit_id value should NOT appear literally in the SQL string
        assert unit_id not in str(query_str), (
            "Unit ID should not be interpolated into SQL string — use parameterized queries"
        )

    @pytest.mark.asyncio
    async def test_created_block_has_uuid_v4_id(self):
        """inv_uuid_v4_format: block id must be a valid UUID v4."""
        unit_id = make_uuid()
        generated_id = make_uuid()
        row = make_block_row(id=generated_id, unit_id=unit_id)
        db = make_db_client(query_result={"rows": [row], "rowCount": 1})

        result = await createBlock(
            db,
            CreateBlockInput(
                unitId=unit_id,
                startDate="2024-07-01",
                endDate="2024-07-10",
                blockType="reserved",
            ),
        )

        assert UUID_V4_REGEX.match(result.id), (
            f"Block id must match UUID v4 pattern, got: {result.id}"
        )


# ===================================================================
# 5. ADDITIONAL VALIDATION TESTS
# ===================================================================


class TestDateRangeValidation:
    """Tests for DateRange validation rules."""

    def test_date_range_valid(self):
        """DateRange with valid ISO dates and end >= start is accepted."""
        dr = DateRange(start="2024-01-01", end="2024-12-31")
        assert dr.start == "2024-01-01"
        assert dr.end == "2024-12-31"

    def test_date_range_same_day(self):
        """DateRange with start == end is valid."""
        dr = DateRange(start="2024-06-15", end="2024-06-15")
        assert dr.start == dr.end

    def test_date_range_start_format_validation(self):
        """DateRange rejects non-ISO-8601 start date."""
        with pytest.raises((ValueError, InvalidDateRangeError)):
            DateRange(start="not-a-date", end="2024-06-30")

    def test_date_range_end_format_validation(self):
        """DateRange rejects non-ISO-8601 end date."""
        with pytest.raises((ValueError, InvalidDateRangeError)):
            DateRange(start="2024-06-01", end="not-a-date")

    def test_date_range_end_before_start_rejected(self):
        """DateRange with end < start is rejected."""
        with pytest.raises((ValueError, InvalidDateRangeError)):
            DateRange(start="2024-06-30", end="2024-06-01")


class TestBlockTypeValidation:
    """Tests for BlockType enum validation."""

    @pytest.mark.parametrize("valid_type", ["reserved", "maintenance", "owner_hold"])
    def test_valid_block_types_accepted(self, valid_type):
        """All three valid block types are accepted in CreateBlockInput."""
        input_data = CreateBlockInput(
            unitId=make_uuid(),
            startDate="2024-07-01",
            endDate="2024-07-10",
            blockType=valid_type,
        )
        assert input_data.blockType == valid_type

    @pytest.mark.parametrize("invalid_type", ["", "RESERVED", "Reserved", "vacation", "blocked", "hold"])
    def test_invalid_block_types_rejected(self, invalid_type):
        """Invalid block types are rejected."""
        with pytest.raises((ValueError, InvalidBlockTypeError)):
            CreateBlockInput(
                unitId=make_uuid(),
                startDate="2024-07-01",
                endDate="2024-07-10",
                blockType=invalid_type,
            )


class TestCreateBlockInputValidation:
    """Tests for CreateBlockInput validation."""

    def test_valid_input_accepted(self):
        """Fully valid CreateBlockInput is constructed successfully."""
        uid = make_uuid()
        inp = CreateBlockInput(
            unitId=uid,
            startDate="2024-07-01",
            endDate="2024-07-10",
            blockType="reserved",
        )
        assert inp.unitId == uid
        assert inp.startDate == "2024-07-01"
        assert inp.endDate == "2024-07-10"
        assert inp.blockType == "reserved"

    def test_end_date_before_start_date_rejected(self):
        """CreateBlockInput with endDate < startDate is rejected."""
        with pytest.raises((ValueError, InvalidDateRangeError)):
            CreateBlockInput(
                unitId=make_uuid(),
                startDate="2024-07-10",
                endDate="2024-07-01",
                blockType="reserved",
            )


class TestAvailabilityBlockFields:
    """Tests for AvailabilityBlock field validation."""

    def test_start_date_regex_valid(self):
        """startDate field matches ISO 8601 date pattern."""
        valid_dates = ["2024-01-01", "2024-06-15", "2024-12-31", "2000-02-29"]
        for d in valid_dates:
            assert ISO_DATE_REGEX.match(d), f"{d} should match ISO date pattern"

    def test_start_date_regex_invalid(self):
        """Non-ISO-8601 strings are rejected by the date regex."""
        invalid_dates = ["2024-13-01", "2024-00-15", "2024-06-00", "24-06-15", "not-a-date"]
        for d in invalid_dates:
            assert not ISO_DATE_REGEX.match(d), f"{d} should NOT match ISO date pattern"
