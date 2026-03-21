// ============================================================
// Property Service - Integration Tests (API)
// ============================================================
// These tests exercise the full HTTP API using an in-memory
// backing store (no PostgreSQL/Redis required).
// ============================================================

import {
  assertEquals,
  assertExists,
} from "https://deno.land/std@0.208.0/assert/mod.ts";
import { describe, it, beforeEach, afterEach } from "https://deno.land/std@0.208.0/testing/bdd.ts";
import { Application, Router } from "https://deno.land/x/oak@v12.6.1/mod.ts";
import { InMemoryPropertyRepository } from "../../src/repositories/property_repository.ts";
import { InMemoryUnitRepository } from "../../src/repositories/unit_repository.ts";
import { InMemoryPlatformRepository } from "../../src/repositories/platform_repository.ts";
import { InMemoryBrandRepository } from "../../src/repositories/brand_repository.ts";
import { InMemoryCache } from "../../src/cache/redis_cache.ts";
import { SettingsResolver } from "../../src/services/settings_resolver.ts";
import { PropertyService } from "../../src/services/property_service.ts";
import { UnitService } from "../../src/services/unit_service.ts";
import { PropertyHandler } from "../../src/handlers/property_handler.ts";
import { UnitHandler } from "../../src/handlers/unit_handler.ts";
import { createRouter } from "../../src/router.ts";
import { errorHandler } from "../../src/middleware/error_handler.ts";
import type { PlatformDefaults, Brand } from "../../src/models/types.ts";

function createTestApp() {
  const platformRepo = new InMemoryPlatformRepository();
  platformRepo.setDefaults({
    id: "platform-1",
    settings: {
      check_in_time: "16:00",
      check_out_time: "11:00",
      min_stay_nights: 1,
      cancellation_policy: "moderate",
    },
    created_at: new Date(),
    updated_at: new Date(),
  });

  const brandRepo = new InMemoryBrandRepository();
  brandRepo.addBrand({
    id: "brand-1",
    name: "Wander",
    slug: "wander",
    settings: {
      cancellation_policy: "flexible",
      check_in_time: "15:00",
    },
    is_active: true,
    created_at: new Date(),
    updated_at: new Date(),
  });

  const propertyRepo = new InMemoryPropertyRepository();
  const unitRepo = new InMemoryUnitRepository();
  const cache = new InMemoryCache();

  const resolver = new SettingsResolver({ platformRepo, brandRepo, cache });
  const propertyService = new PropertyService(propertyRepo, cache, resolver);
  const unitService = new UnitService(unitRepo, propertyRepo, cache, resolver);

  const propertyHandler = new PropertyHandler(propertyService);
  const unitHandler = new UnitHandler(unitService);
  const router = createRouter(propertyHandler, unitHandler);

  const app = new Application();
  app.use(errorHandler);
  app.use(router.routes());
  app.use(router.allowedMethods());

  return app;
}

let controller: AbortController;
let port: number;

async function startServer(app: Application): Promise<number> {
  port = 9100 + Math.floor(Math.random() * 900);
  controller = new AbortController();

  const listenPromise = app.listen({
    port,
    signal: controller.signal,
  });

  // Wait a bit for server to start
  await new Promise((r) => setTimeout(r, 200));

  // Don't await the listen promise; it only resolves when the server stops
  listenPromise.catch(() => {
    // Expected when we abort
  });

  return port;
}

function stopServer() {
  controller?.abort();
}

function url(path: string) {
  return `http://localhost:${port}${path}`;
}

describe("Property API Integration", () => {
  let app: Application;

  beforeEach(async () => {
    app = createTestApp();
    await startServer(app);
  });

  afterEach(() => {
    stopServer();
  });

  it("GET /health should return healthy", async () => {
    const res = await fetch(url("/health"));
    assertEquals(res.status, 200);
    const body = await res.json();
    assertEquals(body.status, "healthy");
    assertEquals(body.service, "property");
  });

  it("POST /properties should create a property", async () => {
    const res = await fetch(url("/properties"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "Mountain Retreat",
        slug: "mountain-retreat",
        brand_id: "brand-1",
        description: "A peaceful mountain getaway",
        settings: { check_in_time: "14:00" },
      }),
    });

    assertEquals(res.status, 201);
    const body = await res.json();
    assertEquals(body.success, true);
    assertEquals(body.data.name, "Mountain Retreat");
    assertEquals(body.data.slug, "mountain-retreat");
    assertExists(body.data.id);
  });

  it("GET /properties/:id should return property with resolved settings", async () => {
    // Create first
    const createRes = await fetch(url("/properties"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "Beach Villa",
        slug: "beach-villa",
        brand_id: "brand-1",
        settings: { check_in_time: "14:00", pool: true },
      }),
    });
    const createBody = await createRes.json();
    const propertyId = createBody.data.id;

    // Get
    const res = await fetch(url(`/properties/${propertyId}`));
    assertEquals(res.status, 200);
    const body = await res.json();
    assertEquals(body.success, true);
    assertEquals(body.data.name, "Beach Villa");
    assertExists(body.data.resolved_settings);
    assertEquals(body.data.resolved_settings.check_in_time, "14:00"); // property
    assertEquals(body.data.resolved_settings.cancellation_policy, "flexible"); // brand
    assertEquals(body.data.resolved_settings.check_out_time, "11:00"); // platform
  });

  it("GET /properties/:id should return 404 for non-existent", async () => {
    const res = await fetch(url("/properties/non-existent-id"));
    assertEquals(res.status, 404);
    const body = await res.json();
    assertEquals(body.success, false);
  });

  it("PUT /properties/:id should update property", async () => {
    const createRes = await fetch(url("/properties"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Old Name", slug: "old-slug" }),
    });
    const createBody = await createRes.json();
    const propertyId = createBody.data.id;

    const res = await fetch(url(`/properties/${propertyId}`), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "New Name", status: "active" }),
    });

    assertEquals(res.status, 200);
    const body = await res.json();
    assertEquals(body.data.name, "New Name");
    assertEquals(body.data.status, "active");
  });

  it("POST /properties should reject duplicate slug", async () => {
    await fetch(url("/properties"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Prop 1", slug: "unique-slug" }),
    });

    const res = await fetch(url("/properties"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Prop 2", slug: "unique-slug" }),
    });

    assertEquals(res.status, 409);
    const body = await res.json();
    assertEquals(body.success, false);
  });

  it("POST /properties/:id/units should create a unit", async () => {
    // Create property first
    const propRes = await fetch(url("/properties"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "Test Property",
        slug: "test-prop-units",
        brand_id: "brand-1",
        settings: { min_stay_nights: 3 },
      }),
    });
    const propBody = await propRes.json();
    const propertyId = propBody.data.id;

    // Create unit
    const res = await fetch(url(`/properties/${propertyId}/units`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "Suite 101",
        slug: "suite-101",
        bedrooms: 2,
        bathrooms: 1.5,
        max_guests: 4,
        settings: { check_in_time: "13:00" },
      }),
    });

    assertEquals(res.status, 201);
    const body = await res.json();
    assertEquals(body.success, true);
    assertEquals(body.data.name, "Suite 101");
    assertEquals(body.data.property_id, propertyId);
    assertEquals(body.data.bedrooms, 2);
  });

  it("GET /units/:id should return unit with 4-tier resolved settings", async () => {
    // Create property
    const propRes = await fetch(url("/properties"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "Resort",
        slug: "resort-4tier",
        brand_id: "brand-1",
        settings: { min_stay_nights: 3, check_in_time: "14:00" },
      }),
    });
    const propBody = await propRes.json();
    const propertyId = propBody.data.id;

    // Create unit
    const unitRes = await fetch(url(`/properties/${propertyId}/units`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "Penthouse",
        slug: "penthouse",
        settings: { check_in_time: "12:00", vip_access: true },
      }),
    });
    const unitBody = await unitRes.json();
    const unitId = unitBody.data.id;

    // Get unit
    const res = await fetch(url(`/units/${unitId}`));
    assertEquals(res.status, 200);
    const body = await res.json();
    assertEquals(body.success, true);
    assertExists(body.data.resolved_settings);

    // Tier 4: Unit override
    assertEquals(body.data.resolved_settings.check_in_time, "12:00");
    assertEquals(body.data.resolved_settings.vip_access, true);

    // Tier 3: Property override
    assertEquals(body.data.resolved_settings.min_stay_nights, 3);

    // Tier 2: Brand override
    assertEquals(body.data.resolved_settings.cancellation_policy, "flexible");

    // Tier 1: Platform default
    assertEquals(body.data.resolved_settings.check_out_time, "11:00");
  });

  it("GET /properties/:id/units should list units", async () => {
    // Create property
    const propRes = await fetch(url("/properties"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Multi-Unit", slug: "multi-unit" }),
    });
    const propBody = await propRes.json();
    const propertyId = propBody.data.id;

    // Create multiple units
    for (let i = 1; i <= 3; i++) {
      await fetch(url(`/properties/${propertyId}/units`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: `Unit ${i}`, slug: `unit-${i}` }),
      });
    }

    const res = await fetch(url(`/properties/${propertyId}/units`));
    assertEquals(res.status, 200);
    const body = await res.json();
    assertEquals(body.success, true);
    assertEquals(body.total, 3);
    assertEquals(body.data.length, 3);
  });

  it("PUT /units/:id should update unit", async () => {
    // Create property and unit
    const propRes = await fetch(url("/properties"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Prop", slug: "prop-update-unit" }),
    });
    const propBody = await propRes.json();

    const unitRes = await fetch(url(`/properties/${propBody.data.id}/units`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Unit Old", slug: "unit-old", bedrooms: 1 }),
    });
    const unitBody = await unitRes.json();

    const res = await fetch(url(`/units/${unitBody.data.id}`), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Unit New", bedrooms: 3 }),
    });

    assertEquals(res.status, 200);
    const body = await res.json();
    assertEquals(body.data.name, "Unit New");
    assertEquals(body.data.bedrooms, 3);
  });

  it("GET /units/:id should return 404 for non-existent", async () => {
    const res = await fetch(url("/units/non-existent-id"));
    assertEquals(res.status, 404);
  });
});
