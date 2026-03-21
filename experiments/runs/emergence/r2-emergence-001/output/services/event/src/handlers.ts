import { EventService, ValidationError } from "./service.ts";

/**
 * Minimal request/response abstraction for handlers.
 */
export interface HandlerRequest {
  body: unknown;
  params: Record<string, string>;
  query: Record<string, string>;
}

export interface HandlerResponse {
  status: number;
  body: unknown;
}

/**
 * Create handler for POST /events
 */
export function createPublishHandler(service: EventService) {
  return async (req: HandlerRequest): Promise<HandlerResponse> => {
    try {
      const { event, created } = await service.publishEvent(
        req.body as { topic: string; payload: Record<string, unknown>; source: string; timestamp?: string }
      );

      return {
        status: created ? 201 : 200,
        body: event,
      };
    } catch (err) {
      if (err instanceof ValidationError) {
        return {
          status: 400,
          body: { error: "validation_error", message: err.message, status: 400 },
        };
      }
      console.error("Publish event error:", err);
      return {
        status: 500,
        body: { error: "internal_error", message: "Internal server error", status: 500 },
      };
    }
  };
}

/**
 * Create handler for GET /events/:event_id
 */
export function createGetEventHandler(service: EventService) {
  return async (req: HandlerRequest): Promise<HandlerResponse> => {
    try {
      const eventId = req.params.event_id;
      const event = await service.getEvent(eventId);

      if (!event) {
        return {
          status: 404,
          body: { error: "not_found", message: `Event ${eventId} not found`, status: 404 },
        };
      }

      return { status: 200, body: event };
    } catch (err) {
      if (err instanceof ValidationError) {
        return {
          status: 400,
          body: { error: "validation_error", message: err.message, status: 400 },
        };
      }
      console.error("Get event error:", err);
      return {
        status: 500,
        body: { error: "internal_error", message: "Internal server error", status: 500 },
      };
    }
  };
}

/**
 * Create handler for GET /events?topic=&since=&limit=
 */
export function createQueryEventsHandler(service: EventService) {
  return async (req: HandlerRequest): Promise<HandlerResponse> => {
    try {
      const query = {
        topic: req.query.topic || undefined,
        since: req.query.since || undefined,
        limit: req.query.limit ? parseInt(req.query.limit, 10) : undefined,
      };

      const events = await service.queryEvents(query);
      return {
        status: 200,
        body: { events, count: events.length },
      };
    } catch (err) {
      if (err instanceof ValidationError) {
        return {
          status: 400,
          body: { error: "validation_error", message: err.message, status: 400 },
        };
      }
      console.error("Query events error:", err);
      return {
        status: 500,
        body: { error: "internal_error", message: "Internal server error", status: 500 },
      };
    }
  };
}

/**
 * Create handler for POST /events/subscribe
 */
export function createSubscribeHandler(service: EventService) {
  return async (req: HandlerRequest): Promise<HandlerResponse> => {
    try {
      const subscription = await service.subscribe(
        req.body as { topic: string; webhook_url: string; secret?: string }
      );

      return { status: 201, body: subscription };
    } catch (err) {
      if (err instanceof ValidationError) {
        return {
          status: 400,
          body: { error: "validation_error", message: err.message, status: 400 },
        };
      }
      console.error("Subscribe error:", err);
      return {
        status: 500,
        body: { error: "internal_error", message: "Internal server error", status: 500 },
      };
    }
  };
}
