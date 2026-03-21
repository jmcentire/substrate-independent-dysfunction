import { assertEquals } from "https://deno.land/std@0.208.0/assert/mod.ts";
import { Application, Router } from "../../src/deps.ts";
import { BookingService } from "../../src/services/booking_service.ts";
import { BookingHandler } from "../../src/handlers/booking_handler.ts";
import { createRouter } from "../../src/router.ts";
import { createServer } from "../../src/server.ts";
import { InMemoryIdempotencyStore } from "../../src/repositories/idempotency_store.ts";
import type { BookingRepository } from "../../src/repositories/booking_repository.ts";
import type { AvailabilityClient } from "../../src/clients/availability_client.ts";
import type { PricingClient } from "../../src/clients/pricing_client.ts";
import type { PaymentsClient } from "../../src/clients/payments_client.ts";
import type { EventClient } from "../../src/clients/event_client.ts";
import type { Booking, BookingStatus } from "../../src/types.ts";

const validUUID = "550e8400-e29b-41d4-a716-446655440000";

function futureDate(daysFromNow: number): string {
  const d = new Date();
  d.setDate(d.getDate() + daysFromNow);
  return d.toISOString().split("T")[0];
}

// ── In-memory booking repo for integration tests ──────────────────────────
function createInMemoryBookingRepo(): BookingRepository {
  const bookings = new Map<string, Booking>();
  return {
    async create(booking) {
      const full: Booking = { ...booking, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
      bookings.set(full.id, full);
      return full;
    },
    async findById(id) { return bookings.get(id) ?? null; },
    async findByIdempotencyKey(key) {
      for (const b of bookings.values()) {
        if (b.idempotency_key === key) return b;
      }
      return null;
    },
    async list(query) {
      let results = [...bookings.values()];
      if (query.guest_id) results = results.filter(b => b.guest_id === query.guest_id);
      if (query.property_id) results = results.filter(b => b.property_id === query.property_id);
      if (query.status) results = results.filter(b => b.status === query.status);
      return results.slice(query.offset ?? 0, (query.offset ?? 0) + (query.limit ?? 50));
    },
    async updateStatus(id, status, extra) {
      const b = bookings.get(id);
      if (!b) return null;
      const updated = { ...b, status, updated_at: new Date().toISOString(), ...extra };
      bookings.set(id, updated);
      return updated;
    },
  };
}

function createMockClients() {
  return {
    availabilityClient: {
      async checkAvailability(p: string, u: string, ci: string, co: string) {
        return { available: true, property_id: p, unit_id: u, check_in: ci, check_out: co };
      },
      async holdDates(p: string, u: string, ci: string, co: string) {
        return { hold_id: crypto.randomUUID(), property_id: p, unit_id: u, check_in: ci, check_out: co, expires_at: new Date(Date.now() + 600000).toISOString() };
      },
      async releaseDates(_holdId: string) {},
    } as AvailabilityClient,
    pricingClient: {
      async getQuote(p: string, u: string, ci: string, co: string, g: number) {
        return { quote_id: crypto.randomUUID(), property_id: p, unit_id: u, check_in: ci, check_out: co, total_price: 300, currency: "USD", nightly_rates: [] };
      },
    } as PricingClient,
    paymentsClient: {
      async processPayment(bid: string, amount: number, currency: string, _pmid: string, _ik: string) {
        return { payment_id: crypto.randomUUID(), status: "succeeded" as const, amount, currency };
      },
      async refundPayment(_pid: string) {},
    } as PaymentsClient,
    eventClient: {
      async emit(_event: any) {},
    } as EventClient,
  };
}

function createTestApp() {
  const bookingRepo = createInMemoryBookingRepo();
  const idempotencyStore = new InMemoryIdempotencyStore();
  const clients = createMockClients();

  const service = new BookingService({
    bookingRepo,
    idempotencyStore,
    ...clients,
    idempotencyTtlSeconds: 86400,
    totalFlowTimeoutMs: 3000,
  });

  const handler = new BookingHandler(service);
  const router = createRouter(handler);
  const app = createServer(router);

  return { app, service };
}

async function startTestServer(app: Application): Promise<{ port: number; controller: AbortController }> {
  const controller = new AbortController();
  const port = 10000 + Math.floor(Math.random() * 50000);

  const listenPromise = app.listen({
    hostname: "127.0.0.1",
    port,
    signal: controller.signal,
  });

  // Give server time to start
  await new Promise((r) => setTimeout(r, 100));

  return { port, controller };
}

Deno.test("API Integration - health check", async () => {
  const { app } = createTestApp();
  const { port, controller } = await startTestServer(app);

  try {
    const res = await fetch(`http://127.0.0.1:${port}/health`);
    const body = await res.json();
    assertEquals(res.status, 200);
    assertEquals(body.service, "booking");
    assertEquals(body.status, "healthy");
  } finally {
    controller.abort();
    await new Promise((r) => setTimeout(r, 50));
  }
});

Deno.test("API Integration - POST /bookings creates booking", async () => {
  const { app } = createTestApp();
  const { port, controller } = await startTestServer(app);

  try {
    const res = await fetch(`http://127.0.0.1:${port}/bookings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        property_id: validUUID,
        unit_id: validUUID,
        guest_id: validUUID,
        check_in: futureDate(7),
        check_out: futureDate(10),
        guests: 2,
        payment_method_id: validUUID,
      }),
    });

    const body = await res.json();
    assertEquals(res.status, 201);
    assertEquals(body.data.status, "confirmed");
    assertEquals(body.data.total_price, 300);
    assertEquals(typeof body.data.id, "string");
  } finally {
    controller.abort();
    await new Promise((r) => setTimeout(r, 50));
  }
});

Deno.test("API Integration - GET /bookings/:booking_id returns booking", async () => {
  const { app } = createTestApp();
  const { port, controller } = await startTestServer(app);

  try {
    // Create first
    const createRes = await fetch(`http://127.0.0.1:${port}/bookings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        property_id: validUUID,
        unit_id: validUUID,
        guest_id: validUUID,
        check_in: futureDate(7),
        check_out: futureDate(10),
        guests: 2,
        payment_method_id: validUUID,
      }),
    });
    const created = await createRes.json();
    const bookingId = created.data.id;

    // Get
    const getRes = await fetch(`http://127.0.0.1:${port}/bookings/${bookingId}`);
    const body = await getRes.json();
    assertEquals(getRes.status, 200);
    assertEquals(body.data.id, bookingId);
  } finally {
    controller.abort();
    await new Promise((r) => setTimeout(r, 50));
  }
});

Deno.test("API Integration - GET /bookings lists bookings", async () => {
  const { app } = createTestApp();
  const { port, controller } = await startTestServer(app);

  try {
    // Create a booking
    await fetch(`http://127.0.0.1:${port}/bookings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        property_id: validUUID,
        unit_id: validUUID,
        guest_id: validUUID,
        check_in: futureDate(7),
        check_out: futureDate(10),
        guests: 2,
        payment_method_id: validUUID,
      }),
    });

    const listRes = await fetch(`http://127.0.0.1:${port}/bookings?guest_id=${validUUID}`);
    const body = await listRes.json();
    assertEquals(listRes.status, 200);
    assertEquals(Array.isArray(body.data), true);
    assertEquals(body.data.length >= 1, true);
  } finally {
    controller.abort();
    await new Promise((r) => setTimeout(r, 50));
  }
});

Deno.test("API Integration - PUT /bookings/:id/cancel cancels booking", async () => {
  const { app } = createTestApp();
  const { port, controller } = await startTestServer(app);

  try {
    // Create
    const createRes = await fetch(`http://127.0.0.1:${port}/bookings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        property_id: validUUID,
        unit_id: validUUID,
        guest_id: validUUID,
        check_in: futureDate(7),
        check_out: futureDate(10),
        guests: 2,
        payment_method_id: validUUID,
      }),
    });
    const created = await createRes.json();
    const bookingId = created.data.id;

    // Cancel
    const cancelRes = await fetch(
      `http://127.0.0.1:${port}/bookings/${bookingId}/cancel`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "Test cancellation" }),
      },
    );
    const body = await cancelRes.json();
    assertEquals(cancelRes.status, 200);
    assertEquals(body.data.status, "cancelled");
  } finally {
    controller.abort();
    await new Promise((r) => setTimeout(r, 50));
  }
});

Deno.test("API Integration - POST /bookings with invalid body returns 400", async () => {
  const { app } = createTestApp();
  const { port, controller } = await startTestServer(app);

  try {
    const res = await fetch(`http://127.0.0.1:${port}/bookings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ invalid: true }),
    });
    const body = await res.json();
    assertEquals(res.status, 400);
    assertEquals(body.error.code, "VALIDATION_ERROR");
  } finally {
    controller.abort();
    await new Promise((r) => setTimeout(r, 50));
  }
});

Deno.test("API Integration - GET /bookings/:id for missing booking returns 404", async () => {
  const { app } = createTestApp();
  const { port, controller } = await startTestServer(app);

  try {
    const res = await fetch(`http://127.0.0.1:${port}/bookings/nonexistent-id`);
    const body = await res.json();
    assertEquals(res.status, 404);
    assertEquals(body.error.code, "NOT_FOUND");
  } finally {
    controller.abort();
    await new Promise((r) => setTimeout(r, 50));
  }
});

Deno.test("API Integration - booking flow completes under 3 seconds", async () => {
  const { app } = createTestApp();
  const { port, controller } = await startTestServer(app);

  try {
    const start = Date.now();
    const res = await fetch(`http://127.0.0.1:${port}/bookings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        property_id: validUUID,
        unit_id: validUUID,
        guest_id: validUUID,
        check_in: futureDate(7),
        check_out: futureDate(10),
        guests: 2,
        payment_method_id: validUUID,
      }),
    });
    const elapsed = Date.now() - start;
    await res.json();

    assertEquals(res.status, 201);
    assertEquals(elapsed < 3000, true, `Flow took ${elapsed}ms, expected <3000ms`);
  } finally {
    controller.abort();
    await new Promise((r) => setTimeout(r, 50));
  }
});
