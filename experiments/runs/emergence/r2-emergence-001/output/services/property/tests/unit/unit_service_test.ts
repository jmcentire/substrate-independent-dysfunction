// ============================================================
// Unit Service - Unit Tests
// ============================================================

import {
  assertEquals,
  assertRejects,
  assertExists,
} from "https://deno.land/std@0.208.0/assert/mod.ts";
import { describe, it, beforeEach } from "https://deno.land/std@0.208.0/testing/bdd.ts";
import { UnitService, UnitError } from "../../src/services/unit_service.ts";
import { InMemoryUnitRepository } from "../../src/repositories/unit_repository.ts";
import { InMemoryPropertyRepository } from "../../src/repositories/property_repository.ts";
import { InMemoryPlatformRepository } from "../../src/repositories/platform_repository.ts";
import { InMemoryBrandRepository } from "../../src/repositories/brand_repository.ts";
import { InMemoryCache } from "../../src/cache/redis_cache.ts";
import { SettingsResolver } from "../../src/services/settings_resolver.ts";
import type { Property, Brand, PlatformDefaults } from "../../src/models/types.ts";

describe("UnitService", () => {
  let unitRepo: InMemoryUnitRepository;
  let propertyRepo: InMemoryPropertyRepository;
  let brandRepo: InMemoryBrandRepository;
  let cache: InMemoryCache;
  let resolver: SettingsResolver;
  let service: UnitService;

  const platformDefaults: PlatformDefaults = {
    id: "platform-1",
    settings: {
      check_in_time: "16:00",
      check_out_time: "11:00",
      min_stay_nights: 1,
      cancellation_policy: "moderate",
      pet_policy: "not_allowed",
    },
    created_at: new Date(),
    updated_at: new Date(),
  };

  const testBrand: Brand = {
    id: "brand-1",
    name: "Test Brand",
    slug: "test-brand",
    settings: {
      cancellation_policy: "flexible",
      check_in_time: "15:00",
    },
    is_active: true,
    created_at: new Date(),
    updated_at: new Date(),
  };

  const testProperty: Property = {
    id: "property-1",
    brand_id: "brand-1",
    name: "Test Property",
    slug: "test-property",
    description: null,
    address: null,
    location: null,
    amenities: [],
    images: [],
    settings: {
      check_in_time: "14:00",
      min_stay_nights: 2,
    },
    status: "active",
    is_active: true,
    created_at: new Date(),
    updated_at: new Date(),
  };

  beforeEach(() => {
    unitRepo = new InMemoryUnitRepository();
    propertyRepo = new InMemoryPropertyRepository();
    propertyRepo.addProperty(testProperty);

    brandRepo = new InMemoryBrandRepository();
    brandRepo.addBrand(testBrand);

    const platformRepo = new InMemoryPlatformRepository();
    platformRepo.setDefaults(platformDefaults);

    cache = new InMemoryCache();
    resolver = new SettingsResolver({ platformRepo, brandRepo, cache });
    service = new UnitService(unitRepo, propertyRepo, cache, resolver);
  });

  it("should create a unit", async () => {
    const unit = await service.create("property-1", {
      name: "Suite A",
      slug: "suite-a",
      bedrooms: 2,
      bathrooms: 1.5,
      max_guests: 4,
      settings: { check_in_time: "13:00" },
    });

    assertExists(unit.id);
    assertEquals(unit.name, "Suite A");
    assertEquals(unit.property_id, "property-1");
    assertEquals(unit.bedrooms, 2);
    assertEquals(unit.settings.check_in_time, "13:00");
  });

  it("should reject creation for non-existent property", async () => {
    await assertRejects(
      async () => {
        await service.create("non-existent", {
          name: "Suite",
          slug: "suite",
        });
      },
      UnitError,
      "Property not found"
    );
  });

  it("should reject missing name", async () => {
    await assertRejects(
      async () => {
        await service.create("property-1", {
          name: "",
          slug: "empty",
        });
      },
      UnitError,
      "required"
    );
  });

  it("should get a unit with full 4-tier resolved settings", async () => {
    const created = await service.create("property-1", {
      name: "Suite A",
      slug: "suite-a",
      settings: {
        check_in_time: "13:00",
        special_feature: "jacuzzi",
      },
    });

    const result = await service.getById(created.id);

    assertExists(result);
    assertExists(result!.resolved_settings);

    // Tier 4: Unit override wins
    assertEquals(result!.resolved_settings.check_in_time, "13:00");
    assertEquals(result!.resolved_settings.special_feature, "jacuzzi");

    // Tier 3: Property override
    assertEquals(result!.resolved_settings.min_stay_nights, 2);

    // Tier 2: Brand override
    assertEquals(result!.resolved_settings.cancellation_policy, "flexible");

    // Tier 1: Platform default
    assertEquals(result!.resolved_settings.check_out_time, "11:00");
    assertEquals(result!.resolved_settings.pet_policy, "not_allowed");
  });

  it("should return null for non-existent unit", async () => {
    const result = await service.getById("non-existent");
    assertEquals(result, null);
  });

  it("should list units for a property", async () => {
    await service.create("property-1", { name: "Unit 1", slug: "unit-1" });
    await service.create("property-1", { name: "Unit 2", slug: "unit-2" });
    await service.create("property-1", { name: "Unit 3", slug: "unit-3" });

    const result = await service.getByPropertyId("property-1");
    assertEquals(result.total, 3);
    assertEquals(result.units.length, 3);
  });

  it("should reject listing units for non-existent property", async () => {
    await assertRejects(
      async () => {
        await service.getByPropertyId("non-existent");
      },
      UnitError,
      "Property not found"
    );
  });

  it("should update a unit", async () => {
    const created = await service.create("property-1", {
      name: "Old Unit",
      slug: "old-unit",
      bedrooms: 1,
    });

    const updated = await service.update(created.id, {
      name: "Renovated Unit",
      bedrooms: 3,
      settings: { check_in_time: "12:00" },
    });

    assertExists(updated);
    assertEquals(updated!.name, "Renovated Unit");
    assertEquals(updated!.bedrooms, 3);
    assertEquals(updated!.settings.check_in_time, "12:00");
  });

  it("should return null when updating non-existent unit", async () => {
    const result = await service.update("non-existent", { name: "test" });
    assertEquals(result, null);
  });

  it("should invalidate cache on unit update", async () => {
    const created = await service.create("property-1", {
      name: "Cached Unit",
      slug: "cached-unit",
      settings: { min_stay_nights: 3 },
    });

    // Populate cache
    await service.getById(created.id);

    // Update
    await service.update(created.id, {
      settings: { min_stay_nights: 5 },
    });

    // Get again
    const result = await service.getById(created.id);
    assertExists(result);
    assertEquals(result!.settings.min_stay_nights, 5);
  });

  it("should resolve settings without brand when property has no brand", async () => {
    const noBrandProperty: Property = {
      ...testProperty,
      id: "property-no-brand",
      brand_id: null,
      slug: "no-brand-property",
      settings: { check_in_time: "14:00" },
    };
    propertyRepo.addProperty(noBrandProperty);

    const unit = await service.create("property-no-brand", {
      name: "No Brand Unit",
      slug: "no-brand-unit",
      settings: {},
    });

    const result = await service.getById(unit.id);
    assertExists(result);

    // Property override
    assertEquals(result!.resolved_settings.check_in_time, "14:00");

    // Platform default (brand is skipped since no brand_id)
    assertEquals(result!.resolved_settings.cancellation_policy, "moderate");
    assertEquals(result!.resolved_settings.check_out_time, "11:00");
  });
});
