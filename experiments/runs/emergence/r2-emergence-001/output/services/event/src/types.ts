// Event types supported by the platform
export const EVENT_TYPES = [
  "availability.updated",
  "pricing.updated",
  "booking.created",
  "booking.confirmed",
  "booking.cancelled",
  "property.updated",
  "sync.completed",
  "payment.processed",
] as const;

export type EventType = typeof EVENT_TYPES[number];

// Core event structure
export interface WanderEvent {
  event_id: string;           // SHA-256 hash of canonical payload
  event_type: EventType;
  source: string;             // Service that produced the event
  payload: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  created_at: string;         // ISO 8601 timestamp
  published_at?: string;      // When it was published to Kafka
  version: number;
}

// Request to publish an event (event_id computed server-side)
export interface PublishEventRequest {
  event_type: EventType;
  source: string;
  payload: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

// Query parameters for listing events
export interface EventQuery {
  type?: EventType;
  since?: string;  // ISO 8601
  until?: string;  // ISO 8601
  limit?: number;
  offset?: number;
}

// Webhook subscription
export interface Subscription {
  subscription_id: string;
  event_type: EventType;
  webhook_url: string;
  secret?: string;
  active: boolean;
  created_at: string;
  updated_at: string;
  last_delivery?: string;
  failure_count: number;
}

// Request to create a subscription
export interface SubscribeRequest {
  event_type: EventType;
  webhook_url: string;
  secret?: string;
}

// API response wrapper
export interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: string;
  message?: string;
}

// Event bus interface (Kafka or in-memory)
export interface EventBus {
  publish(event: WanderEvent): Promise<void>;
  subscribe(eventType: EventType, handler: (event: WanderEvent) => Promise<void>): void;
  unsubscribe(eventType: EventType, handler: (event: WanderEvent) => Promise<void>): void;
  close(): Promise<void>;
}

// Event store interface (PostgreSQL or in-memory)
export interface EventStore {
  save(event: WanderEvent): Promise<{ created: boolean }>;
  getById(eventId: string): Promise<WanderEvent | null>;
  query(params: EventQuery): Promise<WanderEvent[]>;
  close(): Promise<void>;
}

// Subscription store interface
export interface SubscriptionStore {
  create(sub: SubscribeRequest): Promise<Subscription>;
  getByEventType(eventType: EventType): Promise<Subscription[]>;
  getById(subscriptionId: string): Promise<Subscription | null>;
  deactivate(subscriptionId: string): Promise<boolean>;
  updateDelivery(subscriptionId: string, success: boolean): Promise<void>;
  close(): Promise<void>;
}
