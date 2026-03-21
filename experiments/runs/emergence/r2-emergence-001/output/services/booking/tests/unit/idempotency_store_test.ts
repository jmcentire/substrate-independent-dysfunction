import { assertEquals } from "https://deno.land/std@0.208.0/assert/mod.ts";
import { InMemoryIdempotencyStore } from "../../src/repositories/idempotency_store.ts";

Deno.test("InMemoryIdempotencyStore - tryAcquire returns true first time", async () => {
  const store = new InMemoryIdempotencyStore();
  const result = await store.tryAcquire("key1", "booking-1", 3600);
  assertEquals(result, true);
});

Deno.test("InMemoryIdempotencyStore - tryAcquire returns false for duplicate", async () => {
  const store = new InMemoryIdempotencyStore();
  await store.tryAcquire("key1", "booking-1", 3600);
  const result = await store.tryAcquire("key1", "booking-2", 3600);
  assertEquals(result, false);
});

Deno.test("InMemoryIdempotencyStore - getBookingId returns stored ID", async () => {
  const store = new InMemoryIdempotencyStore();
  await store.tryAcquire("key1", "booking-1", 3600);
  const id = await store.getBookingId("key1");
  assertEquals(id, "booking-1");
});

Deno.test("InMemoryIdempotencyStore - getBookingId returns null for unknown key", async () => {
  const store = new InMemoryIdempotencyStore();
  const id = await store.getBookingId("unknown");
  assertEquals(id, null);
});

Deno.test("InMemoryIdempotencyStore - release allows re-acquire", async () => {
  const store = new InMemoryIdempotencyStore();
  await store.tryAcquire("key1", "booking-1", 3600);
  await store.release("key1");
  const result = await store.tryAcquire("key1", "booking-2", 3600);
  assertEquals(result, true);
});

Deno.test("InMemoryIdempotencyStore - clear removes all keys", async () => {
  const store = new InMemoryIdempotencyStore();
  await store.tryAcquire("key1", "booking-1", 3600);
  await store.tryAcquire("key2", "booking-2", 3600);
  store.clear();
  const r1 = await store.tryAcquire("key1", "booking-3", 3600);
  const r2 = await store.tryAcquire("key2", "booking-4", 3600);
  assertEquals(r1, true);
  assertEquals(r2, true);
});
