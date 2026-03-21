import { Event, EventTopic, Subscription } from "./types.ts";
import { SubscriptionRepository } from "./repository.ts";

/**
 * Listener callback for event bus.
 */
export type EventListener = (event: Event) => void | Promise<void>;

/**
 * Event bus interface — abstracts Kafka or in-memory distribution.
 */
export interface EventBus {
  publish(event: Event): Promise<void>;
  subscribe(topic: EventTopic, listener: EventListener): void;
  unsubscribe(topic: EventTopic, listener: EventListener): void;
}

/**
 * In-memory event bus for MVP.
 * Distributes events to registered listeners and webhook subscribers.
 */
export class InMemoryEventBus implements EventBus {
  private listeners: Map<EventTopic, Set<EventListener>> = new Map();
  private subscriptionRepo: SubscriptionRepository | null;

  constructor(subscriptionRepo?: SubscriptionRepository) {
    this.subscriptionRepo = subscriptionRepo ?? null;
  }

  async publish(event: Event): Promise<void> {
    // Notify in-process listeners
    const topicListeners = this.listeners.get(event.topic);
    if (topicListeners) {
      for (const listener of topicListeners) {
        try {
          await listener(event);
        } catch (err) {
          console.error(`Event listener error for topic ${event.topic}:`, err);
        }
      }
    }

    // Notify webhook subscribers
    if (this.subscriptionRepo) {
      const subs = await this.subscriptionRepo.getSubscriptionsByTopic(event.topic);
      for (const sub of subs) {
        this.deliverWebhook(sub, event).catch((err) => {
          console.error(`Webhook delivery failed for ${sub.webhook_url}:`, err);
        });
      }
    }
  }

  subscribe(topic: EventTopic, listener: EventListener): void {
    if (!this.listeners.has(topic)) {
      this.listeners.set(topic, new Set());
    }
    this.listeners.get(topic)!.add(listener);
  }

  unsubscribe(topic: EventTopic, listener: EventListener): void {
    const topicListeners = this.listeners.get(topic);
    if (topicListeners) {
      topicListeners.delete(listener);
    }
  }

  /**
   * Deliver event to a webhook subscriber.
   */
  private async deliverWebhook(sub: Subscription, event: Event): Promise<void> {
    try {
      const body = JSON.stringify(event);
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        "X-Event-Topic": event.topic,
        "X-Event-ID": event.event_id,
      };

      // Add HMAC signature if secret is configured
      if (sub.secret) {
        const encoder = new TextEncoder();
        const key = await crypto.subtle.importKey(
          "raw",
          encoder.encode(sub.secret),
          { name: "HMAC", hash: "SHA-256" },
          false,
          ["sign"],
        );
        const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(body));
        const hexSig = Array.from(new Uint8Array(signature))
          .map((b) => b.toString(16).padStart(2, "0"))
          .join("");
        headers["X-Signature"] = `sha256=${hexSig}`;
      }

      await fetch(sub.webhook_url, {
        method: "POST",
        headers,
        body,
      });
    } catch (err) {
      console.error(`Failed to deliver webhook to ${sub.webhook_url}:`, err);
    }
  }
}
