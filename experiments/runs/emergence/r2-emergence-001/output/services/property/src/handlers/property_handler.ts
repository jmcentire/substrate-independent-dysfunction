// ============================================================
// Property Handlers
// ============================================================

import type { RouterContext } from "https://deno.land/x/oak@v12.6.1/mod.ts";
import type { PropertyService } from "../services/property_service.ts";

export class PropertyHandler {
  constructor(private propertyService: PropertyService) {}

  /**
   * GET /properties/:property_id
   */
  getProperty = async (ctx: RouterContext<"/properties/:property_id">) => {
    const propertyId = ctx.params.property_id;

    if (!propertyId) {
      ctx.response.status = 400;
      ctx.response.body = { success: false, error: "Property ID is required" };
      return;
    }

    const property = await this.propertyService.getById(propertyId);

    if (!property) {
      ctx.response.status = 404;
      ctx.response.body = { success: false, error: "Property not found" };
      return;
    }

    ctx.response.status = 200;
    ctx.response.body = { success: true, data: property };
  };

  /**
   * POST /properties
   */
  createProperty = async (ctx: RouterContext<"/properties">) => {
    const body = ctx.request.body();
    if (body.type !== "json") {
      ctx.response.status = 400;
      ctx.response.body = { success: false, error: "JSON body required" };
      return;
    }

    const data = await body.value;

    try {
      const property = await this.propertyService.create(data);
      ctx.response.status = 201;
      ctx.response.body = { success: true, data: property };
    } catch (error) {
      const status = (error as any).statusCode ?? 400;
      ctx.response.status = status;
      ctx.response.body = {
        success: false,
        error: error instanceof Error ? error.message : "Failed to create property",
      };
    }
  };

  /**
   * PUT /properties/:property_id
   */
  updateProperty = async (ctx: RouterContext<"/properties/:property_id">) => {
    const propertyId = ctx.params.property_id;
    const body = ctx.request.body();
    if (body.type !== "json") {
      ctx.response.status = 400;
      ctx.response.body = { success: false, error: "JSON body required" };
      return;
    }

    const data = await body.value;

    try {
      const property = await this.propertyService.update(propertyId, data);

      if (!property) {
        ctx.response.status = 404;
        ctx.response.body = { success: false, error: "Property not found" };
        return;
      }

      ctx.response.status = 200;
      ctx.response.body = { success: true, data: property };
    } catch (error) {
      const status = (error as any).statusCode ?? 400;
      ctx.response.status = status;
      ctx.response.body = {
        success: false,
        error: error instanceof Error ? error.message : "Failed to update property",
      };
    }
  };
}
