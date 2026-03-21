import { EventService } from "./service.ts";
import { validatePublishRequest, validateSubscribeRequest, ValidationError } from "./validation.ts";
import { isValidEventType } from "./validation.ts";
import { ApiResponse, EventQuery, EventType } from "./types.ts";

/**
 * Creates an HTTP request handler for the Event Service.
 */
export function createRouter(service: EventService): (req: Request) => Promise<Response> {
  return async (req: Request): Promise<Response> => {
    const url = new URL(req.url);
    const path = url.pathname;
    const method = req.method;

    try {
      // Health check
      if (path === "/health" && method === "GET") {
        return jsonResponse(200, {
          success: true,
          data: { status: "healthy", service: "event", timestamp: new Date().toISOString() },
        });
      }

      // POST /events/subscribe — must come before /events/:event_id pattern
      if (path === "/events/subscribe" && method === "POST") {
        return await handleSubscribe(req, service);
      }

      // POST /events — Publish a new event
      if (path === "/events" && method === "POST") {
        return await handlePublishEvent(req, service);
      }

      // GET /events?type=&since=&limit= — Query events
      if (path === "/events" && method === "GET") {
        return await handleQueryEvents(url, service);
      }

      // GET /events/:event_id — Get event by ID
      const eventIdMatch = path.match(/^\/events\/([a-f0-9]{64})$/);
      if (eventIdMatch && method === "GET") {
        return await handleGetEvent(eventIdMatch[1], service);
      }

      // 404
      return jsonResponse(404, {
        success: false,
        error: `Not found: ${method} ${path}`,
      });
    } catch (err) {
      if (err instanceof ValidationError) {
        return jsonResponse(400, {
          success: false,
          error: err.message,
        });
      }

      console.error("Unhandled error:", err);
      return jsonResponse(500, {
        success: false,
        error: "Internal server error",
      });
    }
  };
}

async function handlePublishEvent(
  req: Request,
  service: EventService
): Promise<Response> {
  const body = await req.json();
  const request = validatePublishRequest(body);
  const { event, created } = await service.publishEvent(request);

  return jsonResponse(created ? 201 : 200, {
    success: true,
    data: event,
    message: created ? "Event published" : "Event already exists (idempotent)",
  });
}

async function handleGetEvent(
  eventId: string,
  service: EventService
): Promise<Response> {
  const event = await service.getEvent(eventId);

  if (!event) {
    return jsonResponse(404, {
      success: false,
      error: `Event not found: ${eventId}`,
    });
  }

  return jsonResponse(200, {
    success: true,
    data: event,
  });
}

async function handleQueryEvents(
  url: URL,
  service: EventService
): Promise<Response> {
  const query: EventQuery = {};

  const type = url.searchParams.get("type");
  if (type) {
    if (!isValidEventType(type)) {
      return jsonResponse(400, {
        success: false,
        error: `Invalid event type: ${type}`,
      });
    }
    query.type = type as EventType;
  }

  const since = url.searchParams.get("since");
  if (since) {
    if (isNaN(Date.parse(since))) {
      return jsonResponse(400, {
        success: false,
        error: "Invalid 'since' parameter: must be ISO 8601 date",
      });
    }
    query.since = since;
  }

  const until = url.searchParams.get("until");
  if (until) {
    if (isNaN(Date.parse(until))) {
      return jsonResponse(400, {
        success: false,
        error: "Invalid 'until' parameter: must be ISO 8601 date",
      });
    }
    query.until = until;
  }

  const limit = url.searchParams.get("limit");
  if (limit) {
    const n = parseInt(limit, 10);
    if (isNaN(n) || n < 1 || n > 1000) {
      return jsonResponse(400, {
        success: false,
        error: "Invalid 'limit' parameter: must be 1-1000",
      });
    }
    query.limit = n;
  }

  const offset = url.searchParams.get("offset");
  if (offset) {
    const n = parseInt(offset, 10);
    if (isNaN(n) || n < 0) {
      return jsonResponse(400, {
        success: false,
        error: "Invalid 'offset' parameter: must be >= 0",
      });
    }
    query.offset = n;
  }

  const events = await service.queryEvents(query);

  return jsonResponse(200, {
    success: true,
    data: events,
  });
}

async function handleSubscribe(
  req: Request,
  service: EventService
): Promise<Response> {
  const body = await req.json();
  const request = validateSubscribeRequest(body);
  const subscription = await service.subscribe(request);

  return jsonResponse(201, {
    success: true,
    data: subscription,
    message: "Subscription created",
  });
}

function jsonResponse(status: number, body: ApiResponse<unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}
