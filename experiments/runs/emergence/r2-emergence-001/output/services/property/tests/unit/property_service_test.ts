// ============================================================
// Property Service - Unit Tests
// ============================================================

import {
  assertEquals,
  assertRejects,
  assertExists,
} from "https://deno.land/std@0.208.0/assert/mod.ts";
import { describe, it, beforeEach } from "https://deno.land/std@0.208.0/testing/bdd.ts";
import { PropertyService, PropertyError } from "../../src/services/property_service.ts";
import { InMemoryPropertyRepository } from "../../src/repositories/property_repository.ts";
import { InMemoryPlatformRepository } from "../../src/repositories/platform_repository.ts";
import { InMemoryBrandRepository } from "../../src/repositories/brand_repository.ts";
import { InMemoryCache } from "../../src/cache/redis_cache.ts";
import { SettingsResolver } from "../../src/services/settings_resolver.ts";
import type { Property, PlatformDefaults } from "../../src/models/types.ts";

describe("PropertyService", () => {
  let propertyRepo: InMemoryPropertyRepository;
  let cache: InMemoryCache;
  let resolver: SettingsResolver;
  let service: PropertyService;

  const platformDefaults: PlatformDefaults = {
    id: "platform-1",
    settings: {
      check_in_time: "16:00",
      check_out_time: "11:00",
    },
    created_at: new Date(),
    updated_at: new Date(),
  };

  beforeEach(() => {
    propertyRepo = new InMemoryPropertyRepository();
    cache = new InMemoryCache();

    const platformRepo = new InMemoryPlatformRepository();
    platformRepo.setDefaults(platformDefaults);
    const brandRepo = new InMemoryBrandRepository();

    resolver = new SettingsResolver({ platformRepo, brandRepo, cache });
    service = new PropertyService(propertyRepo, cache, resolver);
  });

  it("should create a property", async () => {
    const property = await service.create({
      name: "Mountain Lodge",
      slug: "mountain-lodge",
      description: "A beautiful lodge",
      settings: { check_in_time: "14:00" },
    });

    assertExists(property.id);
    assertEquals(property.name, "Mountain Lodge");
    assertEquals(property.slug, "mountain-lodge");
    assertEquals(property.settings.check_in_time, "14:00");
    assertEquals(property.status, "draft");
  });

  it("should reject duplicate slugs", async () => {
    await service.create({
      name: "Mountain Lodge",
      slug: "mountain-lodge",
    });

    await assertRejects(
      async () => {
        await service.create({
          name: "Another Lodge",
          slug: "mountain-lodge",
        });
      },
      PropertyError,
      "slug already exists"
    );
  });

  it("should reject missing name", async () => {
    await assertRejects(
      async () => {
        await service.create({
          name: "",
          slug: "empty-name",
        });
      },
      PropertyError,
      "required"
    );
  });

  it("should get a property by ID with resolved settings", async () => {
    const created = await service.create({
      name: "Beach House",
      slug: "beach-house",
      settings: { check_in_time: "15:00" },
    });

    const result = await service.getById(created.id);

    assertExists(result);
    assertEquals(result!.name, "Beach House");
    assertExists(result!.resolved_settings);
    assertEquals(result!.resolved_settings!.check_in_time, "15:00"); // property override
    assertEquals(result!.resolved_settings!.check_out_time, "11:00"); // platform default
  });

  it("should return null for non-existent property", async () => {
    const result = await service.getById("non-existent-id");
    assertEquals(result, null);
  });

  it("should update a property", async () => {
    const created = await service.create({
      name: "Old Name",
      slug: "old-slug",
    });

    const updated = await service.update(created.id, {
      name: "New Name",
      status: "active",
    });

    assertExists(updated);
    assertEquals(updated!.name, "New Name");
    assertEquals(updated!.status, "active");
  });

  it("should return null when updating non-existent property", async () => {
    const result = await service.update("non-existent", { name: "test" });
    assertEquals(result, null);
  });

  it("should reject duplicate slug on update", async () => {
    await service.create({
      name: "Property 1",
      slug: "slug-one",
    });

    const property2 = await service.create({
      name: "Property 2",
      slug: "slug-two",
    });

    await assertRejects(
      async () => {
        await service.update(property2.id, { slug: "slug-one" });
      },
      PropertyError,
      "slug already exists"
    );
  });

  it("should list properties", async () => {
    await service.create({ name: "P1", slug: "p1" });
    await service.create({ name: "P2", slug: "p2" });
    await service.create({ name: "P3", slug: "p3" });

    const result = await service.list();
    assertEquals(result.total, 3);
    assertEquals(result.properties.length, 3);
  });

  it("should invalidate cache on update", async () => {
    const created = await service.create({
      name: "Cached Property",
      slug: "cached-property",
      settings: { check_in_time: "14:00" },
    });

    // Populate cache
    await service.getById(created.id);

    // Update
    await service.update(created.id, {
      settings: { check_in_time: "13:00" },
    });

    // Get again - should reflect new settings
    const result = await service.getById(created.id);
    assertExists(result);
    assertEquals(result!.settings.check_in_time, "13:00");
  });
});
