// Core types
export type {
  WanderEvent,
  PublishEventRequest,
  EventQuery,
  Subscription,
  SubscribeRequest,
  EventBus,
  EventStore,
  SubscriptionStore,
  ApiResponse,
  EventType,
} from "./types.ts";

export { EVENT_TYPES } from "./types.ts";

// Core service
export { EventService } from "./service.ts";

// Hashing
export { computeEventId, canonicalJson } from "./hash.ts";

// Validation
export { validatePublishRequest, validateSubscribeRequest, ValidationError } from "./validation.ts";

// Stores
export { MemoryEventStore } from "./store/memory_event_store.ts";
export { MemorySubscriptionStore } from "./store/memory_subscription_store.ts";

// Bus
export { MemoryEventBus } from "./bus/memory_event_bus.ts";

// Router
export { createRouter } from "./router.ts";

// Config
export { loadConfig } from "./config.ts";
export type { Config } from "./config.ts";
