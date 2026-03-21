/**
 * API routes for the Pricing Service.
 */
import { Router, type Context } from "oak";
import type { PricingService } from "../services/pricing_service.ts";
import { PricingError } from "../services/pricing_service.ts";
import {
  GetPricingQuerySchema,
  QuoteRequestSchema,
  RateUpdateSchema,
} from "../types.ts";
import { ZodError } from "zod";

export function createPricingRouter(pricingService: PricingService): Router {
  const router = new Router();

  /**
   * GET /pricing/:unit_id?start=YYYY-MM-DD&end=YYYY-MM-DD
   * Get pricing for a date range.
   */
  router.get("/pricing/:unit_id", async (ctx: Context) => {
    try {
      const unitId = ctx.params.unit_id;
      if (!unitId) {
        ctx.response.status = 400;
        ctx.response.body = { error: "unit_id is required" };
        return;
      }

      const url = ctx.request.url;
      const query = GetPricingQuerySchema.parse({
        start: url.searchParams.get("start"),
        end: url.searchParams.get("end"),
      });

      const prices = await pricingService.getPricing(
        unitId,
        query.start,
        query.end,
      );

      ctx.response.status = 200;
      ctx.response.body = {
        unitId,
        startDate: query.start,
        endDate: query.end,
        prices,
      };
    } catch (err) {
      handleError(ctx, err);
    }
  });

  /**
   * POST /pricing/quote
   * Generate a price quote for a booking.
   */
  router.post("/pricing/quote", async (ctx: Context) => {
    try {
      const body = await ctx.request.body({ type: "json" }).value;
      const request = QuoteRequestSchema.parse(body);
      const quote = await pricingService.generateQuote(request);

      ctx.response.status = 200;
      ctx.response.body = quote;
    } catch (err) {
      handleError(ctx, err);
    }
  });

  /**
   * PUT /pricing/:unit_id/rates
   * Update rates for a unit.
   */
  router.put("/pricing/:unit_id/rates", async (ctx: Context) => {
    try {
      const unitId = ctx.params.unit_id;
      if (!unitId) {
        ctx.response.status = 400;
        ctx.response.body = { error: "unit_id is required" };
        return;
      }

      const body = await ctx.request.body({ type: "json" }).value;
      const { rates } = RateUpdateSchema.parse(body);

      const updated = await pricingService.updateRates(unitId, rates);

      ctx.response.status = 200;
      ctx.response.body = {
        unitId,
        updated: updated.length,
        rates: updated,
      };
    } catch (err) {
      handleError(ctx, err);
    }
  });

  return router;
}

function handleError(ctx: Context, err: unknown): void {
  if (err instanceof ZodError) {
    ctx.response.status = 400;
    ctx.response.body = {
      error: "Validation error",
      details: err.errors,
    };
  } else if (err instanceof PricingError) {
    ctx.response.status = 400;
    ctx.response.body = {
      error: err.message,
      code: err.code,
    };
  } else {
    console.error("[Routes] Unhandled error:", err);
    ctx.response.status = 500;
    ctx.response.body = {
      error: "Internal server error",
    };
  }
}
