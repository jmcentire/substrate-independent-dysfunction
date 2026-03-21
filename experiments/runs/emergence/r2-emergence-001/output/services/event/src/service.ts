import {
  EventBus,
  EventStore,
  EventType,
  EventQuery,
  PublishEventRequest,
  SubscribeRequest,
  Subscription,
  SubscriptionStore,
  WanderEvent,
} from "./types.ts";
import { computeEventId } from "./hash.ts";
import { Config } from "./config.ts";

export class EventService {
  constructor(
    private eventStore: EventStore,
    private eventBus: EventBus,
    private subscriptionStore: SubscriptionStore,
    private config: Config
  ) {
    // Set up webhook delivery when events are published to the bus
    // We listen on the bus and deliver to registered webhooks
  }

  /**
   * Publish an event. Computes content-addressable ID, stores the event,
   * and publishes to the event bus. Idempotent - same content = same ID.
   */
  async publishEvent(request: PublishEventRequest): Promise<{ event: WanderEvent; created: boolean }> {
    const eventId = await computeEventId(
      request.event_type,
      request.source,
      request.payload
    );

    const now = new Date().toISOString();

    const event: WanderEvent = {
      event_id: eventId,
      event_type: request.event_type,
      source: request.source,
      payload: request.payload,
      metadata: request.metadata || {},
      created_at: now,
      published_at: now,
      version: 1,
    };

    const { created } = await this.eventStore.save(event);

    if (created) {
      // Publish to event bus (Kafka or in-memory)
      await this.eventBus.publish(event);

      // Deliver to webhook subscribers (fire and forget)
      this.deliverToWebhooks(event).catch((err) => {
        console.error("Webhook delivery error:", err);
      });
    }

    // Return the stored event (may have been previously stored)
    const storedEvent = await this.eventStore.getById(eventId);
    return { event: storedEvent || event, created };
  }

  /**
   * Get a single event by its content-addressable ID.
   */
  async getEvent(eventId: string): Promise<WanderEvent | null> {
    return this.eventStore.getById(eventId);
  }

  /**
   * Query events by type and time range.
   */
  async queryEvents(query: EventQuery): Promise<WanderEvent[]> {
    return this.eventStore.query(query);
  }

  /**
   * Register a webhook subscription for an event type.
   */
  async subscribe(request: SubscribeRequest): Promise<Subscription> {
    return this.subscriptionStore.create(request);
  }

  /**
   * Deliver an event to all registered webhook subscribers for its type.
   */
  private async deliverToWebhooks(event: WanderEvent): Promise<void> {
    const subs = await this.subscriptionStore.getByEventType(event.event_type);

    const deliveryPromises = subs.map(async (sub) => {
      try {
        const controller = new AbortController();
        const timeout = setTimeout(
          () => controller.abort(),
          this.config.webhookTimeoutMs
        );

        try {
          const headers: Record<string, string> = {
            "Content-Type": "application/json",
            "X-Event-ID": event.event_id,
            "X-Event-Type": event.event_type,
          };

          if (sub.secret) {
            // HMAC signature for webhook verification
            const encoder = new TextEncoder();
            const key = await crypto.subtle.importKey(
              "raw",
              encoder.encode(sub.secret),
              { name: "HMAC", hash: "SHA-256" },
              false,
              ["sign"]
            );
            const body = JSON.stringify(event);
            const signature = await crypto.subtle.sign(
              "HMAC",
              key,
              encoder.encode(body)
            );
            const sigHex = Array.from(new Uint8Array(signature))
              .map((b) => b.toString(16).padStart(2, "0"))
              .join("");
            headers["X-Webhook-Signature"] = `sha256=${sigHex}`;
          }

          const response = await fetch(sub.webhook_url, {
            method: "POST",
            headers,
            body: JSON.stringify(event),
            signal: controller.signal,
          });

          const success = response.ok;
          await this.subscriptionStore.updateDelivery(
            sub.subscription_id,
            success
          );

          if (!success) {
            console.warn(
              `Webhook delivery failed for ${sub.subscription_id}: HTTP ${response.status}`
            );
          }
        } finally {
          clearTimeout(timeout);
        }
      } catch (err) {
        console.error(
          `Webhook delivery error for ${sub.subscription_id}:`,
          err
        );
        await this.subscriptionStore.updateDelivery(
          sub.subscription_id,
          false
        );
      }
    });

    await Promise.allSettled(deliveryPromises);
  }
}
