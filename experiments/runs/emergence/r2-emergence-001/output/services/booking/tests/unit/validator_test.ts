import { assertEquals, assertThrows } from "https://deno.land/std@0.208.0/assert/mod.ts";
import { validateCreateBookingRequest, validateBookingStatus } from "../../src/services/validator.ts";
import { BookingError } from "../../src/types.ts";

const validUUID = "550e8400-e29b-41d4-a716-446655440000";

function futureDate(daysFromNow: number): string {
  const d = new Date();
  d.setDate(d.getDate() + daysFromNow);
  return d.toISOString().split("T")[0];
}

const validRequest = {
  property_id: validUUID,
  unit_id: validUUID,
  guest_id: validUUID,
  check_in: futureDate(7),
  check_out: futureDate(10),
  guests: 2,
  payment_method_id: validUUID,
};

Deno.test("validator - accepts valid request", () => {
  const result = validateCreateBookingRequest(validRequest);
  assertEquals(result.property_id, validUUID);
  assertEquals(result.guests, 2);
});

Deno.test("validator - rejects null body", () => {
  assertThrows(
    () => validateCreateBookingRequest(null),
    BookingError,
    "Request body is required",
  );
});

Deno.test("validator - rejects missing required field", () => {
  const { property_id: _, ...incomplete } = validRequest;
  assertThrows(
    () => validateCreateBookingRequest(incomplete),
    BookingError,
    "Missing required field: property_id",
  );
});

Deno.test("validator - rejects invalid UUID", () => {
  assertThrows(
    () => validateCreateBookingRequest({ ...validRequest, property_id: "not-a-uuid" }),
    BookingError,
    "Invalid UUID format",
  );
});

Deno.test("validator - rejects invalid date format", () => {
  assertThrows(
    () => validateCreateBookingRequest({ ...validRequest, check_in: "2024/01/01" }),
    BookingError,
    "Invalid date format",
  );
});

Deno.test("validator - rejects check_in >= check_out", () => {
  assertThrows(
    () =>
      validateCreateBookingRequest({
        ...validRequest,
        check_in: futureDate(10),
        check_out: futureDate(7),
      }),
    BookingError,
    "check_in must be before check_out",
  );
});

Deno.test("validator - rejects past check_in", () => {
  assertThrows(
    () =>
      validateCreateBookingRequest({
        ...validRequest,
        check_in: "2020-01-01",
        check_out: "2020-01-05",
      }),
    BookingError,
    "check_in cannot be in the past",
  );
});

Deno.test("validator - rejects zero guests", () => {
  assertThrows(
    () => validateCreateBookingRequest({ ...validRequest, guests: 0 }),
    BookingError,
    "guests must be a positive number",
  );
});

Deno.test("validator - accepts optional idempotency_key", () => {
  const result = validateCreateBookingRequest({
    ...validRequest,
    idempotency_key: "my-key-123",
  });
  assertEquals(result.idempotency_key, "my-key-123");
});

Deno.test("validateBookingStatus - accepts valid statuses", () => {
  assertEquals(validateBookingStatus("pending"), "pending");
  assertEquals(validateBookingStatus("confirmed"), "confirmed");
  assertEquals(validateBookingStatus("cancelled"), "cancelled");
});

Deno.test("validateBookingStatus - rejects invalid status", () => {
  assertThrows(
    () => validateBookingStatus("invalid"),
    BookingError,
    "Invalid status",
  );
});
