import { assertEquals } from "../../deps.ts";
import { validatePublishRequest, validateSubscribeRequest, validatePayloadForTopic } from "../../src/validator.ts";

// --- validatePublishRequest ---

Deno.test("validatePublishRequest - valid request", () => {
  const result = validatePublishRequest({
    topic: "booking.created",
    payload: { booking_id: "b123" },
    source: "booking-service",
  });
  assertEquals(result.valid, true);
  assertEquals(result.errors.length, 0);
});

Deno.test("validatePublishRequest - missing topic", () => {
  const result = validatePublishRequest({
    payload: { booking_id: "b123" },
    source: "booking-service",
  });
  assertEquals(result.valid, false);
  assertEquals(result.errors.length >= 1, true);
});

Deno.test("validatePublishRequest - invalid topic", () => {
  const result = validatePublishRequest({
    topic: "invalid.topic",
    payload: { booking_id: "b123" },
    source: "booking-service",
  });
  assertEquals(result.valid, false);
  assertEquals(result.errors[0].includes("Invalid topic"), true);
});

Deno.test("validatePublishRequest - missing payload", () => {
  const result = validatePublishRequest({
    topic: "booking.created",
    source: "booking-service",
  });
  assertEquals(result.valid, false);
});

Deno.test("validatePublishRequest - missing source", () => {
  const result = validatePublishRequest({
    topic: "booking.created",
    payload: { booking_id: "b123" },
  });
  assertEquals(result.valid, false);
});

Deno.test("validatePublishRequest - null body", () => {
  const result = validatePublishRequest(null);
  assertEquals(result.valid, false);
  assertEquals(result.errors[0], "Request body must be a JSON object");
});

Deno.test("validatePublishRequest - valid with timestamp", () => {
  const result = validatePublishRequest({
    topic: "booking.created",
    payload: { booking_id: "b123" },
    source: "booking-service",
    timestamp: "2024-01-15T10:00:00.000Z",
  });
  assertEquals(result.valid, true);
});

Deno.test("validatePublishRequest - invalid timestamp", () => {
  const result = validatePublishRequest({
    topic: "booking.created",
    payload: { booking_id: "b123" },
    source: "booking-service",
    timestamp: "not-a-date",
  });
  assertEquals(result.valid, false);
});

Deno.test("validatePublishRequest - array payload rejected", () => {
  const result = validatePublishRequest({
    topic: "booking.created",
    payload: [1, 2, 3],
    source: "booking-service",
  });
  assertEquals(result.valid, false);
});

// --- validateSubscribeRequest ---

Deno.test("validateSubscribeRequest - valid request", () => {
  const result = validateSubscribeRequest({
    topic: "booking.created",
    webhook_url: "https://example.com/webhook",
  });
  assertEquals(result.valid, true);
});

Deno.test("validateSubscribeRequest - valid with secret", () => {
  const result = validateSubscribeRequest({
    topic: "booking.created",
    webhook_url: "https://example.com/webhook",
    secret: "my-secret",
  });
  assertEquals(result.valid, true);
});

Deno.test("validateSubscribeRequest - invalid URL", () => {
  const result = validateSubscribeRequest({
    topic: "booking.created",
    webhook_url: "not-a-url",
  });
  assertEquals(result.valid, false);
});

Deno.test("validateSubscribeRequest - missing topic", () => {
  const result = validateSubscribeRequest({
    webhook_url: "https://example.com/webhook",
  });
  assertEquals(result.valid, false);
});

Deno.test("validateSubscribeRequest - null body", () => {
  const result = validateSubscribeRequest(null);
  assertEquals(result.valid, false);
});

// --- validatePayloadForTopic ---

Deno.test("validatePayloadForTopic - booking.created requires booking_id", () => {
  const valid = validatePayloadForTopic("booking.created", { booking_id: "b123" });
  assertEquals(valid.valid, true);

  const invalid = validatePayloadForTopic("booking.created", { foo: "bar" });
  assertEquals(invalid.valid, false);
});

Deno.test("validatePayloadForTopic - availability.updated requires property_id", () => {
  const valid = validatePayloadForTopic("availability.updated", { property_id: "p123" });
  assertEquals(valid.valid, true);

  const invalid = validatePayloadForTopic("availability.updated", {});
  assertEquals(invalid.valid, false);
});

Deno.test("validatePayloadForTopic - payment.processed accepts payment_id or booking_id", () => {
  const v1 = validatePayloadForTopic("payment.processed", { payment_id: "pay1" });
  assertEquals(v1.valid, true);

  const v2 = validatePayloadForTopic("payment.processed", { booking_id: "b1" });
  assertEquals(v2.valid, true);

  const invalid = validatePayloadForTopic("payment.processed", { foo: "bar" });
  assertEquals(invalid.valid, false);
});
