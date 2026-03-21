// ============================================================
// Unit Handlers
// ============================================================

import type { RouterContext } from "https://deno.land/x/oak@v12.6.1/mod.ts";
import type { UnitService } from "../services/unit_service.ts";

export class UnitHandler {
  constructor(private unitService: UnitService) {}

  /**
   * GET /units/:unit_id
   */
  getUnit = async (ctx: RouterContext<"/units/:unit_id">) => {
    const unitId = ctx.params.unit_id;

    if (!unitId) {
      ctx.response.status = 400;
      ctx.response.body = { success: false, error: "Unit ID is required" };
      return;
    }

    const unit = await this.unitService.getById(unitId);

    if (!unit) {
      ctx.response.status = 404;
      ctx.response.body = { success: false, error: "Unit not found" };
      return;
    }

    ctx.response.status = 200;
    ctx.response.body = { success: true, data: unit };
  };

  /**
   * GET /properties/:property_id/units
   */
  getUnitsByProperty = async (ctx: RouterContext<"/properties/:property_id/units">) => {
    const propertyId = ctx.params.property_id;
    const url = ctx.request.url;
    const page = parseInt(url.searchParams.get("page") || "1", 10);
    const perPage = parseInt(url.searchParams.get("per_page") || "50", 10);

    try {
      const result = await this.unitService.getByPropertyId(propertyId, { page, perPage });

      ctx.response.status = 200;
      ctx.response.body = {
        success: true,
        data: result.units,
        total: result.total,
        page,
        per_page: perPage,
      };
    } catch (error) {
      const status = (error as any).statusCode ?? 400;
      ctx.response.status = status;
      ctx.response.body = {
        success: false,
        error: error instanceof Error ? error.message : "Failed to list units",
      };
    }
  };

  /**
   * POST /properties/:property_id/units
   */
  createUnit = async (ctx: RouterContext<"/properties/:property_id/units">) => {
    const propertyId = ctx.params.property_id;
    const body = ctx.request.body();
    if (body.type !== "json") {
      ctx.response.status = 400;
      ctx.response.body = { success: false, error: "JSON body required" };
      return;
    }

    const data = await body.value;

    try {
      const unit = await this.unitService.create(propertyId, data);
      ctx.response.status = 201;
      ctx.response.body = { success: true, data: unit };
    } catch (error) {
      const status = (error as any).statusCode ?? 400;
      ctx.response.status = status;
      ctx.response.body = {
        success: false,
        error: error instanceof Error ? error.message : "Failed to create unit",
      };
    }
  };

  /**
   * PUT /units/:unit_id
   */
  updateUnit = async (ctx: RouterContext<"/units/:unit_id">) => {
    const unitId = ctx.params.unit_id;
    const body = ctx.request.body();
    if (body.type !== "json") {
      ctx.response.status = 400;
      ctx.response.body = { success: false, error: "JSON body required" };
      return;
    }

    const data = await body.value;

    try {
      const unit = await this.unitService.update(unitId, data);

      if (!unit) {
        ctx.response.status = 404;
        ctx.response.body = { success: false, error: "Unit not found" };
        return;
      }

      ctx.response.status = 200;
      ctx.response.body = { success: true, data: unit };
    } catch (error) {
      const status = (error as any).statusCode ?? 400;
      ctx.response.status = status;
      ctx.response.body = {
        success: false,
        error: error instanceof Error ? error.message : "Failed to update unit",
      };
    }
  };
}
