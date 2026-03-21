import { assertEquals, assertExists } from "../../deps.ts";
import { EventService } from "../../src/service.ts";
import { MemoryEventStore } from "../../src/store/memory_event_store.ts";
import { MemorySubscriptionStore } from "../../src/store/memory_subscription_store.ts";
import { MemoryEventBus } from "../../src/bus/memory_event_bus.ts";
import { Config } from "../../src/config.ts";
import { WanderEvent } from "../../src/types.ts";

function createTestConfig(): Config {
  return {
    port: 0,
    databaseUrl: "",
    kafkaBrokers: [],
    kafkaTopicPrefix: "test.",
    useMockKafka: true,
    useMockDb: true,
    webhookTimeoutMs: 1000,
    webhookMaxRetries: 1,
  };
}

function createTestService() {
  const eventStore = new MemoryEventStore();
  const subscriptionStore = new MemorySubscriptionStore();
  const eventBus = new MemoryEventBus();
  const config = createTestConfig();
  const service = new EventService(eventStore, eventBus, subscriptionStore, config);
  return { service, eventStore, subscriptionStore, eventBus };
}

Deno.test("EventService - publish event returns event with computed ID", async () => {
  const { service } = createTestService();

  const { event, created } = await service.publishEvent({
    event_type: "booking.created",
    source: "test-service",
    payload: { booking_id: "b1", property_id: "p1" },
  });

  assertEquals(created, true);
  assertExists(event.event_id);
  assertEquals(event.event_id.length, 64);
  assertEquals(event.event_type, "booking.created");
  assertEquals(event.source, "test-service");
  assertEquals(event.payload.booking_id, "b1");
  assertExists(event.created_at);
});

Deno.test("EventService - idempotent publish (same content = same ID)", async () => {
  const { service, eventStore } = createTestService();

  const request = {
    event_type: "booking.created" as const,
    source: "test-service",
    payload: { booking_id: "b1", property_id: "p1" },
  };

  const { event: e1, created: c1 } = await service.publishEvent(request);
  const { event: e2, created: c2 } = await service.publishEvent(request);

  assertEquals(c1, true);
  assertEquals(c2, false);
  assertEquals(e1.event_id, e2.event_id);
  assertEquals(eventStore.size, 1);
});

Deno.test("EventService - different payloads get different IDs", async () => {
  const { service } = createTestService();

  const { event: e1 } = await service.publishEvent({
    event_type: "booking.created",
    source: "test",
    payload: { booking_id: "b1", property_id: "p1" },
  });

  const { event: e2 } = await service.publishEvent({
    event_type: "booking.created",
    source: "test",
    payload: { booking_id: "b2", property_id: "p2" },
  });

  assertEquals(e1.event_id !== e2.event_id, true);
});

Deno.test("EventService - getEvent returns stored event", async () => {
  const { service } = createTestService();

  const { event } = await service.publishEvent({
    event_type: "booking.created",
    source: "test",
    payload: { booking_id: "b1", property_id: "p1" },
  });

  const retrieved = await service.getEvent(event.event_id);
  assertExists(retrieved);
  assertEquals(retrieved!.event_id, event.event_id);
  assertEquals(retrieved!.event_type, "booking.created");
});

Deno.test("EventService - getEvent returns null for missing", async () => {
  const { service } = createTestService();
  const result = await service.getEvent("nonexistent");
  assertEquals(result, null);
});

Deno.test("EventService - queryEvents by type", async () => {
  const { service } = createTestService();

  await service.publishEvent({
    event_type: "booking.created",
    source: "test",
    payload: { booking_id: "b1", property_id: "p1" },
  });
  await service.publishEvent({
    event_type: "booking.confirmed",
    source: "test",
    payload: { booking_id: "b1" },
  });
  await service.publishEvent({
    event_type: "booking.created",
    source: "test",
    payload: { booking_id: "b2", property_id: "p2" },
  });

  const results = await service.queryEvents({ type: "booking.created" });
  assertEquals(results.length, 2);
});

Deno.test("EventService - queryEvents with limit", async () => {
  const { service } = createTestService();

  for (let i = 0; i < 5; i++) {
    await service.publishEvent({
      event_type: "booking.created",
      source: "test",
      payload: { booking_id: `b${i}`, property_id: `p${i}` },
    });
  }

  const results = await service.queryEvents({ limit: 2 });
  assertEquals(results.length, 2);
});

Deno.test("EventService - publish emits to event bus", async () => {
  const { service, eventBus } = createTestService();
  const received: WanderEvent[] = [];

  eventBus.subscribe("booking.created", async (e) => {
    received.push(e);
  });

  await service.publishEvent({
    event_type: "booking.created",
    source: "test",
    payload: { booking_id: "b1", property_id: "p1" },
  });

  assertEquals(received.length, 1);
  assertEquals(received[0].event_type, "booking.created");
});

Deno.test("EventService - duplicate publish does NOT re-emit to bus", async () => {
  const { service, eventBus } = createTestService();
  const received: WanderEvent[] = [];

  eventBus.subscribe("booking.created", async (e) => {
    received.push(e);
  });

  const request = {
    event_type: "booking.created" as const,
    source: "test",
    payload: { booking_id: "b1", property_id: "p1" },
  };

  await service.publishEvent(request);
  await service.publishEvent(request);

  assertEquals(received.length, 1); // Only emitted once
});

Deno.test("EventService - subscribe creates subscription", async () => {
  const { service } = createTestService();

  const sub = await service.subscribe({
    event_type: "booking.created",
    webhook_url: "https://example.com/webhook",
  });

  assertExists(sub.subscription_id);
  assertEquals(sub.event_type, "booking.created");
  assertEquals(sub.webhook_url, "https://example.com/webhook");
  assertEquals(sub.active, true);
});
