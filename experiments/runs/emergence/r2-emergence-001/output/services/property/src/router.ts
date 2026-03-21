// ============================================================
// Property Service - Router
// ============================================================

import { Router } from "https://deno.land/x/oak@v12.6.1/mod.ts";
import type { PropertyHandler } from "./handlers/property_handler.ts";
import type { UnitHandler } from "./handlers/unit_handler.ts";

export function createRouter(
  propertyHandler: PropertyHandler,
  unitHandler: UnitHandler
): Router {
  const router = new Router();

  // Health check
  router.get("/health", (ctx) => {
    ctx.response.status = 200;
    ctx.response.body = {
      status: "healthy",
      service: "property",
      timestamp: new Date().toISOString(),
    };
  });

  // Property endpoints
  router.get("/properties/:property_id", propertyHandler.getProperty as any);
  router.post("/properties", propertyHandler.createProperty as any);
  router.put("/properties/:property_id", propertyHandler.updateProperty as any);

  // Unit endpoints
  router.get("/properties/:property_id/units", unitHandler.getUnitsByProperty as any);
  router.post("/properties/:property_id/units", unitHandler.createUnit as any);
  router.get("/units/:unit_id", unitHandler.getUnit as any);
  router.put("/units/:unit_id", unitHandler.updateUnit as any);

  return router;
}
