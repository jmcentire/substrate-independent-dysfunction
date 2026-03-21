/**
 * Integration tests for the Pricing Service.
 *
 * These tests use mocked dependencies to test the service layer
 * without requiring actual PostgreSQL or Redis connections.
 */
import {
  assertEquals,
  assertStrictEquals,
} from "https://deno.land/std@0.208.0/assert/mod.ts";
import { PricingService } from "../../src/services/pricing_service.ts";
import type { Config } from "../../src/config.ts";
import type { Rate, DailyPrice } from "../../src/types.ts";

// ---------- Mock implementations ----------

class MockRateRepository {
  private rates: Rate[] = [];

  setRates(rates: Rate[]) {
    this.rates = rates;
  }

  async getRates(
    unitId: string,
    startDate: string,
    endDate: string,
  ): Promise<Rate[]> {
    return this.rates.filter(
      (r) =>
        r.unitId === unitId &&
        r.date >= startDate &&
        r.date <= endDate,
    );
  }

  async upsertRates(
    unitId: string,
    rates: { date: string; baseRate: number }[],
  ): Promise<Rate[]> {
    const upserted: Rate[] = rates.map((r, i) => ({
      id: `rate-${i}`,
      unitId,
      date: r.date,
      baseRate: r.baseRate,
      weekendRate: null,
      seasonalMultiplier: 1.0,
      minimumStay: 1,
      currency: "USD",
      createdAt: new Date(),
      updatedAt: new Date(),
    }));
    this.rates = [...this.rates.filter((r) => r.unitId !== unitId), ...upserted];
    return upserted;
  }
}

class MockRedisCache {
  private cache = new Map<string, DailyPrice[]>();
  public invalidateCallCount = 0;

  async getRates(
    unitId: string,
    startDate: string,
    endDate: string,
  ): Promise<DailyPrice[] | null> {
    const key = `${unitId}:${startDate}:${endDate}`;
    return this.cache.get(key) ?? null;
  }

  async setRates(
    unitId: string,
    startDate: string,
    endDate: string,
    prices: DailyPrice[],
  ): Promise<void> {
    const key = `${unitId}:${startDate}:${endDate}`;
    this.cache.set(key, prices);
  }

  async invalidateUnit(_unitId: string): Promise<void> {
    this.invalidateCallCount++;
    this.cache.clear();
  }

  get isConnected() {
    return true;
  }
  get shardCount() {
    return 1;
  }
}

function createTestConfig(): Config {
  return {
    port: 8020,
    hostname: "localhost",
    pgHost: "localhost",
    pgPort: 5432,
    pgUser: "test",
    pgPassword: "test",
    pgDatabase: "test",
    pgPoolSize: 1,
    redisNodes: [{ host: "localhost", port: 6379 }],
    redisTtlSeconds: 3600,
    availabilityServiceUrl: "http://localhost:8010",
    logLevel: "ERROR",
  };
}

// ---------- Tests ----------

Deno.test("PricingService.getPricing - returns prices from DB when cache misses", async () => {
  const repo = new MockRateRepository();
  const cache = new MockRedisCache();
  const config = createTestConfig();

  repo.setRates([
    {
      id: "r1",
      unitId: "unit-1",
      date: "2024-01-08",
      baseRate: 20000,
      weekendRate: null,
      seasonalMultiplier: 1.0,
      minimumStay: 1,
      currency: "USD",
      createdAt: new Date(),
      updatedAt: new Date(),
    },
    {
      id: "r2",
      unitId: "unit-1",
      date: "2024-01-09",
      baseRate: 21000,
      weekendRate: null,
      seasonalMultiplier: 1.0,
      minimumStay: 1,
      currency: "USD",
      createdAt: new Date(),
      updatedAt: new Date(),
    },
  ]);

  // deno-lint-ignore no-explicit-any
  const service = new PricingService(repo as any, cache as any, config);
  const prices = await service.getPricing("unit-1", "2024-01-08", "2024-01-10");

  assertEquals(prices.length, 2);
  assertStrictEquals(prices[0].price, 20000);
  assertStrictEquals(prices[0].date, "2024-01-08");
  assertStrictEquals(prices[1].price, 21000);
  assertStrictEquals(prices[1].date, "2024-01-09");
});

Deno.test("PricingService.getPricing - returns cached prices on cache hit", async () => {
  const repo = new MockRateRepository();
  const cache = new MockRedisCache();
  const config = createTestConfig();

  // Pre-populate cache
  const cachedPrices: DailyPrice[] = [
    {
      date: "2024-01-08",
      price: 99999,
      isWeekend: false,
      seasonalMultiplier: 1.0,
      minimumStay: 1,
      currency: "USD",
    },
  ];
  await cache.setRates("unit-1", "2024-01-08", "2024-01-09", cachedPrices);

  // deno-lint-ignore no-explicit-any
  const service = new PricingService(repo as any, cache as any, config);
  const prices = await service.getPricing("unit-1", "2024-01-08", "2024-01-09");

  assertEquals(prices.length, 1);
  assertStrictEquals(prices[0].price, 99999); // From cache, not DB
});

Deno.test("PricingService.generateQuote - creates valid quote", async () => {
  const repo = new MockRateRepository();
  const cache = new MockRedisCache();
  const config = createTestConfig();

  repo.setRates([
    {
      id: "r1",
      unitId: "550e8400-e29b-41d4-a716-446655440000",
      date: "2024-01-08",
      baseRate: 20000,
      weekendRate: null,
      seasonalMultiplier: 1.0,
      minimumStay: 1,
      currency: "USD",
      createdAt: new Date(),
      updatedAt: new Date(),
    },
    {
      id: "r2",
      unitId: "550e8400-e29b-41d4-a716-446655440000",
      date: "2024-01-09",
      baseRate: 20000,
      weekendRate: null,
      seasonalMultiplier: 1.0,
      minimumStay: 1,
      currency: "USD",
      createdAt: new Date(),
      updatedAt: new Date(),
    },
  ]);

  // deno-lint-ignore no-explicit-any
  const service = new PricingService(repo as any, cache as any, config);
  const quote = await service.generateQuote({
    unitId: "550e8400-e29b-41d4-a716-446655440000",
    checkIn: "2024-01-08",
    checkOut: "2024-01-10",
    guestCount: 2,
  });

  assertStrictEquals(quote.nights, 2);
  assertStrictEquals(quote.subtotal, 40000); // 2 nights * $200
  assertStrictEquals(quote.taxes, 4800); // 12% of 40000
  assertStrictEquals(quote.fees, 8700); // $75 cleaning + 3% of 40000
  assertStrictEquals(quote.total, 53500); // 40000 + 4800 + 8700
  assertStrictEquals(quote.minimumStayMet, true);
  assertStrictEquals(quote.currency, "USD");
  assertEquals(quote.dailyPrices.length, 2);
});

Deno.test("PricingService.generateQuote - detects minimum stay not met", async () => {
  const repo = new MockRateRepository();
  const cache = new MockRedisCache();
  const config = createTestConfig();

  repo.setRates([
    {
      id: "r1",
      unitId: "550e8400-e29b-41d4-a716-446655440000",
      date: "2024-01-08",
      baseRate: 20000,
      weekendRate: null,
      seasonalMultiplier: 1.0,
      minimumStay: 3, // Requires 3 nights minimum
      currency: "USD",
      createdAt: new Date(),
      updatedAt: new Date(),
    },
  ]);

  // deno-lint-ignore no-explicit-any
  const service = new PricingService(repo as any, cache as any, config);
  const quote = await service.generateQuote({
    unitId: "550e8400-e29b-41d4-a716-446655440000",
    checkIn: "2024-01-08",
    checkOut: "2024-01-10", // Only 2 nights
    guestCount: 1,
  });

  assertStrictEquals(quote.minimumStayMet, false);
});

Deno.test("PricingService.updateRates - upserts and invalidates cache", async () => {
  const repo = new MockRateRepository();
  const cache = new MockRedisCache();
  const config = createTestConfig();

  // deno-lint-ignore no-explicit-any
  const service = new PricingService(repo as any, cache as any, config);

  const updated = await service.updateRates("unit-1", [
    { date: "2024-07-01", baseRate: 30000 },
    { date: "2024-07-02", baseRate: 31000 },
  ]);

  assertEquals(updated.length, 2);
  assertStrictEquals(cache.invalidateCallCount, 1);
});

Deno.test("PricingService.getPricing - caches DB results", async () => {
  const repo = new MockRateRepository();
  const cache = new MockRedisCache();
  const config = createTestConfig();

  repo.setRates([
    {
      id: "r1",
      unitId: "unit-2",
      date: "2024-03-01",
      baseRate: 18000,
      weekendRate: null,
      seasonalMultiplier: 1.0,
      minimumStay: 1,
      currency: "USD",
      createdAt: new Date(),
      updatedAt: new Date(),
    },
  ]);

  // deno-lint-ignore no-explicit-any
  const service = new PricingService(repo as any, cache as any, config);

  // First call: DB hit, writes to cache
  await service.getPricing("unit-2", "2024-03-01", "2024-03-02");

  // Second call: should return from cache
  const prices = await service.getPricing("unit-2", "2024-03-01", "2024-03-02");
  assertStrictEquals(prices[0].price, 18000);
});
