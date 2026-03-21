import { assertEquals } from "https://deno.land/std@0.208.0/assert/mod.ts";
import { dateRangeQuerySchema, rateUpdateSchema, quoteRequestSchema } from "../../src/validators.ts";

Deno.test("dateRangeQuerySchema - valid input", () => {
  const result = dateRangeQuerySchema.safeParse({ start: "2024-01-01", end: "2024-01-05" });
  assertEquals(result.success, true);
});

Deno.test("dateRangeQuerySchema - rejects invalid date format", () => {
  const result = dateRangeQuerySchema.safeParse({ start: "01/01/2024", end: "2024-01-05" });
  assertEquals(result.success, false);
});

Deno.test("dateRangeQuerySchema - rejects missing fields", () => {
  const result = dateRangeQuerySchema.safeParse({ start: "2024-01-01" });
  assertEquals(result.success, false);
});

Deno.test("rateUpdateSchema - valid input", () => {
  const result = rateUpdateSchema.safeParse({
    rates: [
      { startDate: "2024-01-01", endDate: "2024-01-05", baseRate: 10000 },
    ],
  });
  assertEquals(result.success, true);
  if (result.success) {
    assertEquals(result.data.rates[0].currency, "USD");
    assertEquals(result.data.rates[0].rateType, "standard");
    assertEquals(result.data.rates[0].seasonalMultiplier, 1.0);
  }
});

Deno.test("rateUpdateSchema - rejects empty rates array", () => {
  const result = rateUpdateSchema.safeParse({ rates: [] });
  assertEquals(result.success, false);
});

Deno.test("rateUpdateSchema - rejects negative baseRate", () => {
  const result = rateUpdateSchema.safeParse({
    rates: [{ startDate: "2024-01-01", endDate: "2024-01-05", baseRate: -100 }],
  });
  assertEquals(result.success, false);
});

Deno.test("rateUpdateSchema - accepts optional fields", () => {
  const result = rateUpdateSchema.safeParse({
    rates: [
      {
        startDate: "2024-01-01",
        endDate: "2024-01-05",
        baseRate: 10000,
        currency: "EUR",
        rateType: "seasonal",
        seasonalMultiplier: 1.5,
        weekendMultiplier: 1.25,
        minStay: 3,
        weekendRate: 15000,
      },
    ],
  });
  assertEquals(result.success, true);
  if (result.success) {
    assertEquals(result.data.rates[0].currency, "EUR");
    assertEquals(result.data.rates[0].rateType, "seasonal");
    assertEquals(result.data.rates[0].weekendRate, 15000);
  }
});

Deno.test("quoteRequestSchema - valid input", () => {
  const result = quoteRequestSchema.safeParse({
    unitId: "unit-123",
    startDate: "2024-01-01",
    endDate: "2024-01-05",
  });
  assertEquals(result.success, true);
  if (result.success) {
    assertEquals(result.data.guests, 1);
  }
});

Deno.test("quoteRequestSchema - rejects empty unitId", () => {
  const result = quoteRequestSchema.safeParse({
    unitId: "",
    startDate: "2024-01-01",
    endDate: "2024-01-05",
  });
  assertEquals(result.success, false);
});

Deno.test("quoteRequestSchema - accepts optional fields", () => {
  const result = quoteRequestSchema.safeParse({
    unitId: "unit-123",
    startDate: "2024-01-01",
    endDate: "2024-01-05",
    guests: 4,
    promoCode: "SAVE10",
  });
  assertEquals(result.success, true);
  if (result.success) {
    assertEquals(result.data.guests, 4);
    assertEquals(result.data.promoCode, "SAVE10");
  }
});
