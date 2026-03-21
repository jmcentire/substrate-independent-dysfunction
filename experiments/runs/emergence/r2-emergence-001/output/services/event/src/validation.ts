import { EVENT_TYPES, EventType, PublishEventRequest, SubscribeRequest } from "./types.ts";

export class ValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ValidationError";
  }
}

/**
 * Validates that a string is a known event type.
 */
export function isValidEventType(type: string): type is EventType {
  return (EVENT_TYPES as readonly string[]).includes(type);
}

/**
 * Validates a publish event request.
 */
export function validatePublishRequest(body: unknown): PublishEventRequest {
  if (!body || typeof body !== "object") {
    throw new ValidationError("Request body must be a JSON object");
  }

  const obj = body as Record<string, unknown>;

  // event_type
  if (!obj.event_type || typeof obj.event_type !== "string") {
    throw new ValidationError("event_type is required and must be a string");
  }
  if (!isValidEventType(obj.event_type)) {
    throw new ValidationError(
      `Invalid event_type '${obj.event_type}'. Must be one of: ${EVENT_TYPES.join(", ")}`
    );
  }

  // source
  if (!obj.source || typeof obj.source !== "string") {
    throw new ValidationError("source is required and must be a string");
  }
  if (obj.source.length > 255) {
    throw new ValidationError("source must be 255 characters or less");
  }

  // payload
  if (!obj.payload || typeof obj.payload !== "object" || Array.isArray(obj.payload)) {
    throw new ValidationError("payload is required and must be a JSON object");
  }

  // metadata (optional)
  if (obj.metadata !== undefined && obj.metadata !== null) {
    if (typeof obj.metadata !== "object" || Array.isArray(obj.metadata)) {
      throw new ValidationError("metadata must be a JSON object if provided");
    }
  }

  // Validate payload per event type
  validatePayloadForType(obj.event_type as EventType, obj.payload as Record<string, unknown>);

  return {
    event_type: obj.event_type as EventType,
    source: obj.source as string,
    payload: obj.payload as Record<string, unknown>,
    metadata: (obj.metadata as Record<string, unknown>) || undefined,
  };
}

/**
 * Validates payload structure for specific event types.
 */
function validatePayloadForType(eventType: EventType, payload: Record<string, unknown>): void {
  switch (eventType) {
    case "availability.updated":
      requireFields(payload, ["property_id"], "availability.updated payload");
      break;
    case "pricing.updated":
      requireFields(payload, ["property_id"], "pricing.updated payload");
      break;
    case "booking.created":
      requireFields(payload, ["booking_id", "property_id"], "booking.created payload");
      break;
    case "booking.confirmed":
      requireFields(payload, ["booking_id"], "booking.confirmed payload");
      break;
    case "booking.cancelled":
      requireFields(payload, ["booking_id"], "booking.cancelled payload");
      break;
    case "property.updated":
      requireFields(payload, ["property_id"], "property.updated payload");
      break;
    case "sync.completed":
      requireFields(payload, ["sync_id"], "sync.completed payload");
      break;
    case "payment.processed":
      requireFields(payload, ["payment_id"], "payment.processed payload");
      break;
  }
}

function requireFields(obj: Record<string, unknown>, fields: string[], context: string): void {
  for (const field of fields) {
    if (obj[field] === undefined || obj[field] === null) {
      throw new ValidationError(`${context} requires '${field}'`);
    }
  }
}

/**
 * Validates a subscribe request.
 */
export function validateSubscribeRequest(body: unknown): SubscribeRequest {
  if (!body || typeof body !== "object") {
    throw new ValidationError("Request body must be a JSON object");
  }

  const obj = body as Record<string, unknown>;

  // event_type
  if (!obj.event_type || typeof obj.event_type !== "string") {
    throw new ValidationError("event_type is required and must be a string");
  }
  if (!isValidEventType(obj.event_type)) {
    throw new ValidationError(
      `Invalid event_type '${obj.event_type}'. Must be one of: ${EVENT_TYPES.join(", ")}`
    );
  }

  // webhook_url
  if (!obj.webhook_url || typeof obj.webhook_url !== "string") {
    throw new ValidationError("webhook_url is required and must be a string");
  }
  try {
    new URL(obj.webhook_url);
  } catch {
    throw new ValidationError("webhook_url must be a valid URL");
  }

  // secret (optional)
  if (obj.secret !== undefined && typeof obj.secret !== "string") {
    throw new ValidationError("secret must be a string if provided");
  }

  return {
    event_type: obj.event_type as EventType,
    webhook_url: obj.webhook_url as string,
    secret: obj.secret as string | undefined,
  };
}
