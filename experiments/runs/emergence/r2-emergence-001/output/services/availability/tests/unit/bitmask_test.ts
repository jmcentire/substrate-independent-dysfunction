/**
 * Unit tests for bitmask operations.
 * Tests: single-day check, range check, overlap detection, encoding/decoding.
 */

import {
  assertEquals,
  assertThrows,
} from "testing";
import { describe, it } from "testing/bdd";

import {
  daysInMonth,
  fullMonthMask,
  isDayAvailable,
  setDayAvailable,
  setDayBlocked,
  rangeMask,
  isRangeAvailable,
  blockRange,
  unblockRange,
  countAvailableDays,
  getAvailableDays,
  getBlockedDays,
  bitmaskToHex,
  hexToBitmask,
  parseDate,
  formatDate,
  splitDateRangeByMonth,
} from "../../src/bitmask.ts";

describe("Bitmask Utilities", () => {
  describe("daysInMonth", () => {
    it("should return correct days for each month", () => {
      assertEquals(daysInMonth(2024, 1), 31); // January
      assertEquals(daysInMonth(2024, 2), 29); // February (leap year)
      assertEquals(daysInMonth(2023, 2), 28); // February (non-leap)
      assertEquals(daysInMonth(2024, 4), 30); // April
      assertEquals(daysInMonth(2024, 12), 31); // December
    });
  });

  describe("fullMonthMask", () => {
    it("should create mask with all days available", () => {
      const jan = fullMonthMask(2024, 1);
      assertEquals(jan, (1 << 31) - 1); // 31 bits set

      const feb = fullMonthMask(2024, 2);
      assertEquals(feb, (1 << 29) - 1); // 29 bits set (leap year)

      const apr = fullMonthMask(2024, 4);
      assertEquals(apr, (1 << 30) - 1); // 30 bits set
    });
  });

  describe("isDayAvailable - single-day check", () => {
    it("should return true for available day", () => {
      const mask = fullMonthMask(2024, 1);
      assertEquals(isDayAvailable(mask, 1), true);
      assertEquals(isDayAvailable(mask, 15), true);
      assertEquals(isDayAvailable(mask, 31), true);
    });

    it("should return false for blocked day", () => {
      const mask = 0; // all blocked
      assertEquals(isDayAvailable(mask, 1), false);
      assertEquals(isDayAvailable(mask, 15), false);
    });

    it("should throw for invalid day", () => {
      assertThrows(() => isDayAvailable(0, 0));
      assertThrows(() => isDayAvailable(0, 32));
    });
  });

  describe("setDayAvailable / setDayBlocked", () => {
    it("should set a day as available", () => {
      let mask = 0;
      mask = setDayAvailable(mask, 5);
      assertEquals(isDayAvailable(mask, 5), true);
      assertEquals(isDayAvailable(mask, 4), false);
    });

    it("should set a day as blocked", () => {
      let mask = fullMonthMask(2024, 1);
      mask = setDayBlocked(mask, 10);
      assertEquals(isDayAvailable(mask, 10), false);
      assertEquals(isDayAvailable(mask, 9), true);
      assertEquals(isDayAvailable(mask, 11), true);
    });

    it("should be idempotent", () => {
      let mask = 0;
      mask = setDayAvailable(mask, 5);
      mask = setDayAvailable(mask, 5);
      assertEquals(isDayAvailable(mask, 5), true);
      assertEquals(countAvailableDays(mask), 1);
    });
  });

  describe("rangeMask", () => {
    it("should create mask for a single day", () => {
      const mask = rangeMask(5, 5);
      assertEquals(isDayAvailable(mask, 5), true);
      assertEquals(isDayAvailable(mask, 4), false);
      assertEquals(isDayAvailable(mask, 6), false);
    });

    it("should create mask for a range", () => {
      const mask = rangeMask(3, 7);
      assertEquals(isDayAvailable(mask, 2), false);
      assertEquals(isDayAvailable(mask, 3), true);
      assertEquals(isDayAvailable(mask, 5), true);
      assertEquals(isDayAvailable(mask, 7), true);
      assertEquals(isDayAvailable(mask, 8), false);
    });

    it("should throw for invalid range", () => {
      assertThrows(() => rangeMask(5, 3));
      assertThrows(() => rangeMask(0, 5));
    });
  });

  describe("isRangeAvailable - O(1) range check", () => {
    it("should return true when all days in range are available", () => {
      const mask = fullMonthMask(2024, 1);
      assertEquals(isRangeAvailable(mask, 1, 31), true);
      assertEquals(isRangeAvailable(mask, 10, 20), true);
    });

    it("should return false when any day in range is blocked", () => {
      let mask = fullMonthMask(2024, 1);
      mask = setDayBlocked(mask, 15);
      assertEquals(isRangeAvailable(mask, 10, 20), false);
      assertEquals(isRangeAvailable(mask, 14, 16), false);
      assertEquals(isRangeAvailable(mask, 15, 15), false);
    });

    it("should return true when range excludes blocked day", () => {
      let mask = fullMonthMask(2024, 1);
      mask = setDayBlocked(mask, 15);
      assertEquals(isRangeAvailable(mask, 1, 14), true);
      assertEquals(isRangeAvailable(mask, 16, 31), true);
    });

    it("should detect overlap with blocked range", () => {
      let mask = fullMonthMask(2024, 1);
      mask = blockRange(mask, 10, 15); // block days 10-15
      // Overlapping ranges
      assertEquals(isRangeAvailable(mask, 8, 12), false);
      assertEquals(isRangeAvailable(mask, 13, 18), false);
      assertEquals(isRangeAvailable(mask, 10, 15), false);
      // Non-overlapping
      assertEquals(isRangeAvailable(mask, 1, 9), true);
      assertEquals(isRangeAvailable(mask, 16, 31), true);
    });
  });

  describe("blockRange / unblockRange", () => {
    it("should block a range of days", () => {
      let mask = fullMonthMask(2024, 1);
      mask = blockRange(mask, 5, 10);
      assertEquals(isDayAvailable(mask, 4), true);
      assertEquals(isDayAvailable(mask, 5), false);
      assertEquals(isDayAvailable(mask, 7), false);
      assertEquals(isDayAvailable(mask, 10), false);
      assertEquals(isDayAvailable(mask, 11), true);
    });

    it("should unblock a range of days", () => {
      let mask = 0; // all blocked
      mask = unblockRange(mask, 5, 10);
      assertEquals(isDayAvailable(mask, 4), false);
      assertEquals(isDayAvailable(mask, 5), true);
      assertEquals(isDayAvailable(mask, 10), true);
      assertEquals(isDayAvailable(mask, 11), false);
    });
  });

  describe("countAvailableDays", () => {
    it("should count available days", () => {
      assertEquals(countAvailableDays(0), 0);
      assertEquals(countAvailableDays(fullMonthMask(2024, 1)), 31);
      assertEquals(countAvailableDays(fullMonthMask(2024, 2)), 29);
    });

    it("should count after modifications", () => {
      let mask = fullMonthMask(2024, 1); // 31 days
      mask = blockRange(mask, 10, 15); // block 6 days
      assertEquals(countAvailableDays(mask), 25);
    });
  });

  describe("getAvailableDays / getBlockedDays", () => {
    it("should list available days", () => {
      let mask = 0;
      mask = setDayAvailable(mask, 1);
      mask = setDayAvailable(mask, 5);
      mask = setDayAvailable(mask, 10);
      assertEquals(getAvailableDays(mask, 10), [1, 5, 10]);
    });

    it("should list blocked days", () => {
      let mask = fullMonthMask(2024, 1);
      mask = setDayBlocked(mask, 3);
      mask = setDayBlocked(mask, 7);
      const blocked = getBlockedDays(mask, 10);
      assertEquals(blocked, [3, 7]);
    });
  });

  describe("hex encoding", () => {
    it("should encode and decode bitmask", () => {
      const mask = fullMonthMask(2024, 1);
      const hex = bitmaskToHex(mask);
      assertEquals(hexToBitmask(hex), mask);
    });

    it("should handle zero", () => {
      assertEquals(bitmaskToHex(0), "00000000");
      assertEquals(hexToBitmask("00000000"), 0);
    });

    it("should round-trip various values", () => {
      const values = [0, 1, 255, 65535, 2147483647];
      for (const v of values) {
        assertEquals(hexToBitmask(bitmaskToHex(v)), v);
      }
    });
  });

  describe("parseDate / formatDate", () => {
    it("should parse date string", () => {
      const { year, month, day } = parseDate("2024-03-15");
      assertEquals(year, 2024);
      assertEquals(month, 3);
      assertEquals(day, 15);
    });

    it("should format date", () => {
      assertEquals(formatDate(2024, 3, 15), "2024-03-15");
      assertEquals(formatDate(2024, 1, 5), "2024-01-05");
    });

    it("should throw on invalid format", () => {
      assertThrows(() => parseDate("invalid"));
      assertThrows(() => parseDate("2024/03/15"));
    });
  });

  describe("splitDateRangeByMonth", () => {
    it("should handle single month range", () => {
      const segments = splitDateRangeByMonth("2024-03-10", "2024-03-20");
      assertEquals(segments.length, 1);
      assertEquals(segments[0], { year: 2024, month: 3, startDay: 10, endDay: 20 });
    });

    it("should split across two months", () => {
      const segments = splitDateRangeByMonth("2024-03-25", "2024-04-05");
      assertEquals(segments.length, 2);
      assertEquals(segments[0], { year: 2024, month: 3, startDay: 25, endDay: 31 });
      assertEquals(segments[1], { year: 2024, month: 4, startDay: 1, endDay: 5 });
    });

    it("should split across year boundary", () => {
      const segments = splitDateRangeByMonth("2024-12-20", "2025-01-10");
      assertEquals(segments.length, 2);
      assertEquals(segments[0], { year: 2024, month: 12, startDay: 20, endDay: 31 });
      assertEquals(segments[1], { year: 2025, month: 1, startDay: 1, endDay: 10 });
    });

    it("should handle single day", () => {
      const segments = splitDateRangeByMonth("2024-03-15", "2024-03-15");
      assertEquals(segments.length, 1);
      assertEquals(segments[0], { year: 2024, month: 3, startDay: 15, endDay: 15 });
    });

    it("should handle three months", () => {
      const segments = splitDateRangeByMonth("2024-01-15", "2024-03-10");
      assertEquals(segments.length, 3);
      assertEquals(segments[0], { year: 2024, month: 1, startDay: 15, endDay: 31 });
      assertEquals(segments[1], { year: 2024, month: 2, startDay: 1, endDay: 29 });
      assertEquals(segments[2], { year: 2024, month: 3, startDay: 1, endDay: 10 });
    });
  });
});
