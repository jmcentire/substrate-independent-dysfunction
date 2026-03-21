import { assertEquals } from "https://deno.land/std@0.208.0/assert/mod.ts";
import {
  isWeekend,
  generateDateRange,
  calculateNights,
  isValidDateRange,
  formatDate,
} from "../../src/utils/dates.ts";

Deno.test("isWeekend - Saturday is weekend", () => {
  // 2024-01-06 is a Saturday
  assertEquals(isWeekend("2024-01-06"), true);
});

Deno.test("isWeekend - Sunday is weekend", () => {
  // 2024-01-07 is a Sunday
  assertEquals(isWeekend("2024-01-07"), true);
});

Deno.test("isWeekend - Monday is not weekend", () => {
  // 2024-01-08 is a Monday
  assertEquals(isWeekend("2024-01-08"), false);
});

Deno.test("isWeekend - Friday is not weekend", () => {
  // 2024-01-05 is a Friday
  assertEquals(isWeekend("2024-01-05"), false);
});

Deno.test("generateDateRange - generates correct dates", () => {
  const dates = generateDateRange("2024-01-01", "2024-01-04");
  assertEquals(dates, ["2024-01-01", "2024-01-02", "2024-01-03"]);
});

Deno.test("generateDateRange - single day", () => {
  const dates = generateDateRange("2024-01-01", "2024-01-02");
  assertEquals(dates, ["2024-01-01"]);
});

Deno.test("generateDateRange - empty for same dates", () => {
  const dates = generateDateRange("2024-01-01", "2024-01-01");
  assertEquals(dates, []);
});

Deno.test("generateDateRange - crosses month boundary", () => {
  const dates = generateDateRange("2024-01-30", "2024-02-02");
  assertEquals(dates, ["2024-01-30", "2024-01-31", "2024-02-01"]);
});

Deno.test("calculateNights - correct calculation", () => {
  assertEquals(calculateNights("2024-01-01", "2024-01-05"), 4);
  assertEquals(calculateNights("2024-01-01", "2024-01-02"), 1);
  assertEquals(calculateNights("2024-01-01", "2024-02-01"), 31);
});

Deno.test("isValidDateRange - valid range", () => {
  assertEquals(isValidDateRange("2024-01-01", "2024-01-05"), true);
});

Deno.test("isValidDateRange - invalid same day", () => {
  assertEquals(isValidDateRange("2024-01-01", "2024-01-01"), false);
});

Deno.test("isValidDateRange - invalid reversed", () => {
  assertEquals(isValidDateRange("2024-01-05", "2024-01-01"), false);
});

Deno.test("formatDate - formats correctly", () => {
  const d = new Date("2024-06-15T12:00:00Z");
  assertEquals(formatDate(d), "2024-06-15");
});
