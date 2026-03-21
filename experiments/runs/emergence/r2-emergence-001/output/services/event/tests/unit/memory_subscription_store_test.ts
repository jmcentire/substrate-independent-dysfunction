import { assertEquals, assertExists } from "../../deps.ts";
import { MemorySubscriptionStore } from "../../src/store/memory_subscription_store.ts";

Deno.test("MemorySubscriptionStore - create subscription", async () => {
  const store = new MemorySubscriptionStore();

  const sub = await store.create({
    event_type: "booking.created",
    webhook_url: "https://example.com/webhook",
  });

  assertExists(sub.subscription_id);
  assertEquals(sub.event_type, "booking.created");
  assertEquals(sub.webhook_url, "https://example.com/webhook");
  assertEquals(sub.active, true);
  assertEquals(sub.failure_count, 0);

  await store.close();
});

Deno.test("MemorySubscriptionStore - idempotent create (same type + url)", async () => {
  const store = new MemorySubscriptionStore();

  const sub1 = await store.create({
    event_type: "booking.created",
    webhook_url: "https://example.com/webhook",
  });

  const sub2 = await store.create({
    event_type: "booking.created",
    webhook_url: "https://example.com/webhook",
  });

  assertEquals(sub1.subscription_id, sub2.subscription_id);
  assertEquals(store.size, 1);

  await store.close();
});

Deno.test("MemorySubscriptionStore - different URLs create different subs", async () => {
  const store = new MemorySubscriptionStore();

  const sub1 = await store.create({
    event_type: "booking.created",
    webhook_url: "https://example.com/webhook1",
  });

  const sub2 = await store.create({
    event_type: "booking.created",
    webhook_url: "https://example.com/webhook2",
  });

  assertEquals(sub1.subscription_id !== sub2.subscription_id, true);
  assertEquals(store.size, 2);

  await store.close();
});

Deno.test("MemorySubscriptionStore - getByEventType", async () => {
  const store = new MemorySubscriptionStore();

  await store.create({ event_type: "booking.created", webhook_url: "https://a.com" });
  await store.create({ event_type: "booking.confirmed", webhook_url: "https://b.com" });
  await store.create({ event_type: "booking.created", webhook_url: "https://c.com" });

  const results = await store.getByEventType("booking.created");
  assertEquals(results.length, 2);

  await store.close();
});

Deno.test("MemorySubscriptionStore - getById", async () => {
  const store = new MemorySubscriptionStore();

  const sub = await store.create({
    event_type: "booking.created",
    webhook_url: "https://example.com/webhook",
  });

  const found = await store.getById(sub.subscription_id);
  assertExists(found);
  assertEquals(found!.subscription_id, sub.subscription_id);

  const missing = await store.getById("nonexistent");
  assertEquals(missing, null);

  await store.close();
});

Deno.test("MemorySubscriptionStore - deactivate", async () => {
  const store = new MemorySubscriptionStore();

  const sub = await store.create({
    event_type: "booking.created",
    webhook_url: "https://example.com/webhook",
  });

  const result = await store.deactivate(sub.subscription_id);
  assertEquals(result, true);

  const found = await store.getById(sub.subscription_id);
  assertEquals(found!.active, false);

  // Should not appear in getByEventType (only active)
  const active = await store.getByEventType("booking.created");
  assertEquals(active.length, 0);

  await store.close();
});

Deno.test("MemorySubscriptionStore - updateDelivery success resets failure count", async () => {
  const store = new MemorySubscriptionStore();

  const sub = await store.create({
    event_type: "booking.created",
    webhook_url: "https://example.com/webhook",
  });

  await store.updateDelivery(sub.subscription_id, false);
  await store.updateDelivery(sub.subscription_id, false);

  let found = await store.getById(sub.subscription_id);
  assertEquals(found!.failure_count, 2);

  await store.updateDelivery(sub.subscription_id, true);
  found = await store.getById(sub.subscription_id);
  assertEquals(found!.failure_count, 0);
  assertExists(found!.last_delivery);

  await store.close();
});
