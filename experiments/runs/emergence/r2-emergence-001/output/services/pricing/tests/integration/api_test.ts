/**
 * API integration tests for the Pricing Service.
 *
 * These tests verify HTTP route handling with mocked services.
 * They test request/response contracts, validation, and error handling.
 */
import {
  assertEquals,
  assertExists,
} from "https://deno.land/std@0.208.0/assert/mod.ts";
import { Application } from "https://deno.land/x/oak@v12.6.1/mod.ts";
import { createPricingRouter } from "../../src/routes/pricing_routes.ts";
import type { PriceQuote, DailyPrice } from "../../src/types.ts";

// ---------- Mock PricingService ----------

class MockPricingService {
  async getPricing(
    _unitId: string,
    _startDate: string,
    _endDate: string,
  ): Promise<DailyPrice[]> {
    return [
      {
        date: "2024-01-08",
        price: 20000,
        isWeekend: false,
        seasonalMultiplier: 1.0,
        minimumStay: 1,
        currency: "USD",
      },
    ];
  }

  async generateQuote(_request: unknown): Promise<PriceQuote> {
    return {
      unitId: "550e8400-e29b-41d4-a716-446655440000",
      checkIn: "2024-01-08",
      checkOut: "2024-01-09",
      nights: 1,
      dailyPrices: [
        {
          date: "2024-01-08",
          price: 20000,
          isWeekend: false,
          seasonalMultiplier: 1.0,
          minimumStay: 1,
          currency: "USD",
        },
      ],
      subtotal: 20000,
      taxes: 2400,
      fees: 8100,
      total: 30500,
      currency: "USD",
      minimumStayMet: true,
      availabilityConfirmed: false,
      quoteExpiresAt: new Date().toISOString(),
    };
  }

  async updateRates(_unitId: string, _rates: unknown[]) {
    return [
      {
        id: "rate-1",
        unitId: "unit-1",
        date: "2024-07-01",
        baseRate: 25000,
        weekendRate: null,
        seasonalMultiplier: 1.0,
        minimumStay: 1,
        currency: "USD",
        createdAt: new Date(),
        updatedAt: new Date(),
      },
    ];
  }
}

// Helper to make test requests
async function makeRequest(
  app: Application,
  method: string,
  path: string,
  body?: unknown,
): Promise<{ status: number; body: unknown }> {
  const controller = new AbortController();
  const port = 18020 + Math.floor(Math.random() * 1000);

  const listenPromise = app.listen({
    hostname: "127.0.0.1",
    port,
    signal: controller.signal,
  });

  // Wait a bit for server to start
  await new Promise((r) => setTimeout(r, 100));

  try {
    const url = `http://127.0.0.1:${port}${path}`;
    const init: RequestInit = {
      method,
      headers: { "Content-Type": "application/json" },
    };
    if (body) {
      init.body = JSON.stringify(body);
    }

    const response = await fetch(url, init);
    const responseBody = await response.json();

    return { status: response.status, body: responseBody };
  } finally {
    controller.abort();
    try {
      await listenPromise;
    } catch {
      // Expected abort error
    }
  }
}

function createTestApp(): Application {
  const mockService = new MockPricingService();
  // deno-lint-ignore no-explicit-any
  const router = createPricingRouter(mockService as any);
  const app = new Application();
  app.use(router.routes());
  app.use(router.allowedMethods());
  return app;
}

Deno.test({
  name: "GET /pricing/:unit_id - returns pricing data",
  async fn() {
    const app = createTestApp();
    const result = await makeRequest(
      app,
      "GET",
      "/pricing/unit-123?start=2024-01-08&end=2024-01-09",
    );

    assertEquals(result.status, 200);
    const body = result.body as Record<string, unknown>;
    assertExists(body.prices);
    assertEquals(body.unitId, "unit-123");
  },
  sanitizeResources: false,
  sanitizeOps: false,
});

Deno.test({
  name: "GET /pricing/:unit_id - validates query parameters",
  async fn() {
    const app = createTestApp();
    const result = await makeRequest(
      app,
      "GET",
      "/pricing/unit-123?start=bad-date&end=2024-01-09",
    );

    assertEquals(result.status, 400);
    const body = result.body as Record<string, unknown>;
    assertEquals(body.error, "Validation error");
  },
  sanitizeResources: false,
  sanitizeOps: false,
});

Deno.test({
  name: "POST /pricing/quote - generates a quote",
  async fn() {
    const app = createTestApp();
    const result = await makeRequest(app, "POST", "/pricing/quote", {
      unitId: "550e8400-e29b-41d4-a716-446655440000",
      checkIn: "2024-01-08",
      checkOut: "2024-01-09",
      guestCount: 2,
    });

    assertEquals(result.status, 200);
    const body = result.body as Record<string, unknown>;
    assertExists(body.subtotal);
    assertExists(body.total);
    assertEquals(body.nights, 1);
  },
  sanitizeResources: false,
  sanitizeOps: false,
});

Deno.test({
  name: "POST /pricing/quote - validates request body",
  async fn() {
    const app = createTestApp();
    const result = await makeRequest(app, "POST", "/pricing/quote", {
      unitId: "not-a-uuid",
      checkIn: "2024-01-08",
      checkOut: "2024-01-09",
    });

    assertEquals(result.status, 400);
    const body = result.body as Record<string, unknown>;
    assertEquals(body.error, "Validation error");
  },
  sanitizeResources: false,
  sanitizeOps: false,
});

Deno.test({
  name: "PUT /pricing/:unit_id/rates - updates rates",
  async fn() {
    const app = createTestApp();
    const result = await makeRequest(
      app,
      "PUT",
      "/pricing/unit-123/rates",
      {
        rates: [
          { date: "2024-07-01", baseRate: 25000 },
        ],
      },
    );

    assertEquals(result.status, 200);
    const body = result.body as Record<string, unknown>;
    assertEquals(body.unitId, "unit-123");
    assertEquals(body.updated, 1);
  },
  sanitizeResources: false,
  sanitizeOps: false,
});

Deno.test({
  name: "PUT /pricing/:unit_id/rates - validates empty rates",
  async fn() {
    const app = createTestApp();
    const result = await makeRequest(
      app,
      "PUT",
      "/pricing/unit-123/rates",
      { rates: [] },
    );

    assertEquals(result.status, 400);
  },
  sanitizeResources: false,
  sanitizeOps: false,
});
