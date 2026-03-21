import { assertEquals } from "../../deps.ts";
import { InMemoryEventRepository, InMemorySubscriptionRepository } from "../../src/repository.ts";
import { Event, Subscription } from "../../src/types.ts";

// --- InMemoryEventRepository ---

Deno.test("InMemoryEventRepository - saveEvent and getEvent", async () => {
  const repo = new InMemoryEventRepository();
  const event: Event = {
    event_id: "abc123",
    topic: "booking.created",
    payload: { booking_id: "b1" },
    source: "test",
    timestamp: "2024-01-15T10:00:00.000Z",
    status: "published",
  };

  await repo.saveEvent(event);
  const retrieved = await repo.getEvent("abc123");

  assertEquals(retrieved, event);
});

Deno.test("InMemoryEventRepository - getEvent returns null for missing", async () => {
  const repo = new InMemoryEventRepository();
  const result = await repo.getEvent("nonexistent");
  assertEquals(result, null);
});

Deno.test("InMemoryEventRepository - eventExists", async () => {
  const repo = new InMemoryEventRepository();
  assertEquals(await repo.eventExists("abc"), false);

  await repo.saveEvent({
    event_id: "abc",
    topic: "booking.created",
    payload: { booking_id: "b1" },
    source: "test",
    timestamp: "2024-01-15T10:00:00.000Z",
    status: "published",
  });

  assertEquals(await repo.eventExists("abc"), true);
});

Deno.test("InMemoryEventRepository - queryEvents by topic", async () => {
  const repo = new InMemoryEventRepository();

  await repo.saveEvent({
    event_id: "e1",
    topic: "booking.created",
    payload: { booking_id: "b1" },
    source: "test",
    timestamp: "2024-01-15T10:00:00.000Z",
    status: "published",
  });
  await repo.saveEvent({
    event_id: "e2",
    topic: "pricing.updated",
    payload: { property_id: "p1" },
    source: "test",
    timestamp: "2024-01-15T11:00:00.000Z",
    status: "published",
  });
  await repo.saveEvent({
    event_id: "e3",
    topic: "booking.created",
    payload: { booking_id: "b2" },
    source: "test",
    timestamp: "2024-01-15T12:00:00.000Z",
    status: "published",
  });

  const results = await repo.queryEvents({ topic: "booking.created" });
  assertEquals(results.length, 2);
  assertEquals(results.every(e => e.topic === "booking.created"), true);
});

Deno.test("InMemoryEventRepository - queryEvents with since filter", async () => {
  const repo = new InMemoryEventRepository();

  await repo.saveEvent({
    event_id: "e1",
    topic: "booking.created",
    payload: { booking_id: "b1" },
    source: "test",
    timestamp: "2024-01-15T10:00:00.000Z",
    status: "published",
  });
  await repo.saveEvent({
    event_id: "e2",
    topic: "booking.created",
    payload: { booking_id: "b2" },
    source: "test",
    timestamp: "2024-01-15T12:00:00.000Z",
    status: "published",
  });

  const results = await repo.queryEvents({
    topic: "booking.created",
    since: "2024-01-15T11:00:00.000Z",
  });
  assertEquals(results.length, 1);
  assertEquals(results[0].event_id, "e2");
});

Deno.test("InMemoryEventRepository - queryEvents with limit", async () => {
  const repo = new InMemoryEventRepository();

  for (let i = 0; i < 10; i++) {
    await repo.saveEvent({
      event_id: `e${i}`,
      topic: "booking.created",
      payload: { booking_id: `b${i}` },
      source: "test",
      timestamp: `2024-01-15T${String(i + 10).padStart(2, "0")}:00:00.000Z`,
      status: "published",
    });
  }

  const results = await repo.queryEvents({ limit: 3 });
  assertEquals(results.length, 3);
});

Deno.test("InMemoryEventRepository - queryEvents returns newest first", async () => {
  const repo = new InMemoryEventRepository();

  await repo.saveEvent({
    event_id: "old",
    topic: "booking.created",
    payload: { booking_id: "b1" },
    source: "test",
    timestamp: "2024-01-15T10:00:00.000Z",
    status: "published",
  });
  await repo.saveEvent({
    event_id: "new",
    topic: "booking.created",
    payload: { booking_id: "b2" },
    source: "test",
    timestamp: "2024-01-15T12:00:00.000Z",
    status: "published",
  });

  const results = await repo.queryEvents({});
  assertEquals(results[0].event_id, "new");
  assertEquals(results[1].event_id, "old");
});

// --- InMemorySubscriptionRepository ---

Deno.test("InMemorySubscriptionRepository - saveSubscription and get", async () => {
  const repo = new InMemorySubscriptionRepository();
  const sub: Subscription = {
    subscription_id: "sub1",
    topic: "booking.created",
    webhook_url: "https://example.com/webhook",
    created_at: "2024-01-15T10:00:00.000Z",
    active: true,
  };

  await repo.saveSubscription(sub);
  const retrieved = await repo.getSubscription("sub1");
  assertEquals(retrieved?.subscription_id, "sub1");
  assertEquals(retrieved?.topic, "booking.created");
});

Deno.test("InMemorySubscriptionRepository - getSubscriptionsByTopic", async () => {
  const repo = new InMemorySubscriptionRepository();

  await repo.saveSubscription({
    subscription_id: "s1",
    topic: "booking.created",
    webhook_url: "https://example.com/w1",
    created_at: "2024-01-15T10:00:00.000Z",
    active: true,
  });
  await repo.saveSubscription({
    subscription_id: "s2",
    topic: "pricing.updated",
    webhook_url: "https://example.com/w2",
    created_at: "2024-01-15T10:00:00.000Z",
    active: true,
  });
  await repo.saveSubscription({
    subscription_id: "s3",
    topic: "booking.created",
    webhook_url: "https://example.com/w3",
    created_at: "2024-01-15T10:00:00.000Z",
    active: true,
  });

  const results = await repo.getSubscriptionsByTopic("booking.created");
  assertEquals(results.length, 2);
});

Deno.test("InMemorySubscriptionRepository - deleteSubscription deactivates", async () => {
  const repo = new InMemorySubscriptionRepository();

  await repo.saveSubscription({
    subscription_id: "s1",
    topic: "booking.created",
    webhook_url: "https://example.com/w1",
    created_at: "2024-01-15T10:00:00.000Z",
    active: true,
  });

  assertEquals(await repo.deleteSubscription("s1"), true);

  const subs = await repo.getSubscriptionsByTopic("booking.created");
  assertEquals(subs.length, 0);

  // Still exists but inactive
  const sub = await repo.getSubscription("s1");
  assertEquals(sub?.active, false);
});

Deno.test("InMemorySubscriptionRepository - delete nonexistent returns false", async () => {
  const repo = new InMemorySubscriptionRepository();
  assertEquals(await repo.deleteSubscription("nonexistent"), false);
});
