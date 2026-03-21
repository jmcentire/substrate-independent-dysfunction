import { assertEquals } from "../../deps.ts";
import { generateEventId } from "../../src/event_id.ts";

Deno.test("generateEventId - produces consistent hash for same inputs", async () => {
  const topic = "booking.created";
  const payload = { booking_id: "b123", guest: "John" };
  const timestamp = "2024-01-15T10:00:00.000Z";

  const id1 = await generateEventId(topic, payload, timestamp);
  const id2 = await generateEventId(topic, payload, timestamp);

  assertEquals(id1, id2);
});

Deno.test("generateEventId - produces 64-char hex string (SHA256)", async () => {
  const id = await generateEventId(
    "booking.created",
    { booking_id: "b123" },
    "2024-01-15T10:00:00.000Z",
  );

  assertEquals(id.length, 64);
  assertEquals(/^[a-f0-9]{64}$/.test(id), true);
});

Deno.test("generateEventId - different topic produces different ID", async () => {
  const payload = { booking_id: "b123" };
  const timestamp = "2024-01-15T10:00:00.000Z";

  const id1 = await generateEventId("booking.created", payload, timestamp);
  const id2 = await generateEventId("booking.confirmed", payload, timestamp);

  assertEquals(id1 !== id2, true);
});

Deno.test("generateEventId - different payload produces different ID", async () => {
  const topic = "booking.created";
  const timestamp = "2024-01-15T10:00:00.000Z";

  const id1 = await generateEventId(topic, { booking_id: "b123" }, timestamp);
  const id2 = await generateEventId(topic, { booking_id: "b456" }, timestamp);

  assertEquals(id1 !== id2, true);
});

Deno.test("generateEventId - different timestamp produces different ID", async () => {
  const topic = "booking.created";
  const payload = { booking_id: "b123" };

  const id1 = await generateEventId(topic, payload, "2024-01-15T10:00:00.000Z");
  const id2 = await generateEventId(topic, payload, "2024-01-15T11:00:00.000Z");

  assertEquals(id1 !== id2, true);
});

Deno.test("generateEventId - payload key order doesn't matter", async () => {
  const topic = "booking.created";
  const timestamp = "2024-01-15T10:00:00.000Z";

  const id1 = await generateEventId(topic, { a: 1, b: 2 }, timestamp);
  const id2 = await generateEventId(topic, { b: 2, a: 1 }, timestamp);

  assertEquals(id1, id2);
});
