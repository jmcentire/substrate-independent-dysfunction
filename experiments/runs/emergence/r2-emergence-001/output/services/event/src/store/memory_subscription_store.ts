import { SubscriptionStore, Subscription, SubscribeRequest, EventType } from "../types.ts";

/**
 * In-memory subscription store for development and testing.
 */
export class MemorySubscriptionStore implements SubscriptionStore {
  private subscriptions: Map<string, Subscription> = new Map();
  private nextId = 1;

  async create(req: SubscribeRequest): Promise<Subscription> {
    // Check for existing active subscription with same type + url
    for (const sub of this.subscriptions.values()) {
      if (
        sub.active &&
        sub.event_type === req.event_type &&
        sub.webhook_url === req.webhook_url
      ) {
        return { ...sub };
      }
    }

    const now = new Date().toISOString();
    const sub: Subscription = {
      subscription_id: crypto.randomUUID(),
      event_type: req.event_type,
      webhook_url: req.webhook_url,
      secret: req.secret,
      active: true,
      created_at: now,
      updated_at: now,
      failure_count: 0,
    };

    this.subscriptions.set(sub.subscription_id, sub);
    return { ...sub };
  }

  async getByEventType(eventType: EventType): Promise<Subscription[]> {
    return Array.from(this.subscriptions.values())
      .filter((s) => s.active && s.event_type === eventType)
      .map((s) => ({ ...s }));
  }

  async getById(subscriptionId: string): Promise<Subscription | null> {
    const sub = this.subscriptions.get(subscriptionId);
    return sub ? { ...sub } : null;
  }

  async deactivate(subscriptionId: string): Promise<boolean> {
    const sub = this.subscriptions.get(subscriptionId);
    if (!sub) return false;
    sub.active = false;
    sub.updated_at = new Date().toISOString();
    return true;
  }

  async updateDelivery(subscriptionId: string, success: boolean): Promise<void> {
    const sub = this.subscriptions.get(subscriptionId);
    if (!sub) return;
    sub.last_delivery = new Date().toISOString();
    sub.updated_at = new Date().toISOString();
    if (success) {
      sub.failure_count = 0;
    } else {
      sub.failure_count += 1;
    }
  }

  async close(): Promise<void> {
    this.subscriptions.clear();
  }

  /** Utility for testing */
  get size(): number {
    return this.subscriptions.size;
  }
}
