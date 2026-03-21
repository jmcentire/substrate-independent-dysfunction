/**
 * Unit tests for Zod validation schemas.
 */
import {
  assertEquals,
  assertThrows,
} from "https://deno.land/std@0.208.0/assert/mod.ts";
import {
  GetPricingQuerySchema,
  QuoteRequestSchema,
  RateUpdateSchema,
} from "../../src/types.ts";

// --- GetPricingQuerySchema ---

Deno.test("GetPricingQuerySchema - valid query", () => {
  const result = GetPricingQuerySchema.parse({
    start: "2024-01-01",
    end: "2024-01-31",
  });
  assertEquals(result.start, "2024-01-01");
  assertEquals(result.end, "2024-01-31");
});

Deno.test("GetPricingQuerySchema - rejects invalid date format", () => {
  assertThrows(() => {
    GetPricingQuerySchema.parse({ start: "01-01-2024", end: "01-31-2024" });
  });
});

Deno.test("GetPricingQuerySchema - rejects missing fields", () => {
  assertThrows(() => {
    GetPricingQuerySchema.parse({ start: "2024-01-01" });
  });
});

// --- QuoteRequestSchema ---

Deno.test("QuoteRequestSchema - valid request", () => {
  const result = QuoteRequestSchema.parse({
    unitId: "550e8400-e29b-41d4-a716-446655440000",
    checkIn: "2024-06-01",
    checkOut: "2024-06-05",
    guestCount: 4,
  });
  assertEquals(result.unitId, "550e8400-e29b-41d4-a716-446655440000");
  assertEquals(result.guestCount, 4);
});

Deno.test("QuoteRequestSchema - defaults guestCount to 1", () => {
  const result = QuoteRequestSchema.parse({
    unitId: "550e8400-e29b-41d4-a716-446655440000",
    checkIn: "2024-06-01",
    checkOut: "2024-06-05",
  });
  assertEquals(result.guestCount, 1);
});

Deno.test("QuoteRequestSchema - rejects non-uuid unitId", () => {
  assertThrows(() => {
    QuoteRequestSchema.parse({
      unitId: "not-a-uuid",
      checkIn: "2024-06-01",
      checkOut: "2024-06-05",
    });
  });
});

Deno.test("QuoteRequestSchema - rejects invalid date format", () => {
  assertThrows(() => {
    QuoteRequestSchema.parse({
      unitId: "550e8400-e29b-41d4-a716-446655440000",
      checkIn: "June 1, 2024",
      checkOut: "2024-06-05",
    });
  });
});

// --- RateUpdateSchema ---

Deno.test("RateUpdateSchema - valid rate update", () => {
  const result = RateUpdateSchema.parse({
    rates: [
      {
        date: "2024-07-01",
        baseRate: 25000,
        weekendRate: 30000,
        seasonalMultiplier: 1.5,
        minimumStay: 3,
        currency: "USD",
      },
    ],
  });
  assertEquals(result.rates.length, 1);
  assertEquals(result.rates[0].baseRate, 25000);
  assertEquals(result.rates[0].weekendRate, 30000);
});

Deno.test("RateUpdateSchema - applies defaults", () => {
  const result = RateUpdateSchema.parse({
    rates: [
      {
        date: "2024-07-01",
        baseRate: 25000,
      },
    ],
  });
  assertEquals(result.rates[0].seasonalMultiplier, 1.0);
  assertEquals(result.rates[0].minimumStay, 1);
  assertEquals(result.rates[0].currency, "USD");
});

Deno.test("RateUpdateSchema - rejects empty rates array", () => {
  assertThrows(() => {
    RateUpdateSchema.parse({ rates: [] });
  });
});

Deno.test("RateUpdateSchema - rejects negative baseRate", () => {
  assertThrows(() => {
    RateUpdateSchema.parse({
      rates: [{ date: "2024-07-01", baseRate: -100 }],
    });
  });
});

Deno.test("RateUpdateSchema - allows null weekendRate", () => {
  const result = RateUpdateSchema.parse({
    rates: [
      {
        date: "2024-07-01",
        baseRate: 25000,
        weekendRate: null,
      },
    ],
  });
  assertEquals(result.rates[0].weekendRate, null);
});

Deno.test("RateUpdateSchema - multiple rates", () => {
  const result = RateUpdateSchema.parse({
    rates: [
      { date: "2024-07-01", baseRate: 25000 },
      { date: "2024-07-02", baseRate: 26000 },
      { date: "2024-07-03", baseRate: 27000 },
    ],
  });
  assertEquals(result.rates.length, 3);
});
