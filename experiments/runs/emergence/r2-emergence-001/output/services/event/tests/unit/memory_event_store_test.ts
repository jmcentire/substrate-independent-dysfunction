import { assertEquals } from "../../deps.ts";
import { MemoryEventStore } from "../../src/store/memory_event_store.ts";
import { WanderEvent } from "../../src/types.ts";

function makeEvent(overrides: Partial<WanderEvent> = {}): WanderEvent {
  return {
    event_id: "a".repeat(64),
    event_type: "booking.created",
    source: "test",
    payload: { booking_id: "b1", property_id: "p1" },
    metadata: {},
    created_at: new Date().toISOString(),
    version: 1,
    ...overrides,
  };
}

Deno.test("MemoryEventStore - save and retrieve event", async () => {
  const store = new MemoryEventStore();
  const event = makeEvent();

  const { created } = await store.save(event);
  assertEquals(created, true);
  assertEquals(store.size, 1);

  const retrieved = await store.getById(event.event_id);
  assertEquals(retrieved?.event_id, event.event_id);
  assertEquals(retrieved?.event_type, event.event_type);

  await store.close();
});

Deno.test("MemoryEventStore - idempotent save (same ID)", async () => {
  const store = new MemoryEventStore();
  const event = makeEvent();

  const { created: first } = await store.save(event);
  assertEquals(first, true);

  const { created: second } = await store.save(event);
  assertEquals(second, false);
  assertEquals(store.size, 1);

  await store.close();
});

Deno.test("MemoryEventStore - getById returns null for missing", async () => {
  const store = new MemoryEventStore();
  const result = await store.getById("nonexistent");
  assertEquals(result, null);
  await store.close();
});

Deno.test("MemoryEventStore - query by type", async () => {
  const store = new MemoryEventStore();
  
  await store.save(makeEvent({ event_id: "a".repeat(64), event_type: "booking.created" }));
  await store.save(makeEvent({ event_id: "b".repeat(64), event_type: "booking.confirmed" }));
  await store.save(makeEvent({ event_id: "c".repeat(64), event_type: "booking.created" }));

  const results = await store.query({ type: "booking.created" });
  assertEquals(results.length, 2);
  results.forEach((e) => assertEquals(e.event_type, "booking.created"));

  await store.close();
});

Deno.test("MemoryEventStore - query with limit", async () => {
  const store = new MemoryEventStore();
  
  for (let i = 0; i < 10; i++) {
    await store.save(makeEvent({
      event_id: `${i}${'0'.repeat(63)}`,
      created_at: new Date(Date.now() + i * 1000).toISOString(),
    }));
  }

  const results = await store.query({ limit: 3 });
  assertEquals(results.length, 3);

  await store.close();
});

Deno.test("MemoryEventStore - query with since", async () => {
  const store = new MemoryEventStore();
  const now = Date.now();

  await store.save(makeEvent({
    event_id: "a".repeat(64),
    created_at: new Date(now - 10000).toISOString(),
  }));
  await store.save(makeEvent({
    event_id: "b".repeat(64),
    created_at: new Date(now + 10000).toISOString(),
  }));

  const results = await store.query({ since: new Date(now).toISOString() });
  assertEquals(results.length, 1);
  assertEquals(results[0].event_id, "b".repeat(64));

  await store.close();
});

Deno.test("MemoryEventStore - query returns results sorted by created_at desc", async () => {
  const store = new MemoryEventStore();
  const now = Date.now();

  await store.save(makeEvent({
    event_id: "a".repeat(64),
    created_at: new Date(now - 1000).toISOString(),
  }));
  await store.save(makeEvent({
    event_id: "b".repeat(64),
    created_at: new Date(now + 1000).toISOString(),
  }));
  await store.save(makeEvent({
    event_id: "c".repeat(64),
    created_at: new Date(now).toISOString(),
  }));

  const results = await store.query({});
  assertEquals(results.length, 3);
  assertEquals(results[0].event_id, "b".repeat(64));
  assertEquals(results[1].event_id, "c".repeat(64));
  assertEquals(results[2].event_id, "a".repeat(64));

  await store.close();
});

Deno.test("MemoryEventStore - close clears data", async () => {
  const store = new MemoryEventStore();
  await store.save(makeEvent());
  assertEquals(store.size, 1);
  await store.close();
  assertEquals(store.size, 0);
});
