/**
 * Unit tests for the Pricing Engine.
 */
import {
  assertEquals,
  assertStrictEquals,
} from "https://deno.land/std@0.208.0/assert/mod.ts";
import {
  isWeekendNight,
  generateDateRange,
  calculateNightlyPrice,
  calculateDailyPrices,
  isMinimumStayMet,
  calculateTaxes,
  calculateFees,
  defaultRate,
} from "../../src/services/pricing_engine.ts";
import type { Rate } from "../../src/types.ts";

Deno.test("isWeekendNight - Friday is weekend", () => {
  // 2024-01-05 is a Friday
  assertStrictEquals(isWeekendNight("2024-01-05"), true);
});

Deno.test("isWeekendNight - Saturday is weekend", () => {
  // 2024-01-06 is a Saturday
  assertStrictEquals(isWeekendNight("2024-01-06"), true);
});

Deno.test("isWeekendNight - Sunday is not weekend", () => {
  // 2024-01-07 is a Sunday
  assertStrictEquals(isWeekendNight("2024-01-07"), false);
});

Deno.test("isWeekendNight - Monday is not weekend", () => {
  // 2024-01-08 is a Monday
  assertStrictEquals(isWeekendNight("2024-01-08"), false);
});

Deno.test("isWeekendNight - Thursday is not weekend", () => {
  // 2024-01-04 is a Thursday
  assertStrictEquals(isWeekendNight("2024-01-04"), false);
});

Deno.test("generateDateRange - basic range", () => {
  const dates = generateDateRange("2024-01-01", "2024-01-05");
  assertEquals(dates, [
    "2024-01-01",
    "2024-01-02",
    "2024-01-03",
    "2024-01-04",
  ]);
});

Deno.test("generateDateRange - single day", () => {
  const dates = generateDateRange("2024-01-01", "2024-01-02");
  assertEquals(dates, ["2024-01-01"]);
});

Deno.test("generateDateRange - same date returns empty", () => {
  const dates = generateDateRange("2024-01-01", "2024-01-01");
  assertEquals(dates, []);
});

Deno.test("generateDateRange - spans month boundary", () => {
  const dates = generateDateRange("2024-01-30", "2024-02-02");
  assertEquals(dates, ["2024-01-30", "2024-01-31", "2024-02-01"]);
});

Deno.test("calculateNightlyPrice - base rate on weekday", () => {
  const rate: Rate = {
    id: "1",
    unitId: "u1",
    date: "2024-01-08", // Monday
    baseRate: 20000,
    weekendRate: 25000,
    seasonalMultiplier: 1.0,
    minimumStay: 1,
    currency: "USD",
    createdAt: new Date(),
    updatedAt: new Date(),
  };
  const price = calculateNightlyPrice(rate, "2024-01-08");
  assertStrictEquals(price, 20000);
});

Deno.test("calculateNightlyPrice - weekend rate on Friday", () => {
  const rate: Rate = {
    id: "1",
    unitId: "u1",
    date: "2024-01-05", // Friday
    baseRate: 20000,
    weekendRate: 25000,
    seasonalMultiplier: 1.0,
    minimumStay: 1,
    currency: "USD",
    createdAt: new Date(),
    updatedAt: new Date(),
  };
  const price = calculateNightlyPrice(rate, "2024-01-05");
  assertStrictEquals(price, 25000);
});

Deno.test("calculateNightlyPrice - weekend falls back to base when weekendRate is null", () => {
  const rate: Rate = {
    id: "1",
    unitId: "u1",
    date: "2024-01-05", // Friday
    baseRate: 20000,
    weekendRate: null,
    seasonalMultiplier: 1.0,
    minimumStay: 1,
    currency: "USD",
    createdAt: new Date(),
    updatedAt: new Date(),
  };
  const price = calculateNightlyPrice(rate, "2024-01-05");
  assertStrictEquals(price, 20000);
});

Deno.test("calculateNightlyPrice - seasonal multiplier applied", () => {
  const rate: Rate = {
    id: "1",
    unitId: "u1",
    date: "2024-01-08",
    baseRate: 20000,
    weekendRate: null,
    seasonalMultiplier: 1.5,
    minimumStay: 1,
    currency: "USD",
    createdAt: new Date(),
    updatedAt: new Date(),
  };
  const price = calculateNightlyPrice(rate, "2024-01-08");
  assertStrictEquals(price, 30000);
});

Deno.test("calculateNightlyPrice - seasonal multiplier with weekend rate", () => {
  const rate: Rate = {
    id: "1",
    unitId: "u1",
    date: "2024-01-05", // Friday
    baseRate: 20000,
    weekendRate: 25000,
    seasonalMultiplier: 2.0,
    minimumStay: 1,
    currency: "USD",
    createdAt: new Date(),
    updatedAt: new Date(),
  };
  const price = calculateNightlyPrice(rate, "2024-01-05");
  assertStrictEquals(price, 50000);
});

Deno.test("calculateDailyPrices - uses default rate when no rates provided", () => {
  const prices = calculateDailyPrices(
    "unit-1",
    [],
    "2024-01-08",
    "2024-01-10",
  );
  assertEquals(prices.length, 2);
  assertStrictEquals(prices[0].price, 15000); // default $150
  assertStrictEquals(prices[1].price, 15000);
});

Deno.test("calculateDailyPrices - maps specific rates to dates", () => {
  const rates: Rate[] = [
    {
      id: "1",
      unitId: "u1",
      date: "2024-01-08",
      baseRate: 18000,
      weekendRate: null,
      seasonalMultiplier: 1.0,
      minimumStay: 2,
      currency: "USD",
      createdAt: new Date(),
      updatedAt: new Date(),
    },
    {
      id: "2",
      unitId: "u1",
      date: "2024-01-09",
      baseRate: 19000,
      weekendRate: null,
      seasonalMultiplier: 1.0,
      minimumStay: 2,
      currency: "USD",
      createdAt: new Date(),
      updatedAt: new Date(),
    },
  ];

  const prices = calculateDailyPrices("u1", rates, "2024-01-08", "2024-01-10");
  assertEquals(prices.length, 2);
  assertStrictEquals(prices[0].price, 18000);
  assertStrictEquals(prices[1].price, 19000);
  assertStrictEquals(prices[0].minimumStay, 2);
});

Deno.test("isMinimumStayMet - passes when enough nights", () => {
  const prices = [
    { date: "2024-01-01", price: 100, isWeekend: false, seasonalMultiplier: 1, minimumStay: 3, currency: "USD" },
    { date: "2024-01-02", price: 100, isWeekend: false, seasonalMultiplier: 1, minimumStay: 3, currency: "USD" },
    { date: "2024-01-03", price: 100, isWeekend: false, seasonalMultiplier: 1, minimumStay: 3, currency: "USD" },
  ];
  assertStrictEquals(isMinimumStayMet(prices, 3), true);
});

Deno.test("isMinimumStayMet - fails when not enough nights", () => {
  const prices = [
    { date: "2024-01-01", price: 100, isWeekend: false, seasonalMultiplier: 1, minimumStay: 3, currency: "USD" },
    { date: "2024-01-02", price: 100, isWeekend: false, seasonalMultiplier: 1, minimumStay: 3, currency: "USD" },
  ];
  assertStrictEquals(isMinimumStayMet(prices, 2), false);
});

Deno.test("isMinimumStayMet - uses max minimumStay across dates", () => {
  const prices = [
    { date: "2024-01-01", price: 100, isWeekend: false, seasonalMultiplier: 1, minimumStay: 1, currency: "USD" },
    { date: "2024-01-02", price: 100, isWeekend: false, seasonalMultiplier: 1, minimumStay: 5, currency: "USD" },
    { date: "2024-01-03", price: 100, isWeekend: false, seasonalMultiplier: 1, minimumStay: 2, currency: "USD" },
  ];
  assertStrictEquals(isMinimumStayMet(prices, 3), false);
  assertStrictEquals(isMinimumStayMet(prices, 5), true);
});

Deno.test("isMinimumStayMet - empty prices returns true", () => {
  assertStrictEquals(isMinimumStayMet([], 0), true);
});

Deno.test("calculateTaxes - 12% default", () => {
  assertStrictEquals(calculateTaxes(10000), 1200);
});

Deno.test("calculateTaxes - custom rate", () => {
  assertStrictEquals(calculateTaxes(10000, 0.08), 800);
});

Deno.test("calculateTaxes - rounds correctly", () => {
  assertStrictEquals(calculateTaxes(10001), 1200); // 10001 * 0.12 = 1200.12 rounds to 1200
});

Deno.test("calculateFees - default cleaning + service fee", () => {
  // $75 cleaning + 3% of 10000 = 7500 + 300 = 7800
  assertStrictEquals(calculateFees(10000), 7800);
});

Deno.test("calculateFees - custom values", () => {
  // $100 cleaning + 5% of 20000 = 10000 + 1000 = 11000
  assertStrictEquals(calculateFees(20000, 10000, 0.05), 11000);
});

Deno.test("defaultRate - provides sensible defaults", () => {
  const rate = defaultRate("unit-123", "2024-01-15");
  assertStrictEquals(rate.baseRate, 15000);
  assertStrictEquals(rate.weekendRate, null);
  assertStrictEquals(rate.seasonalMultiplier, 1.0);
  assertStrictEquals(rate.minimumStay, 1);
  assertStrictEquals(rate.currency, "USD");
  assertStrictEquals(rate.unitId, "unit-123");
  assertStrictEquals(rate.date, "2024-01-15");
});
