import { assertEquals } from "https://deno.land/std@0.208.0/assert/mod.ts";
import {
  BookingError,
  ConflictError,
  NotFoundError,
  ServiceUnavailableError,
  TimeoutError,
  VALID_TRANSITIONS,
} from "../../src/types.ts";

Deno.test("BookingError - has correct defaults", () => {
  const err = new BookingError("test error");
  assertEquals(err.message, "test error");
  assertEquals(err.statusCode, 400);
  assertEquals(err.code, "BOOKING_ERROR");
  assertEquals(err.name, "BookingError");
});

Deno.test("ConflictError - has status 409", () => {
  const err = new ConflictError("conflict");
  assertEquals(err.statusCode, 409);
  assertEquals(err.code, "CONFLICT");
});

Deno.test("NotFoundError - has status 404", () => {
  const err = new NotFoundError("not found");
  assertEquals(err.statusCode, 404);
  assertEquals(err.code, "NOT_FOUND");
});

Deno.test("ServiceUnavailableError - has status 503", () => {
  const err = new ServiceUnavailableError("unavailable");
  assertEquals(err.statusCode, 503);
});

Deno.test("TimeoutError - has status 504", () => {
  const err = new TimeoutError("timeout");
  assertEquals(err.statusCode, 504);
});

Deno.test("VALID_TRANSITIONS - pending can go to confirmed or cancelled", () => {
  assertEquals(VALID_TRANSITIONS.pending, ["confirmed", "cancelled"]);
});

Deno.test("VALID_TRANSITIONS - confirmed can go to checked_in or cancelled", () => {
  assertEquals(VALID_TRANSITIONS.confirmed, ["checked_in", "cancelled"]);
});

Deno.test("VALID_TRANSITIONS - checked_in can only go to checked_out", () => {
  assertEquals(VALID_TRANSITIONS.checked_in, ["checked_out"]);
});

Deno.test("VALID_TRANSITIONS - checked_out and cancelled are terminal", () => {
  assertEquals(VALID_TRANSITIONS.checked_out, []);
  assertEquals(VALID_TRANSITIONS.cancelled, []);
});
