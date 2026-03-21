import { EventBus, EventType, WanderEvent } from "../types.ts";

/**
 * In-memory event bus that simulates Kafka's pub/sub behavior.
 * Used for development and testing without requiring a Kafka cluster.
 */
export class MemoryEventBus implements EventBus {
  private handlers: Map<EventType, Set<(event: WanderEvent) => Promise<void>>> = new Map();
  private allHandlers: Set<(event: WanderEvent) => Promise<void>> = new Set();

  async publish(event: WanderEvent): Promise<void> {
    const typeHandlers = this.handlers.get(event.event_type);
    const promises: Promise<void>[] = [];

    if (typeHandlers) {
      for (const handler of typeHandlers) {
        promises.push(
          handler(event).catch((err) => {
            console.error(
              `Event handler error for ${event.event_type}:`,
              err
            );
          })
        );
      }
    }

    // Also notify wildcard/all handlers
    for (const handler of this.allHandlers) {
      promises.push(
        handler(event).catch((err) => {
          console.error(`Event handler error (all):`, err);
        })
      );
    }

    await Promise.all(promises);
  }

  subscribe(
    eventType: EventType,
    handler: (event: WanderEvent) => Promise<void>
  ): void {
    if (!this.handlers.has(eventType)) {
      this.handlers.set(eventType, new Set());
    }
    this.handlers.get(eventType)!.add(handler);
  }

  unsubscribe(
    eventType: EventType,
    handler: (event: WanderEvent) => Promise<void>
  ): void {
    const typeHandlers = this.handlers.get(eventType);
    if (typeHandlers) {
      typeHandlers.delete(handler);
    }
  }

  /** Subscribe to all event types */
  subscribeAll(handler: (event: WanderEvent) => Promise<void>): void {
    this.allHandlers.add(handler);
  }

  async close(): Promise<void> {
    this.handlers.clear();
    this.allHandlers.clear();
  }
}
