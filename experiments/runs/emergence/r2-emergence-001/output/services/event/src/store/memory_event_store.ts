import { EventStore, EventQuery, WanderEvent } from "../types.ts";

/**
 * In-memory event store for development and testing.
 * Provides the same interface as the PostgreSQL store.
 */
export class MemoryEventStore implements EventStore {
  private events: Map<string, WanderEvent> = new Map();

  async save(event: WanderEvent): Promise<{ created: boolean }> {
    if (this.events.has(event.event_id)) {
      // Idempotent: same event_id means same content, just return
      return { created: false };
    }
    this.events.set(event.event_id, { ...event });
    return { created: true };
  }

  async getById(eventId: string): Promise<WanderEvent | null> {
    const event = this.events.get(eventId);
    return event ? { ...event } : null;
  }

  async query(params: EventQuery): Promise<WanderEvent[]> {
    let results = Array.from(this.events.values());

    // Filter by type
    if (params.type) {
      results = results.filter((e) => e.event_type === params.type);
    }

    // Filter by since
    if (params.since) {
      const since = new Date(params.since).getTime();
      results = results.filter((e) => new Date(e.created_at).getTime() >= since);
    }

    // Filter by until
    if (params.until) {
      const until = new Date(params.until).getTime();
      results = results.filter((e) => new Date(e.created_at).getTime() <= until);
    }

    // Sort by created_at descending
    results.sort((a, b) => 
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    );

    // Pagination
    const offset = params.offset || 0;
    const limit = params.limit || 50;
    results = results.slice(offset, offset + limit);

    return results;
  }

  async close(): Promise<void> {
    this.events.clear();
  }

  /** Utility: get total event count (for testing) */
  get size(): number {
    return this.events.size;
  }
}
