import { assertEquals } from "../../deps.ts";
import { canonicalJson, computeEventId } from "../../src/hash.ts";

Deno.test("canonicalJson - sorts object keys", () => {
  const result = canonicalJson({ z: 1, a: 2, m: 3 });
  assertEquals(result, '{"a":2,"m":3,"z":1}');
});

Deno.test("canonicalJson - handles nested objects", () => {
  const result = canonicalJson({ b: { z: 1, a: 2 }, a: 3 });
  assertEquals(result, '{"a":3,"b":{"a":2,"z":1}}');
});

Deno.test("canonicalJson - handles arrays", () => {
  const result = canonicalJson({ items: [3, 1, 2] });
  assertEquals(result, '{"items":[3,1,2]}');
});

Deno.test("canonicalJson - handles strings", () => {
  const result = canonicalJson({ name: "hello world" });
  assertEquals(result, '{"name":"hello world"}');
});

Deno.test("canonicalJson - handles null", () => {
  const result = canonicalJson({ a: null });
  assertEquals(result, '{"a":null}');
});

Deno.test("canonicalJson - handles booleans", () => {
  const result = canonicalJson({ yes: true, no: false });
  assertEquals(result, '{"no":false,"yes":true}');
});

Deno.test("canonicalJson - handles empty object", () => {
  const result = canonicalJson({});
  assertEquals(result, "{}" );
});

Deno.test("canonicalJson - handles empty array", () => {
  const result = canonicalJson([]);
  assertEquals(result, "[]");
});

Deno.test("canonicalJson - deterministic output for same input", () => {
  const obj = { event_type: "booking.created", payload: { booking_id: "123", amount: 100 }, source: "test" };
  const r1 = canonicalJson(obj);
  const r2 = canonicalJson(obj);
  assertEquals(r1, r2);
});

Deno.test("canonicalJson - same output regardless of key insertion order", () => {
  const obj1 = { a: 1, b: 2, c: 3 };
  const obj2 = { c: 3, a: 1, b: 2 };
  assertEquals(canonicalJson(obj1), canonicalJson(obj2));
});

Deno.test("computeEventId - produces 64-char hex string", async () => {
  const id = await computeEventId("booking.created", "test-service", { booking_id: "123" });
  assertEquals(id.length, 64);
  assertEquals(/^[a-f0-9]{64}$/.test(id), true);
});

Deno.test("computeEventId - deterministic for same inputs", async () => {
  const id1 = await computeEventId("booking.created", "test", { booking_id: "123" });
  const id2 = await computeEventId("booking.created", "test", { booking_id: "123" });
  assertEquals(id1, id2);
});

Deno.test("computeEventId - different for different payloads", async () => {
  const id1 = await computeEventId("booking.created", "test", { booking_id: "123" });
  const id2 = await computeEventId("booking.created", "test", { booking_id: "456" });
  // Should be different
  assertEquals(id1 !== id2, true);
});

Deno.test("computeEventId - different for different event types", async () => {
  const id1 = await computeEventId("booking.created", "test", { booking_id: "123" });
  const id2 = await computeEventId("booking.confirmed", "test", { booking_id: "123" });
  assertEquals(id1 !== id2, true);
});

Deno.test("computeEventId - different for different sources", async () => {
  const id1 = await computeEventId("booking.created", "service-a", { booking_id: "123" });
  const id2 = await computeEventId("booking.created", "service-b", { booking_id: "123" });
  assertEquals(id1 !== id2, true);
});

Deno.test("computeEventId - key order doesn't matter in payload", async () => {
  const id1 = await computeEventId("booking.created", "test", { a: 1, b: 2 });
  const id2 = await computeEventId("booking.created", "test", { b: 2, a: 1 });
  assertEquals(id1, id2);
});
