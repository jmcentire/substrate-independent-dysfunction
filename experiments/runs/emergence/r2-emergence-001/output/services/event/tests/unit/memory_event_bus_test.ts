import { assertEquals } from "../../deps.ts";
import { MemoryEventBus } from "../../src/bus/memory_event_bus.ts";
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

Deno.test("MemoryEventBus - subscribe and receive events", async () => {
  const bus = new MemoryEventBus();
  const received: WanderEvent[] = [];

  const handler = async (event: WanderEvent) => {
    received.push(event);
  };

  bus.subscribe("booking.created", handler);

  const event = makeEvent();
  await bus.publish(event);

  assertEquals(received.length, 1);
  assertEquals(received[0].event_id, event.event_id);

  await bus.close();
});

Deno.test("MemoryEventBus - only receives subscribed event types", async () => {
  const bus = new MemoryEventBus();
  const received: WanderEvent[] = [];

  bus.subscribe("booking.created", async (event) => {
    received.push(event);
  });

  await bus.publish(makeEvent({ event_type: "booking.confirmed", event_id: "b".repeat(64) }));
  assertEquals(received.length, 0);

  await bus.publish(makeEvent({ event_type: "booking.created" }));
  assertEquals(received.length, 1);

  await bus.close();
});

Deno.test("MemoryEventBus - unsubscribe stops receiving", async () => {
  const bus = new MemoryEventBus();
  const received: WanderEvent[] = [];

  const handler = async (event: WanderEvent) => {
    received.push(event);
  };

  bus.subscribe("booking.created", handler);
  await bus.publish(makeEvent());
  assertEquals(received.length, 1);

  bus.unsubscribe("booking.created", handler);
  await bus.publish(makeEvent({ event_id: "b".repeat(64) }));
  assertEquals(received.length, 1); // still 1, not 2

  await bus.close();
});

Deno.test("MemoryEventBus - multiple subscribers", async () => {
  const bus = new MemoryEventBus();
  const received1: WanderEvent[] = [];
  const received2: WanderEvent[] = [];

  bus.subscribe("booking.created", async (e) => { received1.push(e); });
  bus.subscribe("booking.created", async (e) => { received2.push(e); });

  await bus.publish(makeEvent());

  assertEquals(received1.length, 1);
  assertEquals(received2.length, 1);

  await bus.close();
});

Deno.test("MemoryEventBus - handler errors don't block other handlers", async () => {
  const bus = new MemoryEventBus();
  const received: WanderEvent[] = [];

  bus.subscribe("booking.created", async () => {
    throw new Error("handler error");
  });
  bus.subscribe("booking.created", async (e) => {
    received.push(e);
  });

  await bus.publish(makeEvent());
  assertEquals(received.length, 1);

  await bus.close();
});

Deno.test("MemoryEventBus - subscribeAll receives all types", async () => {
  const bus = new MemoryEventBus();
  const received: WanderEvent[] = [];

  bus.subscribeAll(async (e) => { received.push(e); });

  await bus.publish(makeEvent({ event_type: "booking.created", event_id: "a".repeat(64) }));
  await bus.publish(makeEvent({ event_type: "booking.confirmed", event_id: "b".repeat(64) }));
  await bus.publish(makeEvent({ event_type: "pricing.updated", event_id: "c".repeat(64) }));

  assertEquals(received.length, 3);

  await bus.close();
});
