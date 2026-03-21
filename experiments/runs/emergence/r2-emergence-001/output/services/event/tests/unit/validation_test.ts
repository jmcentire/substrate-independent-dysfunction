import { assertEquals, assertThrows } from "../../deps.ts";
import {
  validatePublishRequest,
  validateSubscribeRequest,
  ValidationError,
  isValidEventType,
} from "../../src/validation.ts";

// --- isValidEventType ---

Deno.test("isValidEventType - accepts valid types", () => {
  assertEquals(isValidEventType("booking.created"), true);
  assertEquals(isValidEventType("availability.updated"), true);
  assertEquals(isValidEventType("payment.processed"), true);
});

Deno.test("isValidEventType - rejects invalid types", () => {
  assertEquals(isValidEventType("invalid.type"), false);
  assertEquals(isValidEventType(""), false);
  assertEquals(isValidEventType("booking"), false);
});

// --- validatePublishRequest ---

Deno.test("validatePublishRequest - valid booking.created", () => {
  const result = validatePublishRequest({
    event_type: "booking.created",
    source: "booking-service",
    payload: { booking_id: "b123", property_id: "p456" },
  });
  assertEquals(result.event_type, "booking.created");
  assertEquals(result.source, "booking-service");
  assertEquals(result.payload.booking_id, "b123");
});

Deno.test("validatePublishRequest - valid availability.updated", () => {
  const result = validatePublishRequest({
    event_type: "availability.updated",
    source: "availability-service",
    payload: { property_id: "p123" },
  });
  assertEquals(result.event_type, "availability.updated");
});

Deno.test("validatePublishRequest - rejects missing body", () => {
  assertThrows(
    () => validatePublishRequest(null),
    ValidationError,
    "Request body must be a JSON object"
  );
});

Deno.test("validatePublishRequest - rejects missing event_type", () => {
  assertThrows(
    () => validatePublishRequest({ source: "test", payload: {} }),
    ValidationError,
    "event_type is required"
  );
});

Deno.test("validatePublishRequest - rejects invalid event_type", () => {
  assertThrows(
    () => validatePublishRequest({ event_type: "bad.type", source: "test", payload: {} }),
    ValidationError,
    "Invalid event_type"
  );
});

Deno.test("validatePublishRequest - rejects missing source", () => {
  assertThrows(
    () => validatePublishRequest({ event_type: "booking.created", payload: { booking_id: "1", property_id: "2" } }),
    ValidationError,
    "source is required"
  );
});

Deno.test("validatePublishRequest - rejects missing payload", () => {
  assertThrows(
    () => validatePublishRequest({ event_type: "booking.created", source: "test" }),
    ValidationError,
    "payload is required"
  );
});

Deno.test("validatePublishRequest - rejects array payload", () => {
  assertThrows(
    () => validatePublishRequest({ event_type: "booking.created", source: "test", payload: [] }),
    ValidationError,
    "payload is required and must be a JSON object"
  );
});

Deno.test("validatePublishRequest - rejects booking.created without booking_id", () => {
  assertThrows(
    () => validatePublishRequest({
      event_type: "booking.created",
      source: "test",
      payload: { property_id: "p1" },
    }),
    ValidationError,
    "requires 'booking_id'"
  );
});

Deno.test("validatePublishRequest - rejects booking.created without property_id", () => {
  assertThrows(
    () => validatePublishRequest({
      event_type: "booking.created",
      source: "test",
      payload: { booking_id: "b1" },
    }),
    ValidationError,
    "requires 'property_id'"
  );
});

Deno.test("validatePublishRequest - accepts optional metadata", () => {
  const result = validatePublishRequest({
    event_type: "sync.completed",
    source: "sync-service",
    payload: { sync_id: "s1" },
    metadata: { correlation_id: "abc" },
  });
  assertEquals(result.metadata?.correlation_id, "abc");
});

Deno.test("validatePublishRequest - rejects array metadata", () => {
  assertThrows(
    () => validatePublishRequest({
      event_type: "sync.completed",
      source: "sync-service",
      payload: { sync_id: "s1" },
      metadata: [],
    }),
    ValidationError,
    "metadata must be a JSON object"
  );
});

// --- validateSubscribeRequest ---

Deno.test("validateSubscribeRequest - valid request", () => {
  const result = validateSubscribeRequest({
    event_type: "booking.created",
    webhook_url: "https://example.com/webhook",
  });
  assertEquals(result.event_type, "booking.created");
  assertEquals(result.webhook_url, "https://example.com/webhook");
});

Deno.test("validateSubscribeRequest - with secret", () => {
  const result = validateSubscribeRequest({
    event_type: "booking.created",
    webhook_url: "https://example.com/webhook",
    secret: "my-secret",
  });
  assertEquals(result.secret, "my-secret");
});

Deno.test("validateSubscribeRequest - rejects invalid url", () => {
  assertThrows(
    () => validateSubscribeRequest({
      event_type: "booking.created",
      webhook_url: "not-a-url",
    }),
    ValidationError,
    "webhook_url must be a valid URL"
  );
});

Deno.test("validateSubscribeRequest - rejects missing event_type", () => {
  assertThrows(
    () => validateSubscribeRequest({
      webhook_url: "https://example.com/webhook",
    }),
    ValidationError,
    "event_type is required"
  );
});

Deno.test("validateSubscribeRequest - rejects missing webhook_url", () => {
  assertThrows(
    () => validateSubscribeRequest({
      event_type: "booking.created",
    }),
    ValidationError,
    "webhook_url is required"
  );
});
