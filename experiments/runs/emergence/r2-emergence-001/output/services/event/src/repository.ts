import { Event, EventQuery, EventTopic, Subscription } from "./types.ts";

/**
 * Repository interface for event persistence.
 */
export interface EventRepository {
  saveEvent(event: Event): Promise<Event>;
  getEvent(eventId: string): Promise<Event | null>;
  queryEvents(query: EventQuery): Promise<Event[]>;
  eventExists(eventId: string): Promise<boolean>;
}

/**
 * Repository interface for subscription persistence.
 */
export interface SubscriptionRepository {
  saveSubscription(sub: Subscription): Promise<Subscription>;
  getSubscriptionsByTopic(topic: EventTopic): Promise<Subscription[]>;
  getSubscription(subscriptionId: string): Promise<Subscription | null>;
  deleteSubscription(subscriptionId: string): Promise<boolean>;
}

/**
 * In-memory event repository for MVP / testing.
 */
export class InMemoryEventRepository implements EventRepository {
  private events: Map<string, Event> = new Map();

  async saveEvent(event: Event): Promise<Event> {
    this.events.set(event.event_id, { ...event });
    return event;
  }

  async getEvent(eventId: string): Promise<Event | null> {
    return this.events.get(eventId) ?? null;
  }

  async queryEvents(query: EventQuery): Promise<Event[]> {
    let results = Array.from(this.events.values());

    if (query.topic) {
      results = results.filter((e) => e.topic === query.topic);
    }

    if (query.since) {
      const sinceDate = new Date(query.since);
      results = results.filter((e) => new Date(e.timestamp) >= sinceDate);
    }

    // Sort by timestamp descending (newest first)
    results.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

    const limit = query.limit ?? 50;
    return results.slice(0, Math.min(limit, 1000));
  }

  async eventExists(eventId: string): Promise<boolean> {
    return this.events.has(eventId);
  }

  /** For testing: clear all events */
  clear(): void {
    this.events.clear();
  }

  /** For testing: get count */
  get size(): number {
    return this.events.size;
  }
}

/**
 * In-memory subscription repository for MVP / testing.
 */
export class InMemorySubscriptionRepository implements SubscriptionRepository {
  private subscriptions: Map<string, Subscription> = new Map();

  async saveSubscription(sub: Subscription): Promise<Subscription> {
    this.subscriptions.set(sub.subscription_id, { ...sub });
    return sub;
  }

  async getSubscriptionsByTopic(topic: EventTopic): Promise<Subscription[]> {
    return Array.from(this.subscriptions.values()).filter(
      (s) => s.topic === topic && s.active
    );
  }

  async getSubscription(subscriptionId: string): Promise<Subscription | null> {
    return this.subscriptions.get(subscriptionId) ?? null;
  }

  async deleteSubscription(subscriptionId: string): Promise<boolean> {
    const sub = this.subscriptions.get(subscriptionId);
    if (sub) {
      sub.active = false;
      this.subscriptions.set(subscriptionId, sub);
      return true;
    }
    return false;
  }

  /** For testing: clear all subscriptions */
  clear(): void {
    this.subscriptions.clear();
  }

  /** For testing: get count */
  get size(): number {
    return this.subscriptions.size;
  }
}
