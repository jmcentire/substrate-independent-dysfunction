"""
Bitmask operations for availability.
Each unit-month is a 32-bit integer. Bit N (1-31) represents day N.
1 = blocked, 0 = available. Bit 0 unused.
"""
from datetime import date, timedelta
import calendar


def date_range_to_masks(start_date: date, end_date: date) -> dict:
    """
    Convert a date range to bitmasks (one per month).
    Returns {year_month: mask} dict.
    
    March 15-19 → {'2025-03': (1<<15)|(1<<16)|(1<<17)|(1<<18)|(1<<19)}
    """
    masks = {}
    current = start_date
    while current <= end_date:
        ym = current.strftime('%Y-%m')
        if ym not in masks:
            masks[ym] = 0
        masks[ym] |= (1 << current.day)
        current += timedelta(days=1)
    return masks


def check_conflict(existing_mask: int, request_mask: int) -> bool:
    """Check if any dates conflict. Returns True if conflict."""
    return (existing_mask & request_mask) != 0


def apply_block(existing_mask: int, request_mask: int) -> int:
    """Block dates (set bits to 1). Returns new mask."""
    return existing_mask | request_mask


def remove_block(existing_mask: int, request_mask: int) -> int:
    """Release dates (set bits to 0). Returns new mask."""
    return existing_mask & ~request_mask


def is_available(mask: int, day: int) -> bool:
    """Check if a specific day is available (bit is 0)."""
    return (mask & (1 << day)) == 0


def compute_contiguous_metadata(mask: int, days_in_month: int) -> tuple:
    """
    Compute contiguous free-day metadata for a month.
    Returns (start_free, end_free, max_free).
    """
    # start_free: free days contiguous from day 1
    start_free = 0
    for d in range(1, days_in_month + 1):
        if is_available(mask, d):
            start_free += 1
        else:
            break

    # end_free: free days contiguous to last day of month
    end_free = 0
    for d in range(days_in_month, 0, -1):
        if is_available(mask, d):
            end_free += 1
        else:
            break

    # max_free: longest contiguous free run
    max_free = 0
    current_run = 0
    for d in range(1, days_in_month + 1):
        if is_available(mask, d):
            current_run += 1
            max_free = max(max_free, current_run)
        else:
            current_run = 0

    return start_free, end_free, max_free


def mask_to_string(mask: int, days_in_month: int) -> str:
    """Convert bitmask to string representation for debugging."""
    return ''.join('1' if not is_available(mask, d) else '0'
                   for d in range(1, days_in_month + 1))


def get_days_in_month(year_month: str) -> int:
    """Get number of days in a year-month string."""
    year, month = int(year_month[:4]), int(year_month[5:7])
    return calendar.monthrange(year, month)[1]
