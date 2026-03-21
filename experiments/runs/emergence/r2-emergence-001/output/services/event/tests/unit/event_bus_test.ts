import { assertEquals } from "../../deps.ts";
import { InMemoryEventBus } from "../../src/event_bus.ts";
import { InMemorySubscriptionRepository } from "../../src/repository.ts";
import { Event } from "../../src/types.ts";

function makeEvent(overrides: Partial<Event> = {}): Event {
  return {
    event_id: "test-event-id",
    topic: "booking.created",
    payload: { booking_id: "b123" },
    source: "test",
    timestamp: "2024-01-15T10:00:00.000Z",
    status: "published",
    ...overrides,
  };
}

Deno.test("InMemoryEventBus - publish notifies listeners", async () => {
  const bus = new InMemoryEventBus();
  const received: Event[] = [];

  bus.subscribe("booking.created", (event) => {
    received.push(event);
  });

  await bus.publish(makeEvent());

  assertEquals(received.length, 1);
  assertEquals(received[0].event_id, "test-event-id");
});

Deno.test("InMemoryEventBus - only notifies matching topic", async () => {
  const bus = new InMemoryEventBus();
  const bookingEvents: Event[] = [];
  const pricingEvents: Event[] = [];

  bus.subscribe("booking.created", (e) => bookingEvents.push(e));
  bus.subscribe("pricing.updated", (e) => pricingEvents.push(e));

  await bus.publish(makeEvent({ topic: "booking.created" }));

  assertEquals(bookingEvents.length, 1);
  assertEquals(pricingEvents.length, 0);
});

Deno.test("InMemoryEventBus - multiple listeners on same topic", async () => {
  const bus = new InMemoryEventBus();
  let count = 0;

  bus.subscribe("booking.created", () => { count++; });
  bus.subscribe("booking.created", () => { count++; });

  await bus.publish(makeEvent());

  assertEquals(count, 2);
});

Deno.test("InMemoryEventBus - unsubscribe removes listener", async () => {
  const bus = new InMemoryEventBus();
  const received: Event[] = [];

  const listener = (event: Event) => { received.push(event); };
  bus.subscribe("booking.created", listener);
  bus.unsubscribe("booking.created", listener);

  await bus.publish(makeEvent());

  assertEquals(received.length, 0);
});

Deno.test("InMemoryEventBus - listener errors don't break other listeners", async () => {
  const bus = new InMemoryEventBus();
  let called = false;

  bus.subscribe("booking.created", () => {
    throw new Error("listener error");
  });
  bus.subscribe("booking.created", () => {
    called = true;
  });

  await bus.publish(makeEvent());

  assertEquals(called, true);
});

Deno.test("InMemoryEventBus - publish with no listeners doesn't throw", async () => {
  const bus = new InMemoryEventBus();
  // Should not throw
  await bus.publish(makeEvent());
});
