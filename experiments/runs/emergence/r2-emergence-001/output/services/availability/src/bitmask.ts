/**
 * Bitmask utilities for availability encoding.
 *
 * Each bit represents one day of a month:
 *   bit 0 = day 1, bit 1 = day 2, ..., bit 30 = day 31
 *   1 = available, 0 = blocked
 *
 * A full month with all days available (31 days):
 *   0b1111111111111111111111111111111 = 2147483647
 */

/** Number of days in a given month/year */
export function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate();
}

/** Create a bitmask with all days available for a given month */
export function fullMonthMask(year: number, month: number): number {
  const days = daysInMonth(year, month);
  return (1 << days) - 1;
}

/** Check if a specific day is available in the bitmask */
export function isDayAvailable(bitmask: number, day: number): boolean {
  if (day < 1 || day > 31) {
    throw new Error(`Invalid day: ${day}. Must be between 1 and 31.`);
  }
  return (bitmask & (1 << (day - 1))) !== 0;
}

/** Set a specific day as available */
export function setDayAvailable(bitmask: number, day: number): number {
  if (day < 1 || day > 31) {
    throw new Error(`Invalid day: ${day}. Must be between 1 and 31.`);
  }
  return bitmask | (1 << (day - 1));
}

/** Set a specific day as blocked (unavailable) */
export function setDayBlocked(bitmask: number, day: number): number {
  if (day < 1 || day > 31) {
    throw new Error(`Invalid day: ${day}. Must be between 1 and 31.`);
  }
  return bitmask & ~(1 << (day - 1));
}

/**
 * Create a range mask for days startDay..endDay (inclusive).
 * E.g., rangeMask(3, 5) sets bits for days 3, 4, 5.
 */
export function rangeMask(startDay: number, endDay: number): number {
  if (startDay < 1 || endDay > 31 || startDay > endDay) {
    throw new Error(`Invalid range: ${startDay}-${endDay}`);
  }
  // Create mask with bits startDay-1 through endDay-1 set
  const width = endDay - startDay + 1;
  const mask = ((1 << width) - 1) << (startDay - 1);
  return mask;
}

/**
 * Check if all days in a range are available (O(1) operation).
 * Uses bitwise AND with range mask.
 */
export function isRangeAvailable(
  bitmask: number,
  startDay: number,
  endDay: number,
): boolean {
  const mask = rangeMask(startDay, endDay);
  return (bitmask & mask) === mask;
}

/**
 * Block a range of days (set bits to 0).
 */
export function blockRange(
  bitmask: number,
  startDay: number,
  endDay: number,
): number {
  const mask = rangeMask(startDay, endDay);
  return bitmask & ~mask;
}

/**
 * Unblock a range of days (set bits to 1).
 */
export function unblockRange(
  bitmask: number,
  startDay: number,
  endDay: number,
): number {
  const mask = rangeMask(startDay, endDay);
  return bitmask | mask;
}

/**
 * Count the number of available days in a bitmask.
 * Uses Brian Kernighan's algorithm.
 */
export function countAvailableDays(bitmask: number): number {
  let count = 0;
  let n = bitmask;
  while (n) {
    n &= n - 1;
    count++;
  }
  return count;
}

/**
 * Get list of available day numbers from a bitmask.
 */
export function getAvailableDays(bitmask: number, maxDay: number = 31): number[] {
  const days: number[] = [];
  for (let d = 1; d <= maxDay; d++) {
    if (isDayAvailable(bitmask, d)) {
      days.push(d);
    }
  }
  return days;
}

/**
 * Get list of blocked day numbers from a bitmask.
 */
export function getBlockedDays(bitmask: number, maxDay: number = 31): number[] {
  const days: number[] = [];
  for (let d = 1; d <= maxDay; d++) {
    if (!isDayAvailable(bitmask, d)) {
      days.push(d);
    }
  }
  return days;
}

/**
 * Encode a bitmask to a hex string for Redis storage.
 */
export function bitmaskToHex(bitmask: number): string {
  return (bitmask >>> 0).toString(16).padStart(8, "0");
}

/**
 * Decode a hex string back to a bitmask number.
 */
export function hexToBitmask(hex: string): number {
  return parseInt(hex, 16);
}

/**
 * Parse a date string (YYYY-MM-DD) into components.
 */
export function parseDate(dateStr: string): { year: number; month: number; day: number } {
  const parts = dateStr.split("-");
  if (parts.length !== 3) {
    throw new Error(`Invalid date format: ${dateStr}. Expected YYYY-MM-DD.`);
  }
  return {
    year: parseInt(parts[0], 10),
    month: parseInt(parts[1], 10),
    day: parseInt(parts[2], 10),
  };
}

/**
 * Format date components back to YYYY-MM-DD string.
 */
export function formatDate(year: number, month: number, day: number): string {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

/**
 * Break a date range into per-month segments.
 * Each segment has { year, month, startDay, endDay }.
 */
export function splitDateRangeByMonth(
  startDate: string,
  endDate: string,
): Array<{ year: number; month: number; startDay: number; endDay: number }> {
  const start = parseDate(startDate);
  const end = parseDate(endDate);

  const segments: Array<{ year: number; month: number; startDay: number; endDay: number }> = [];

  let currentYear = start.year;
  let currentMonth = start.month;

  while (
    currentYear < end.year ||
    (currentYear === end.year && currentMonth <= end.month)
  ) {
    const startDay = (currentYear === start.year && currentMonth === start.month)
      ? start.day
      : 1;
    const endDay = (currentYear === end.year && currentMonth === end.month)
      ? end.day
      : daysInMonth(currentYear, currentMonth);

    segments.push({
      year: currentYear,
      month: currentMonth,
      startDay,
      endDay,
    });

    // Advance to next month
    currentMonth++;
    if (currentMonth > 12) {
      currentMonth = 1;
      currentYear++;
    }
  }

  return segments;
}
