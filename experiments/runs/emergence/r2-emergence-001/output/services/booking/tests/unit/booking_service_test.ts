import {
  assertEquals,
  assertRejects,
} from "https://deno.land/std@0.208.0/assert/mod.ts";
import { BookingService } from "../../src/services/booking_service.ts";
import type { BookingRepository } from "../../src/repositories/booking_repository.ts";
import { InMemoryIdempotencyStore } from "../../src/repositories/idempotency_store.ts";
import type { AvailabilityClient } from "../../src/clients/availability_client.ts";
import type { PricingClient } from "../../src/clients/pricing_client.ts";
import type { PaymentsClient } from "../../src/clients/payments_client.ts";
import type { EventClient } from "../../src/clients/event_client.ts";
import {
  type Booking,
  type BookingStatus,
  BookingError,
  ConflictError,
  NotFoundError,
  type CreateBookingRequest,
} from "../../src/types.ts";

const validUUID = "550e8400-e29b-41d4-a716-446655440000";

function futureDate(daysFromNow: number): string {
  const d = new Date();
  d.setDate(d.getDate() + daysFromNow);
  return d.toISOString().split("T")[0];
}

function createMockBookingRepo(): BookingRepository & { bookings: Map<string, Booking> } {
  const bookings = new Map<string, Booking>();

  return {
    bookings,
    async create(booking) {
      const full: Booking = {
        ...booking,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      bookings.set(full.id, full);
      return full;
    },
    async findById(id) {
      return bookings.get(id) ?? null;
    },
    async findByIdempotencyKey(key) {
      for (const b of bookings.values()) {
        if (b.idempotency_key === key) return b;
      }
      return null;
    },
    async list(query) {
      let results = [...bookings.values()];
      if (query.guest_id) results = results.filter((b) => b.guest_id === query.guest_id);
      if (query.property_id) results = results.filter((b) => b.property_id === query.property_id);
      if (query.status) results = results.filter((b) => b.status === query.status);
      return results.slice(query.offset ?? 0, (query.offset ?? 0) + (query.limit ?? 50));
    },
    async updateStatus(id, status, extra) {
      const booking = bookings.get(id);
      if (!booking) return null;
      const updated = {
        ...booking,
        status,
        updated_at: new Date().toISOString(),
        ...extra,
      };
      bookings.set(id, updated);
      return updated;
    },
  };
}

function createMockAvailabilityClient(available = true): AvailabilityClient & { releaseCalled: string[] } {
  const releaseCalled: string[] = [];
  return {
    releaseCalled,
    async checkAvailability(propertyId, unitId, checkIn, checkOut) {
      return { available, property_id: propertyId, unit_id: unitId, check_in: checkIn, check_out: checkOut };
    },
    async holdDates(propertyId, unitId, checkIn, checkOut) {
      return {
        hold_id: "hold-" + crypto.randomUUID(),
        property_id: propertyId,
        unit_id: unitId,
        check_in: checkIn,
        check_out: checkOut,
        expires_at: new Date(Date.now() + 600000).toISOString(),
      };
    },
    async releaseDates(holdId) {
      releaseCalled.push(holdId);
    },
  };
}

function createMockPricingClient(): PricingClient {
  return {
    async getQuote(propertyId, unitId, checkIn, checkOut, guests) {
      return {
        quote_id: "quote-" + crypto.randomUUID(),
        property_id: propertyId,
        unit_id: unitId,
        check_in: checkIn,
        check_out: checkOut,
        total_price: 450.00,
        currency: "USD",
        nightly_rates: [{ date: checkIn, amount: 150.00 }],
      };
    },
  };
}

function createMockPaymentsClient(status: "succeeded" | "failed" = "succeeded"): PaymentsClient & { refundCalled: string[] } {
  const refundCalled: string[] = [];
  return {
    refundCalled,
    async processPayment(bookingId, amount, currency, paymentMethodId, idempotencyKey) {
      return {
        payment_id: "pay-" + crypto.randomUUID(),
        status,
        amount,
        currency,
      };
    },
    async refundPayment(paymentId) {
      refundCalled.push(paymentId);
    },
  };
}

function createMockEventClient(): EventClient & { events: Array<{ type: string }> } {
  const events: Array<{ type: string; source: string; data: Record<string, unknown> }> = [];
  return {
    events,
    async emit(event) {
      events.push(event);
    },
  };
}

function createService(overrides: {
  bookingRepo?: ReturnType<typeof createMockBookingRepo>;
  idempotencyStore?: InMemoryIdempotencyStore;
  availabilityClient?: ReturnType<typeof createMockAvailabilityClient>;
  pricingClient?: PricingClient;
  paymentsClient?: ReturnType<typeof createMockPaymentsClient>;
  eventClient?: ReturnType<typeof createMockEventClient>;
} = {}) {
  const bookingRepo = overrides.bookingRepo ?? createMockBookingRepo();
  const idempotencyStore = overrides.idempotencyStore ?? new InMemoryIdempotencyStore();
  const availabilityClient = overrides.availabilityClient ?? createMockAvailabilityClient();
  const pricingClient = overrides.pricingClient ?? createMockPricingClient();
  const paymentsClient = overrides.paymentsClient ?? createMockPaymentsClient();
  const eventClient = overrides.eventClient ?? createMockEventClient();

  const service = new BookingService({
    bookingRepo,
    idempotencyStore,
    availabilityClient,
    pricingClient,
    paymentsClient,
    eventClient,
    idempotencyTtlSeconds: 86400,
    totalFlowTimeoutMs: 3000,
  });

  return { service, bookingRepo, idempotencyStore, availabilityClient, pricingClient, paymentsClient, eventClient };
}

const validRequest: CreateBookingRequest = {
  property_id: validUUID,
  unit_id: validUUID,
  guest_id: validUUID,
  check_in: futureDate(7),
  check_out: futureDate(10),
  guests: 2,
  payment_method_id: validUUID,
};

// ── Happy Path ─────────────────────────────────────────────────────────────

Deno.test("createBooking - happy path creates booking", async () => {
  const { service, bookingRepo, eventClient } = createService();
  const result = await service.createBooking(validRequest);

  assertEquals(result.status, "confirmed");
  assertEquals(result.property_id, validUUID);
  assertEquals(result.total_price, 450.00);
  assertEquals(result.currency, "USD");
  assertEquals(result.guests, 2);
  assertEquals(typeof result.id, "string");
  assertEquals(typeof result.payment_id, "string");

  // Should be persisted
  assertEquals(bookingRepo.bookings.size, 1);

  // Wait for event emission
  await new Promise((r) => setTimeout(r, 50));
  assertEquals(eventClient.events.length, 1);
  assertEquals(eventClient.events[0].type, "booking.created");
});

// ── Availability Not Available ─────────────────────────────────────────────

Deno.test("createBooking - fails when dates not available", async () => {
  const availabilityClient = createMockAvailabilityClient(false);
  const { service } = createService({ availabilityClient });

  await assertRejects(
    () => service.createBooking(validRequest),
    BookingError,
    "not available",
  );
});

// ── Payment Failure → Compensate ───────────────────────────────────────────

Deno.test("createBooking - compensates when payment fails", async () => {
  const paymentsClient = createMockPaymentsClient("failed");
  const availabilityClient = createMockAvailabilityClient();
  const { service } = createService({ paymentsClient, availabilityClient });

  await assertRejects(
    () => service.createBooking(validRequest),
    BookingError,
    "Payment processing failed",
  );

  // Hold should have been released
  assertEquals(availabilityClient.releaseCalled.length, 1);
});

// ── Idempotency ────────────────────────────────────────────────────────────

Deno.test("createBooking - idempotency returns same booking", async () => {
  const { service } = createService();

  const requestWithKey = {
    ...validRequest,
    idempotency_key: "my-unique-key",
  };

  const first = await service.createBooking(requestWithKey);
  const second = await service.createBooking(requestWithKey);

  assertEquals(first.id, second.id);
});

// ── Get Booking ────────────────────────────────────────────────────────────

Deno.test("getBooking - returns existing booking", async () => {
  const { service } = createService();
  const created = await service.createBooking(validRequest);
  const fetched = await service.getBooking(created.id);
  assertEquals(fetched.id, created.id);
});

Deno.test("getBooking - throws NotFoundError for missing booking", async () => {
  const { service } = createService();
  await assertRejects(
    () => service.getBooking("nonexistent-id"),
    NotFoundError,
  );
});

// ── List Bookings ──────────────────────────────────────────────────────────

Deno.test("listBookings - returns filtered results", async () => {
  const { service } = createService();
  await service.createBooking(validRequest);
  await service.createBooking({
    ...validRequest,
    guest_id: "660e8400-e29b-41d4-a716-446655440000",
  });

  const all = await service.listBookings({});
  assertEquals(all.length, 2);

  const filtered = await service.listBookings({ guest_id: validUUID });
  assertEquals(filtered.length, 1);
});

// ── Cancel Booking ─────────────────────────────────────────────────────────

Deno.test("cancelBooking - cancels a confirmed booking", async () => {
  const { service, eventClient } = createService();
  const created = await service.createBooking(validRequest);

  const cancelled = await service.cancelBooking(created.id, {
    reason: "Changed plans",
  });

  assertEquals(cancelled.status, "cancelled");

  // Wait for events
  await new Promise((r) => setTimeout(r, 50));
  const cancelEvent = eventClient.events.find((e) => e.type === "booking.cancelled");
  assertEquals(cancelEvent !== undefined, true);
});

Deno.test("cancelBooking - cannot cancel already cancelled booking", async () => {
  const { service } = createService();
  const created = await service.createBooking(validRequest);
  await service.cancelBooking(created.id, {});

  await assertRejects(
    () => service.cancelBooking(created.id, {}),
    BookingError,
    "Cannot transition",
  );
});

// ── Confirm Booking ────────────────────────────────────────────────────────

Deno.test("confirmBooking - throws for already confirmed", async () => {
  const { service } = createService();
  const created = await service.createBooking(validRequest);

  // Already confirmed from the flow
  await assertRejects(
    () => service.confirmBooking(created.id),
    BookingError,
    "Cannot transition",
  );
});

// ── State Transitions ──────────────────────────────────────────────────────

Deno.test("state transitions - valid transitions work", async () => {
  const bookingRepo = createMockBookingRepo();

  // Manually create a pending booking
  const booking: Booking = {
    id: "test-booking-1",
    property_id: validUUID,
    unit_id: validUUID,
    guest_id: validUUID,
    check_in: futureDate(7),
    check_out: futureDate(10),
    status: "pending",
    total_price: 300,
    currency: "USD",
    guests: 2,
    payment_id: null,
    idempotency_key: null,
    cancelled_at: null,
    cancellation_reason: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
  bookingRepo.bookings.set(booking.id, booking);

  const { service } = createService({ bookingRepo });

  // pending → confirmed
  const confirmed = await service.confirmBooking("test-booking-1");
  assertEquals(confirmed.status, "confirmed");

  // confirmed → cancelled
  const cancelled = await service.cancelBooking("test-booking-1", { reason: "test" });
  assertEquals(cancelled.status, "cancelled");
});
