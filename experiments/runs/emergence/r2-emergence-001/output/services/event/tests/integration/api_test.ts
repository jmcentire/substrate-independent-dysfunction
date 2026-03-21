import { assertEquals, assertExists } from "../../deps.ts";
import { EventService } from "../../src/service.ts";
import { MemoryEventStore } from "../../src/store/memory_event_store.ts";
import { MemorySubscriptionStore } from "../../src/store/memory_subscription_store.ts";
import { MemoryEventBus } from "../../src/bus/memory_event_bus.ts";
import { createRouter } from "../../src/router.ts";
import { Config } from "../../src/config.ts";

function createTestSetup() {
  const eventStore = new MemoryEventStore();
  const subscriptionStore = new MemorySubscriptionStore();
  const eventBus = new MemoryEventBus();
  const config: Config = {
    port: 0,
    databaseUrl: "",
    kafkaBrokers: [],
    kafkaTopicPrefix: "test.",
    useMockKafka: true,
    useMockDb: true,
    webhookTimeoutMs: 1000,
    webhookMaxRetries: 1,
  };
  const service = new EventService(eventStore, eventBus, subscriptionStore, config);
  const handler = createRouter(service);
  return { handler, eventStore, subscriptionStore, eventBus };
}

async function makeRequest(
  handler: (req: Request) => Promise<Response>,
  method: string,
  path: string,
  body?: unknown
): Promise<{ status: number; body: Record<string, unknown> }> {
  const options: RequestInit = { method };
  if (body) {
    options.body = JSON.stringify(body);
    options.headers = { "Content-Type": "application/json" };
  }
  const response = await handler(new Request(`http://localhost${path}`, options));
  const responseBody = await response.json();
  return { status: response.status, body: responseBody };
}

// --- Health Check ---

Deno.test("API - GET /health returns healthy", async () => {
  const { handler } = createTestSetup();
  const { status, body } = await makeRequest(handler, "GET", "/health");
  assertEquals(status, 200);
  assertEquals(body.success, true);
  assertEquals((body.data as Record<string, unknown>).status, "healthy");
});

// --- Publish Events ---

Deno.test("API - POST /events publishes event", async () => {
  const { handler } = createTestSetup();
  const { status, body } = await makeRequest(handler, "POST", "/events", {
    event_type: "booking.created",
    source: "test-service",
    payload: { booking_id: "b1", property_id: "p1" },
  });

  assertEquals(status, 201);
  assertEquals(body.success, true);
  assertEquals(body.message, "Event published");
  
  const event = body.data as Record<string, unknown>;
  assertExists(event.event_id);
  assertEquals((event.event_id as string).length, 64);
  assertEquals(event.event_type, "booking.created");
  assertEquals(event.source, "test-service");
});

Deno.test("API - POST /events idempotent (duplicate returns 200)", async () => {
  const { handler } = createTestSetup();
  const payload = {
    event_type: "booking.created",
    source: "test-service",
    payload: { booking_id: "b1", property_id: "p1" },
  };

  const first = await makeRequest(handler, "POST", "/events", payload);
  assertEquals(first.status, 201);

  const second = await makeRequest(handler, "POST", "/events", payload);
  assertEquals(second.status, 200);
  assertEquals(second.body.message, "Event already exists (idempotent)");

  // Same event ID
  assertEquals(
    (first.body.data as Record<string, unknown>).event_id,
    (second.body.data as Record<string, unknown>).event_id
  );
});

Deno.test("API - POST /events validates event_type", async () => {
  const { handler } = createTestSetup();
  const { status, body } = await makeRequest(handler, "POST", "/events", {
    event_type: "invalid.type",
    source: "test",
    payload: {},
  });

  assertEquals(status, 400);
  assertEquals(body.success, false);
});

Deno.test("API - POST /events validates required payload fields", async () => {
  const { handler } = createTestSetup();
  const { status, body } = await makeRequest(handler, "POST", "/events", {
    event_type: "booking.created",
    source: "test",
    payload: { booking_id: "b1" }, // missing property_id
  });

  assertEquals(status, 400);
  assertEquals(body.success, false);
});

Deno.test("API - POST /events validates missing source", async () => {
  const { handler } = createTestSetup();
  const { status } = await makeRequest(handler, "POST", "/events", {
    event_type: "booking.created",
    payload: { booking_id: "b1", property_id: "p1" },
  });

  assertEquals(status, 400);
});

// --- Get Event by ID ---

Deno.test("API - GET /events/:event_id returns event", async () => {
  const { handler } = createTestSetup();

  // Publish first
  const published = await makeRequest(handler, "POST", "/events", {
    event_type: "booking.created",
    source: "test",
    payload: { booking_id: "b1", property_id: "p1" },
  });
  const eventId = (published.body.data as Record<string, unknown>).event_id as string;

  // Retrieve
  const { status, body } = await makeRequest(handler, "GET", `/events/${eventId}`);
  assertEquals(status, 200);
  assertEquals(body.success, true);
  assertEquals((body.data as Record<string, unknown>).event_id, eventId);
});

Deno.test("API - GET /events/:event_id returns 404 for missing", async () => {
  const { handler } = createTestSetup();
  const fakeId = "a".repeat(64);
  const { status, body } = await makeRequest(handler, "GET", `/events/${fakeId}`);
  assertEquals(status, 404);
  assertEquals(body.success, false);
});

// --- Query Events ---

Deno.test("API - GET /events returns all events", async () => {
  const { handler } = createTestSetup();

  await makeRequest(handler, "POST", "/events", {
    event_type: "booking.created",
    source: "test",
    payload: { booking_id: "b1", property_id: "p1" },
  });
  await makeRequest(handler, "POST", "/events", {
    event_type: "booking.confirmed",
    source: "test",
    payload: { booking_id: "b1" },
  });

  const { status, body } = await makeRequest(handler, "GET", "/events");
  assertEquals(status, 200);
  assertEquals(body.success, true);
  assertEquals((body.data as unknown[]).length, 2);
});

Deno.test("API - GET /events?type= filters by type", async () => {
  const { handler } = createTestSetup();

  await makeRequest(handler, "POST", "/events", {
    event_type: "booking.created",
    source: "test",
    payload: { booking_id: "b1", property_id: "p1" },
  });
  await makeRequest(handler, "POST", "/events", {
    event_type: "booking.confirmed",
    source: "test",
    payload: { booking_id: "b1" },
  });
  await makeRequest(handler, "POST", "/events", {
    event_type: "booking.created",
    source: "test",
    payload: { booking_id: "b2", property_id: "p2" },
  });

  const { status, body } = await makeRequest(handler, "GET", "/events?type=booking.created");
  assertEquals(status, 200);
  assertEquals((body.data as unknown[]).length, 2);
});

Deno.test("API - GET /events?type= rejects invalid type", async () => {
  const { handler } = createTestSetup();
  const { status } = await makeRequest(handler, "GET", "/events?type=invalid.type");
  assertEquals(status, 400);
});

Deno.test("API - GET /events?limit= limits results", async () => {
  const { handler } = createTestSetup();

  for (let i = 0; i < 5; i++) {
    await makeRequest(handler, "POST", "/events", {
      event_type: "booking.created",
      source: "test",
      payload: { booking_id: `b${i}`, property_id: `p${i}` },
    });
  }

  const { status, body } = await makeRequest(handler, "GET", "/events?limit=2");
  assertEquals(status, 200);
  assertEquals((body.data as unknown[]).length, 2);
});

// --- Subscribe ---

Deno.test("API - POST /events/subscribe creates subscription", async () => {
  const { handler } = createTestSetup();
  const { status, body } = await makeRequest(handler, "POST", "/events/subscribe", {
    event_type: "booking.created",
    webhook_url: "https://example.com/webhook",
  });

  assertEquals(status, 201);
  assertEquals(body.success, true);
  const sub = body.data as Record<string, unknown>;
  assertExists(sub.subscription_id);
  assertEquals(sub.event_type, "booking.created");
  assertEquals(sub.webhook_url, "https://example.com/webhook");
  assertEquals(sub.active, true);
});

Deno.test("API - POST /events/subscribe validates event_type", async () => {
  const { handler } = createTestSetup();
  const { status } = await makeRequest(handler, "POST", "/events/subscribe", {
    event_type: "bad.type",
    webhook_url: "https://example.com/webhook",
  });
  assertEquals(status, 400);
});

Deno.test("API - POST /events/subscribe validates webhook_url", async () => {
  const { handler } = createTestSetup();
  const { status } = await makeRequest(handler, "POST", "/events/subscribe", {
    event_type: "booking.created",
    webhook_url: "not-a-url",
  });
  assertEquals(status, 400);
});

// --- 404 ---

Deno.test("API - unknown path returns 404", async () => {
  const { handler } = createTestSetup();
  const { status, body } = await makeRequest(handler, "GET", "/unknown");
  assertEquals(status, 404);
  assertEquals(body.success, false);
});

// --- All event types work ---

Deno.test("API - all event types can be published", async () => {
  const { handler } = createTestSetup();

  const events = [
    { event_type: "availability.updated", payload: { property_id: "p1" } },
    { event_type: "pricing.updated", payload: { property_id: "p1" } },
    { event_type: "booking.created", payload: { booking_id: "b1", property_id: "p1" } },
    { event_type: "booking.confirmed", payload: { booking_id: "b1" } },
    { event_type: "booking.cancelled", payload: { booking_id: "b1" } },
    { event_type: "property.updated", payload: { property_id: "p1" } },
    { event_type: "sync.completed", payload: { sync_id: "s1" } },
    { event_type: "payment.processed", payload: { payment_id: "pay1" } },
  ];

  for (const evt of events) {
    const { status, body } = await makeRequest(handler, "POST", "/events", {
      ...evt,
      source: "test",
    });
    assertEquals(status, 201, `Failed for ${evt.event_type}: ${JSON.stringify(body)}`);
  }
});
