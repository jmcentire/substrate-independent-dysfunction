import { EVENT_TOPICS, EventTopic, PublishEventRequest, SubscribeRequest } from "./types.ts";

export interface ValidationResult {
  valid: boolean;
  errors: string[];
}

/**
 * Validate a publish event request.
 */
export function validatePublishRequest(req: unknown): ValidationResult {
  const errors: string[] = [];

  if (!req || typeof req !== "object") {
    return { valid: false, errors: ["Request body must be a JSON object"] };
  }

  const body = req as Record<string, unknown>;

  // Validate topic
  if (!body.topic || typeof body.topic !== "string") {
    errors.push("'topic' is required and must be a string");
  } else if (!EVENT_TOPICS.includes(body.topic as EventTopic)) {
    errors.push(
      `Invalid topic '${body.topic}'. Must be one of: ${EVENT_TOPICS.join(", ")}`
    );
  }

  // Validate payload
  if (!body.payload || typeof body.payload !== "object" || Array.isArray(body.payload)) {
    errors.push("'payload' is required and must be a JSON object");
  }

  // Validate source
  if (!body.source || typeof body.source !== "string") {
    errors.push("'source' is required and must be a string");
  }

  // Validate optional timestamp
  if (body.timestamp !== undefined) {
    if (typeof body.timestamp !== "string") {
      errors.push("'timestamp' must be an ISO 8601 string if provided");
    } else {
      const date = new Date(body.timestamp);
      if (isNaN(date.getTime())) {
        errors.push("'timestamp' must be a valid ISO 8601 date string");
      }
    }
  }

  return { valid: errors.length === 0, errors };
}

/**
 * Validate a subscribe request.
 */
export function validateSubscribeRequest(req: unknown): ValidationResult {
  const errors: string[] = [];

  if (!req || typeof req !== "object") {
    return { valid: false, errors: ["Request body must be a JSON object"] };
  }

  const body = req as Record<string, unknown>;

  // Validate topic
  if (!body.topic || typeof body.topic !== "string") {
    errors.push("'topic' is required and must be a string");
  } else if (!EVENT_TOPICS.includes(body.topic as EventTopic)) {
    errors.push(
      `Invalid topic '${body.topic}'. Must be one of: ${EVENT_TOPICS.join(", ")}`
    );
  }

  // Validate webhook_url
  if (!body.webhook_url || typeof body.webhook_url !== "string") {
    errors.push("'webhook_url' is required and must be a string");
  } else {
    try {
      new URL(body.webhook_url);
    } catch {
      errors.push("'webhook_url' must be a valid URL");
    }
  }

  // Validate optional secret
  if (body.secret !== undefined && typeof body.secret !== "string") {
    errors.push("'secret' must be a string if provided");
  }

  return { valid: errors.length === 0, errors };
}

/**
 * Validate topic-specific payload schema.
 * Lightweight validation ensuring required fields per topic.
 */
export function validatePayloadForTopic(
  topic: EventTopic,
  payload: Record<string, unknown>,
): ValidationResult {
  const errors: string[] = [];

  switch (topic) {
    case "availability.updated":
      if (!payload.property_id) errors.push("payload.property_id is required for availability.updated");
      break;
    case "pricing.updated":
      if (!payload.property_id) errors.push("payload.property_id is required for pricing.updated");
      break;
    case "booking.created":
      if (!payload.booking_id) errors.push("payload.booking_id is required for booking.created");
      break;
    case "booking.confirmed":
      if (!payload.booking_id) errors.push("payload.booking_id is required for booking.confirmed");
      break;
    case "property.updated":
      if (!payload.property_id) errors.push("payload.property_id is required for property.updated");
      break;
    case "sync.completed":
      if (!payload.sync_id && !payload.property_id) {
        errors.push("payload.sync_id or payload.property_id is required for sync.completed");
      }
      break;
    case "payment.processed":
      if (!payload.payment_id && !payload.booking_id) {
        errors.push("payload.payment_id or payload.booking_id is required for payment.processed");
      }
      break;
  }

  return { valid: errors.length === 0, errors };
}
