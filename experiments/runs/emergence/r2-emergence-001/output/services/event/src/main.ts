import { loadConfig } from "./config.ts";
import { EventService } from "./service.ts";
import { startServer } from "./server.ts";
import { MemoryEventStore } from "./store/memory_event_store.ts";
import { MemorySubscriptionStore } from "./store/memory_subscription_store.ts";
import { MemoryEventBus } from "./bus/memory_event_bus.ts";

const config = loadConfig();

console.log("Starting Event Service with config:", {
  port: config.port,
  useMockKafka: config.useMockKafka,
  useMockDb: config.useMockDb,
});

// Create stores and bus (using in-memory implementations for now)
const eventStore = new MemoryEventStore();
const subscriptionStore = new MemorySubscriptionStore();
const eventBus = new MemoryEventBus();

// Create service
const service = new EventService(eventStore, eventBus, subscriptionStore, config);

// Start server
const server = await startServer(service, config.port);

console.log(`Event service running on port ${server.port}`);

// Graceful shutdown
Deno.addSignalListener("SIGINT", async () => {
  console.log("Shutting down event service...");
  server.close();
  await eventBus.close();
  await eventStore.close();
  await subscriptionStore.close();
  Deno.exit(0);
});
