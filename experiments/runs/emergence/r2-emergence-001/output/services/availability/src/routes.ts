/**
 * HTTP route handlers for the Availability Service API.
 */

import { AvailabilityService } from "./service.ts";
import type { UpdateAvailabilityRequest, BulkAvailabilityRequest } from "./types.ts";

/** Helper to parse and validate date strings */
function isValidDate(dateStr: string): boolean {
  const regex = /^\d{4}-\d{2}-\d{2}$/;
  if (!regex.test(dateStr)) return false;
  const date = new Date(dateStr);
  return !isNaN(date.getTime());
}

/** Helper to validate UUID format */
function isValidUUID(str: string): boolean {
  const regex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  return regex.test(str);
}

/** Create route handlers bound to a service instance */
export function createRouteHandlers(service: AvailabilityService) {
  return {
    /**
     * GET /availability/:unit_id?start=YYYY-MM-DD&end=YYYY-MM-DD
     * Check availability for a date range.
     */
    async getAvailability(ctx: {
      params: { unit_id: string };
      request: { url: URL };
      response: { status: number; body: unknown };
    }) {
      try {
        const unitId = ctx.params.unit_id;
        if (!unitId) {
          ctx.response.status = 400;
          ctx.response.body = { error: "unit_id is required" };
          return;
        }

        const url = ctx.request.url;
        const start = url.searchParams.get("start");
        const end = url.searchParams.get("end");

        if (!start || !end) {
          ctx.response.status = 400;
          ctx.response.body = { error: "start and end query parameters are required" };
          return;
        }

        if (!isValidDate(start) || !isValidDate(end)) {
          ctx.response.status = 400;
          ctx.response.body = { error: "Invalid date format. Use YYYY-MM-DD." };
          return;
        }

        if (start > end) {
          ctx.response.status = 400;
          ctx.response.body = { error: "start date must be before or equal to end date" };
          return;
        }

        const result = await service.checkAvailability(unitId, start, end);
        ctx.response.status = 200;
        ctx.response.body = result;
      } catch (error) {
        console.error("Error checking availability:", error);
        ctx.response.status = 500;
        ctx.response.body = { error: "Internal server error" };
      }
    },

    /**
     * PUT /availability/:unit_id
     * Update availability (block/unblock dates).
     */
    async updateAvailability(ctx: {
      params: { unit_id: string };
      request: { body: () => { type: string; value: Promise<unknown> } };
      response: { status: number; body: unknown };
    }) {
      try {
        const unitId = ctx.params.unit_id;
        if (!unitId) {
          ctx.response.status = 400;
          ctx.response.body = { error: "unit_id is required" };
          return;
        }

        const body = ctx.request.body();
        const data = await body.value as UpdateAvailabilityRequest;

        if (!data || !data.dates || !data.dates.start || !data.dates.end) {
          ctx.response.status = 400;
          ctx.response.body = { error: "Request body must include dates.start and dates.end" };
          return;
        }

        if (typeof data.available !== "boolean") {
          ctx.response.status = 400;
          ctx.response.body = { error: "available must be a boolean" };
          return;
        }

        if (!isValidDate(data.dates.start) || !isValidDate(data.dates.end)) {
          ctx.response.status = 400;
          ctx.response.body = { error: "Invalid date format. Use YYYY-MM-DD." };
          return;
        }

        if (data.dates.start > data.dates.end) {
          ctx.response.status = 400;
          ctx.response.body = { error: "start date must be before or equal to end date" };
          return;
        }

        const result = await service.updateAvailability(unitId, data);
        ctx.response.status = 200;
        ctx.response.body = result;
      } catch (error) {
        console.error("Error updating availability:", error);
        ctx.response.status = 500;
        ctx.response.body = { error: "Internal server error" };
      }
    },

    /**
     * POST /availability/bulk
     * Bulk availability check for multiple units.
     */
    async bulkCheck(ctx: {
      request: { body: () => { type: string; value: Promise<unknown> } };
      response: { status: number; body: unknown };
    }) {
      try {
        const body = ctx.request.body();
        const data = await body.value as BulkAvailabilityRequest;

        if (!data || !data.unitIds || !Array.isArray(data.unitIds)) {
          ctx.response.status = 400;
          ctx.response.body = { error: "unitIds array is required" };
          return;
        }

        if (!data.start || !data.end) {
          ctx.response.status = 400;
          ctx.response.body = { error: "start and end are required" };
          return;
        }

        if (!isValidDate(data.start) || !isValidDate(data.end)) {
          ctx.response.status = 400;
          ctx.response.body = { error: "Invalid date format. Use YYYY-MM-DD." };
          return;
        }

        if (data.unitIds.length > 100) {
          ctx.response.status = 400;
          ctx.response.body = { error: "Maximum 100 units per bulk request" };
          return;
        }

        const result = await service.bulkCheckAvailability(
          data.unitIds,
          data.start,
          data.end,
        );
        ctx.response.status = 200;
        ctx.response.body = result;
      } catch (error) {
        console.error("Error in bulk check:", error);
        ctx.response.status = 500;
        ctx.response.body = { error: "Internal server error" };
      }
    },

    /** GET /health - Health check endpoint */
    healthCheck(ctx: { response: { status: number; body: unknown } }) {
      ctx.response.status = 200;
      ctx.response.body = {
        status: "healthy",
        service: "availability",
        timestamp: new Date().toISOString(),
      };
    },
  };
}
