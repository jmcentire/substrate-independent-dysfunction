"""
Tests for bitmask operations — the core of the Availability Service.
Verifies O(1) operations on 32-bit integer per unit-month.
"""
import pytest
from datetime import date
from services.availability.bitmask import (
    date_range_to_masks, check_conflict, apply_block, remove_block,
    is_available, compute_contiguous_metadata, mask_to_string, get_days_in_month
)


class TestDateRangeToMasks:
    """Test conversion of date ranges to bitmasks."""
    
    def test_single_day(self):
        masks = date_range_to_masks(date(2025, 3, 15), date(2025, 3, 15))
        assert masks == {"2025-03": 1 << 15}
    
    def test_same_month_range(self):
        masks = date_range_to_masks(date(2025, 3, 15), date(2025, 3, 19))
        expected = (1 << 15) | (1 << 16) | (1 << 17) | (1 << 18) | (1 << 19)
        assert masks == {"2025-03": expected}
    
    def test_cross_month_range(self):
        masks = date_range_to_masks(date(2025, 3, 30), date(2025, 4, 2))
        assert "2025-03" in masks
        assert "2025-04" in masks
        assert masks["2025-03"] == (1 << 30) | (1 << 31)
        assert masks["2025-04"] == (1 << 1) | (1 << 2)
    
    def test_full_month(self):
        masks = date_range_to_masks(date(2025, 2, 1), date(2025, 2, 28))
        expected = sum(1 << d for d in range(1, 29))
        assert masks == {"2025-02": expected}


class TestConflictDetection:
    """Test bitmask conflict detection — O(1) AND operation."""
    
    def test_no_conflict(self):
        existing = (1 << 5) | (1 << 6) | (1 << 7)  # Days 5-7 blocked
        request = (1 << 10) | (1 << 11)  # Days 10-11 requested
        assert check_conflict(existing, request) == False
    
    def test_full_conflict(self):
        existing = (1 << 5) | (1 << 6) | (1 << 7)
        request = (1 << 5) | (1 << 6)
        assert check_conflict(existing, request) == True
    
    def test_partial_conflict(self):
        existing = (1 << 5) | (1 << 6) | (1 << 7)
        request = (1 << 7) | (1 << 8) | (1 << 9)
        assert check_conflict(existing, request) == True
    
    def test_empty_existing(self):
        assert check_conflict(0, (1 << 5)) == False
    
    def test_empty_request(self):
        assert check_conflict((1 << 5), 0) == False


class TestBlockOperations:
    """Test block and release operations."""
    
    def test_apply_block(self):
        existing = 0
        request = (1 << 5) | (1 << 6)
        result = apply_block(existing, request)
        assert result == (1 << 5) | (1 << 6)
    
    def test_apply_block_additive(self):
        existing = (1 << 3)
        request = (1 << 5)
        result = apply_block(existing, request)
        assert result == (1 << 3) | (1 << 5)
    
    def test_remove_block(self):
        existing = (1 << 5) | (1 << 6) | (1 << 7)
        request = (1 << 6)
        result = remove_block(existing, request)
        assert result == (1 << 5) | (1 << 7)
    
    def test_remove_nonexistent(self):
        existing = (1 << 5)
        request = (1 << 10)
        result = remove_block(existing, request)
        assert result == (1 << 5)  # Unchanged


class TestDayAvailability:
    """Test individual day availability checks."""
    
    def test_available_day(self):
        mask = (1 << 5) | (1 << 6)
        assert is_available(mask, 3) == True
        assert is_available(mask, 8) == True
    
    def test_blocked_day(self):
        mask = (1 << 5) | (1 << 6)
        assert is_available(mask, 5) == False
        assert is_available(mask, 6) == False
    
    def test_empty_mask(self):
        assert is_available(0, 15) == True


class TestContiguousMetadata:
    """Test contiguous free-day metadata computation."""
    
    def test_all_available(self):
        sf, ef, mf = compute_contiguous_metadata(0, 31)
        assert sf == 31
        assert ef == 31
        assert mf == 31
    
    def test_all_blocked(self):
        mask = sum(1 << d for d in range(1, 32))
        sf, ef, mf = compute_contiguous_metadata(mask, 31)
        assert sf == 0
        assert ef == 0
        assert mf == 0
    
    def test_middle_blocked(self):
        # Days 10-15 blocked in a 31-day month
        mask = sum(1 << d for d in range(10, 16))
        sf, ef, mf = compute_contiguous_metadata(mask, 31)
        assert sf == 9   # Days 1-9 free
        assert ef == 16  # Days 16-31 free
        assert mf == 16  # Longest run is end (16 days)
    
    def test_start_blocked(self):
        # Days 1-5 blocked
        mask = sum(1 << d for d in range(1, 6))
        sf, ef, mf = compute_contiguous_metadata(mask, 31)
        assert sf == 0
        assert ef == 26  # Days 6-31
        assert mf == 26


class TestHelpers:
    """Test helper functions."""
    
    def test_get_days_in_month(self):
        assert get_days_in_month("2025-02") == 28
        assert get_days_in_month("2025-03") == 31
        assert get_days_in_month("2024-02") == 29  # Leap year
        assert get_days_in_month("2025-04") == 30
    
    def test_mask_to_string(self):
        mask = (1 << 1) | (1 << 2) | (1 << 3)  # Days 1-3 blocked
        s = mask_to_string(mask, 5)
        assert s == "11100"  # Days 1,2,3 blocked; 4,5 available
