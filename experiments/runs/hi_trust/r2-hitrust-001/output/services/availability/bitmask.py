"""
Bitmask operations for availability.

Each unit-month is a 32-bit integer. Bit N (1-31) represents day N.
1 = blocked, 0 = available. Bit 0 is unused.
"""

from datetime import date, timedelta
import calendar as cal


def date_range_to_masks(start_date: date, end_date: date) -> dict[str, int]:
    """Convert a date range to bitmasks per year-month.
    
    Returns dict mapping 'YYYY-MM' -> bitmask integer.
    Bit N = 1 means day N is in the range.
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
    """Check if any bits overlap (conflict). Returns True if conflict."""
    return (existing_mask & request_mask) != 0


def apply_block(existing_mask: int, block_mask: int) -> int:
    """Block dates by OR-ing the mask."""
    return existing_mask | block_mask


def release_block(existing_mask: int, release_mask: int) -> int:
    """Release dates by clearing bits."""
    return existing_mask & ~release_mask


def is_available(mask: int, start_date: date, end_date: date, year_month: str) -> bool:
    """Check if all days in the range are available (bit = 0) for a given month."""
    current = start_date
    while current <= end_date:
        ym = current.strftime('%Y-%m')
        if ym == year_month:
            if mask & (1 << current.day):
                return False
        current += timedelta(days=1)
    return True


def compute_contiguous_metadata(mask: int, year_month: str) -> dict:
    """Compute start_free, end_free, max_free for a month."""
    year, month = int(year_month[:4]), int(year_month[5:7])
    days_in_month = cal.monthrange(year, month)[1]

    # start_free: free days contiguous from day 1
    start_free = 0
    for d in range(1, days_in_month + 1):
        if mask & (1 << d):
            break
        start_free += 1

    # end_free: free days contiguous to last day
    end_free = 0
    for d in range(days_in_month, 0, -1):
        if mask & (1 << d):
            break
        end_free += 1

    # max_free: longest contiguous free run
    max_free = 0
    current_run = 0
    for d in range(1, days_in_month + 1):
        if mask & (1 << d):
            current_run = 0
        else:
            current_run += 1
            max_free = max(max_free, current_run)

    return {
        "start_free": start_free,
        "end_free": end_free,
        "max_free": max_free
    }


def mask_to_string(mask: int, days_in_month: int) -> str:
    """Convert bitmask to string representation (for debugging)."""
    bits = []
    for d in range(1, days_in_month + 1):
        bits.append('1' if mask & (1 << d) else '0')
    return ''.join(bits)


def find_next_available_window(masks: dict[str, int], nights: int,
                                after: date, limit_months: int = 12) -> tuple[bool, str | None]:
    """Find next available window of N contiguous nights.
    
    Returns (found, start_date_str).
    """
    current = after
    end_limit = date(after.year + (after.month + limit_months - 1) // 12,
                     ((after.month + limit_months - 1) % 12) + 1,
                     1)

    consecutive_free = 0
    window_start = current

    while current < end_limit:
        ym = current.strftime('%Y-%m')
        mask = masks.get(ym, 0)  # 0 = all available

        if mask & (1 << current.day):
            # Day is blocked
            consecutive_free = 0
            window_start = current + timedelta(days=1)
        else:
            consecutive_free += 1
            if consecutive_free >= nights:
                return True, window_start.strftime('%Y-%m-%d')

        current += timedelta(days=1)

    return False, None
