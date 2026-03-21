import { assertEquals, assertRejects } from "https://deno.land/std@0.208.0/assert/mod.ts";
import { PricingService, PricingError } from "../../src/services/pricing_service.ts";
import type { DailyRate, LosDiscount, QuoteResponse, RateUpdate } from "../../src/types.ts";
import type { CacheClient } from "../../src/cache/redis_client.ts";

// --- Mock implementations ---

class MockRateRepository {
  private rates: DailyRate[] = [];
  private discounts: LosDiscount[] = [];

  setRates(rates: DailyRate[]) {
    this.rates = rates;
  }

  setDiscounts(discounts: LosDiscount[]) {
    this.discounts = discounts;
  }

  async getRates(unitId: string, startDate: string, endDate: string): Promise<DailyRate[]> {
    const start = new Date(startDate + "T00:00:00Z");
    const end = new Date(endDate + "T00:00:00Z");
    return this.rates.filter((r) => {
      const d = new Date(r.date + "T00:00:00Z");
      return r.unitId === unitId && d >= start && d < end;
    });
  }

  async getLosDiscounts(unitId: string): Promise<LosDiscount[]> {
    return this.discounts
      .filter((d) => d.unitId === unitId)
      .sort((a, b) => b.minNights - a.minNights);
  }

  async upsertRates(_unitId: string, _updates: RateUpdate[]): Promise<number> {
    return 5; // mock
  }
}

class MockQuoteRepository {
  private quotes: QuoteResponse[] = [];

  async saveQuote(quote: QuoteResponse): Promise<void> {
    this.quotes.push(quote);
  }

  async getQuote(quoteId: string): Promise<QuoteResponse | null> {
    return this.quotes.find((q) => q.quoteId === quoteId) || null;
  }
}

class MockCache implements CacheClient {
  private store = new Map<string, string>();
  public invalidatedUnits: string[] = [];

  async connect(): Promise<void> {}
  async get(key: string, _unitId: string): Promise<string | null> {
    return this.store.get(key) ?? null;
  }
  async set(key: string, _unitId: string, value: string): Promise<void> {
    this.store.set(key, value);
  }
  async invalidateUnit(unitId: string): Promise<void> {
    this.invalidatedUnits.push(unitId);
    // Remove matching keys
    for (const key of this.store.keys()) {
      if (key.includes(unitId)) {
        this.store.delete(key);
      }
    }
  }
  async close(): Promise<void> {}
}

// --- Tests ---

function createService() {
  const rateRepo = new MockRateRepository();
  const quoteRepo = new MockQuoteRepository();
  const cache = new MockCache();
  // deno-lint-ignore no-explicit-any
  const service = new PricingService(rateRepo as any, quoteRepo as any, cache);
  return { service, rateRepo, quoteRepo, cache };
}

Deno.test("PricingService.getPricing - throws on invalid date range", async () => {
  const { service } = createService();
  await assertRejects(
    () => service.getPricing("unit-1", "2024-01-05", "2024-01-01"),
    PricingError,
    "Invalid date range"
  );
});

Deno.test("PricingService.getPricing - returns zeros when no rates configured", async () => {
  const { service } = createService();
  const result = await service.getPricing("unit-1", "2024-01-01", "2024-01-04");

  assertEquals(result.unitId, "unit-1");
  assertEquals(result.nights.length, 3);
  assertEquals(result.subtotal, 0);
  assertEquals(result.total, 0);
});

Deno.test("PricingService.getPricing - calculates correct subtotal for weekday rates", async () => {
  const { service, rateRepo } = createService();

  // 2024-01-08 (Mon), 2024-01-09 (Tue), 2024-01-10 (Wed)
  rateRepo.setRates([
    { unitId: "unit-1", date: "2024-01-08", baseRate: 10000, currency: "USD", rateType: "standard", seasonalMultiplier: 1.0, weekendMultiplier: 1.0, minStay: 1 },
    { unitId: "unit-1", date: "2024-01-09", baseRate: 10000, currency: "USD", rateType: "standard", seasonalMultiplier: 1.0, weekendMultiplier: 1.0, minStay: 1 },
    { unitId: "unit-1", date: "2024-01-10", baseRate: 10000, currency: "USD", rateType: "standard", seasonalMultiplier: 1.0, weekendMultiplier: 1.0, minStay: 1 },
  ]);

  const result = await service.getPricing("unit-1", "2024-01-08", "2024-01-11");

  assertEquals(result.subtotal, 30000);
  assertEquals(result.total, 30000);
  assertEquals(result.nights.length, 3);
  assertEquals(result.averageNightlyRate, 10000);
});

Deno.test("PricingService.getPricing - applies seasonal multiplier", async () => {
  const { service, rateRepo } = createService();

  rateRepo.setRates([
    { unitId: "unit-1", date: "2024-01-08", baseRate: 10000, currency: "USD", rateType: "seasonal", seasonalMultiplier: 1.5, weekendMultiplier: 1.0, minStay: 1 },
  ]);

  const result = await service.getPricing("unit-1", "2024-01-08", "2024-01-09");

  assertEquals(result.nights[0].adjustedRate, 15000); // 10000 * 1.5
  assertEquals(result.subtotal, 15000);
});

Deno.test("PricingService.getPricing - applies weekend multiplier on weekends", async () => {
  const { service, rateRepo } = createService();

  // 2024-01-06 is Saturday
  rateRepo.setRates([
    { unitId: "unit-1", date: "2024-01-06", baseRate: 10000, currency: "USD", rateType: "standard", seasonalMultiplier: 1.0, weekendMultiplier: 1.25, minStay: 1 },
  ]);

  const result = await service.getPricing("unit-1", "2024-01-06", "2024-01-07");

  assertEquals(result.nights[0].isWeekend, true);
  assertEquals(result.nights[0].adjustedRate, 12500); // 10000 * 1.25
});

Deno.test("PricingService.getPricing - does NOT apply weekend multiplier on weekdays", async () => {
  const { service, rateRepo } = createService();

  // 2024-01-08 is Monday
  rateRepo.setRates([
    { unitId: "unit-1", date: "2024-01-08", baseRate: 10000, currency: "USD", rateType: "standard", seasonalMultiplier: 1.0, weekendMultiplier: 1.25, minStay: 1 },
  ]);

  const result = await service.getPricing("unit-1", "2024-01-08", "2024-01-09");

  assertEquals(result.nights[0].isWeekend, false);
  assertEquals(result.nights[0].adjustedRate, 10000); // no weekend multiplier
});

Deno.test("PricingService.getPricing - applies LOS discount", async () => {
  const { service, rateRepo } = createService();

  // 7 nights: Mon-Sun, 2024-01-08 to 2024-01-15
  const rates: DailyRate[] = [];
  for (let i = 8; i <= 14; i++) {
    const day = i.toString().padStart(2, "0");
    rates.push({
      unitId: "unit-1",
      date: `2024-01-${day}`,
      baseRate: 10000,
      currency: "USD",
      rateType: "standard",
      seasonalMultiplier: 1.0,
      weekendMultiplier: 1.0,
      minStay: 1,
    });
  }
  rateRepo.setRates(rates);
  rateRepo.setDiscounts([
    { unitId: "unit-1", minNights: 7, discountPercent: 10 },
    { unitId: "unit-1", minNights: 30, discountPercent: 20 },
  ]);

  const result = await service.getPricing("unit-1", "2024-01-08", "2024-01-15");

  assertEquals(result.subtotal, 70000);
  assertEquals(result.losDiscount, 7000); // 10% of 70000
  assertEquals(result.total, 63000);
});

Deno.test("PricingService.getPricing - uses cache on second call", async () => {
  const { service, rateRepo, cache } = createService();

  rateRepo.setRates([
    { unitId: "unit-1", date: "2024-01-08", baseRate: 10000, currency: "USD", rateType: "standard", seasonalMultiplier: 1.0, weekendMultiplier: 1.0, minStay: 1 },
  ]);

  // First call populates cache
  const result1 = await service.getPricing("unit-1", "2024-01-08", "2024-01-09");

  // Change underlying data
  rateRepo.setRates([
    { unitId: "unit-1", date: "2024-01-08", baseRate: 20000, currency: "USD", rateType: "standard", seasonalMultiplier: 1.0, weekendMultiplier: 1.0, minStay: 1 },
  ]);

  // Second call returns cached result
  const result2 = await service.getPricing("unit-1", "2024-01-08", "2024-01-09");
  assertEquals(result2.subtotal, result1.subtotal); // Still the original value
  assertEquals(result2.subtotal, 10000);
});

Deno.test("PricingService.updateRates - invalidates cache", async () => {
  const { service, cache } = createService();

  await service.updateRates("unit-1", [
    { startDate: "2024-01-01", endDate: "2024-01-05", baseRate: 10000 },
  ]);

  assertEquals(cache.invalidatedUnits.includes("unit-1"), true);
});

Deno.test("PricingService.generateQuote - returns valid quote", async () => {
  const { service, rateRepo } = createService();

  rateRepo.setRates([
    { unitId: "unit-1", date: "2024-01-08", baseRate: 10000, currency: "USD", rateType: "standard", seasonalMultiplier: 1.0, weekendMultiplier: 1.0, minStay: 1 },
    { unitId: "unit-1", date: "2024-01-09", baseRate: 10000, currency: "USD", rateType: "standard", seasonalMultiplier: 1.0, weekendMultiplier: 1.0, minStay: 1 },
  ]);

  const quote = await service.generateQuote({
    unitId: "unit-1",
    startDate: "2024-01-08",
    endDate: "2024-01-10",
  });

  assertEquals(quote.unitId, "unit-1");
  assertEquals(quote.subtotal, 20000);
  assertEquals(typeof quote.quoteId, "string");
  assertEquals(quote.quoteId.length > 0, true);
  assertEquals(quote.cleaningFee, 7500);
  assertEquals(quote.serviceFee, Math.round(20000 * 0.12));
  assertEquals(typeof quote.grandTotal, "number");
  assertEquals(quote.grandTotal > quote.total, true);
  assertEquals(typeof quote.expiresAt, "string");
});

Deno.test("PricingService.getPricing - combined seasonal and weekend multiplier", async () => {
  const { service, rateRepo } = createService();

  // 2024-01-06 is Saturday
  rateRepo.setRates([
    { unitId: "unit-1", date: "2024-01-06", baseRate: 10000, currency: "USD", rateType: "seasonal", seasonalMultiplier: 1.5, weekendMultiplier: 1.2, minStay: 1 },
  ]);

  const result = await service.getPricing("unit-1", "2024-01-06", "2024-01-07");

  // 10000 * 1.5 = 15000, then 15000 * 1.2 = 18000
  assertEquals(result.nights[0].adjustedRate, 18000);
});
