/**
 * Oak Router setup for the Availability Service.
 */

import { Router } from "oak";
import { createRouteHandlers } from "./routes.ts";
import { AvailabilityService } from "./service.ts";

export function createRouter(service: AvailabilityService): Router {
  const router = new Router();
  const handlers = createRouteHandlers(service);

  // Health check
  router.get("/health", (ctx) => {
    handlers.healthCheck(ctx);
  });

  // Check availability for a unit
  router.get("/availability/:unit_id", async (ctx) => {
    await handlers.getAvailability({
      params: { unit_id: ctx.params.unit_id! },
      request: { url: ctx.request.url },
      response: ctx.response as unknown as { status: number; body: unknown },
    });
  });

  // Update availability for a unit
  router.put("/availability/:unit_id", async (ctx) => {
    await handlers.updateAvailability({
      params: { unit_id: ctx.params.unit_id! },
      request: ctx.request as unknown as { body: () => { type: string; value: Promise<unknown> } },
      response: ctx.response as unknown as { status: number; body: unknown },
    });
  });

  // Bulk availability check
  router.post("/availability/bulk", async (ctx) => {
    await handlers.bulkCheck({
      request: ctx.request as unknown as { body: () => { type: string; value: Promise<unknown> } },
      response: ctx.response as unknown as { status: number; body: unknown },
    });
  });

  return router;
}
