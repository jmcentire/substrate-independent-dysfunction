// ============================================================
// Settings Resolver - Unit Tests
// ============================================================

import {
  assertEquals,
  assertExists,
} from "https://deno.land/std@0.208.0/assert/mod.ts";
import { describe, it, beforeEach } from "https://deno.land/std@0.208.0/testing/bdd.ts";
import { deepMerge, SettingsResolver } from "../../src/services/settings_resolver.ts";
import { InMemoryPlatformRepository } from "../../src/repositories/platform_repository.ts";
import { InMemoryBrandRepository } from "../../src/repositories/brand_repository.ts";
import { InMemoryCache } from "../../src/cache/redis_cache.ts";
import type { PlatformDefaults, Brand } from "../../src/models/types.ts";

// ============ deepMerge Tests ============

describe("deepMerge", () => {
  it("should merge flat objects", () => {
    const base = { a: 1, b: 2 };
    const override = { b: 3, c: 4 };
    const result = deepMerge(base, override);
    assertEquals(result, { a: 1, b: 3, c: 4 });
  });

  it("should deep merge nested objects", () => {
    const base = {
      notifications: {
        email: true,
        sms: false,
        push: true,
      },
    };
    const override = {
      notifications: {
        sms: true,
      },
    };
    const result = deepMerge(base, override);
    assertEquals(result, {
      notifications: {
        email: true,
        sms: true,
        push: true,
      },
    });
  });

  it("should replace arrays entirely", () => {
    const base = { amenities: ["wifi", "pool"] };
    const override = { amenities: ["wifi", "gym"] };
    const result = deepMerge(base, override);
    assertEquals(result, { amenities: ["wifi", "gym"] });
  });

  it("should replace primitives", () => {
    const base = { check_in_time: "16:00", min_stay: 1 };
    const override = { check_in_time: "15:00" };
    const result = deepMerge(base, override);
    assertEquals(result, { check_in_time: "15:00", min_stay: 1 });
  });

  it("should handle null override values", () => {
    const base = { pet_policy: "allowed" };
    const override = { pet_policy: null };
    const result = deepMerge(base, override as any);
    assertEquals(result.pet_policy, null);
  });

  it("should handle empty override", () => {
    const base = { a: 1, b: 2 };
    const result = deepMerge(base, {});
    assertEquals(result, { a: 1, b: 2 });
  });

  it("should handle empty base", () => {
    const override = { a: 1, b: 2 };
    const result = deepMerge({}, override);
    assertEquals(result, { a: 1, b: 2 });
  });

  it("should handle deeply nested objects (3 levels)", () => {
    const base = {
      level1: {
        level2: {
          level3: {
            a: 1,
            b: 2,
          },
        },
      },
    };
    const override = {
      level1: {
        level2: {
          level3: {
            b: 3,
            c: 4,
          },
        },
      },
    };
    const result = deepMerge(base, override);
    assertEquals(result, {
      level1: {
        level2: {
          level3: {
            a: 1,
            b: 3,
            c: 4,
          },
        },
      },
    });
  });

  it("should not mutate original objects", () => {
    const base = { a: 1, nested: { b: 2 } };
    const override = { nested: { c: 3 } };
    const result = deepMerge(base, override);

    assertEquals(base, { a: 1, nested: { b: 2 } });
    assertEquals(override, { nested: { c: 3 } });
    assertEquals(result, { a: 1, nested: { b: 2, c: 3 } });
  });
});

// ============ SettingsResolver Tests ============

describe("SettingsResolver", () => {
  let platformRepo: InMemoryPlatformRepository;
  let brandRepo: InMemoryBrandRepository;
  let cache: InMemoryCache;
  let resolver: SettingsResolver;

  const platformDefaults: PlatformDefaults = {
    id: "platform-1",
    settings: {
      check_in_time: "16:00",
      check_out_time: "11:00",
      min_stay_nights: 1,
      max_stay_nights: 30,
      cancellation_policy: "moderate",
      instant_book: false,
      pet_policy: "not_allowed",
      notification_preferences: {
        booking_confirmed: true,
        booking_cancelled: true,
        check_in_reminder: true,
      },
    },
    created_at: new Date(),
    updated_at: new Date(),
  };

  const wanderBrand: Brand = {
    id: "brand-wander",
    name: "Wander",
    slug: "wander",
    settings: {
      cancellation_policy: "flexible",
      instant_book: true,
      check_in_time: "15:00",
      pet_policy: "allowed_with_fee",
    },
    is_active: true,
    created_at: new Date(),
    updated_at: new Date(),
  };

  beforeEach(() => {
    platformRepo = new InMemoryPlatformRepository();
    platformRepo.setDefaults(platformDefaults);

    brandRepo = new InMemoryBrandRepository();
    brandRepo.addBrand(wanderBrand);

    cache = new InMemoryCache(300);

    resolver = new SettingsResolver({
      platformRepo,
      brandRepo,
      cache,
    });
  });

  it("should resolve with platform defaults only when no overrides", async () => {
    const result = await resolver.resolveUnitSettings(null, {}, {}, "unit-1");

    assertEquals(result.check_in_time, "16:00");
    assertEquals(result.cancellation_policy, "moderate");
    assertEquals(result.instant_book, false);
  });

  it("should apply brand overrides on top of platform defaults", async () => {
    const result = await resolver.resolveUnitSettings(
      "brand-wander",
      {},
      {},
      "unit-2"
    );

    // Brand overrides
    assertEquals(result.cancellation_policy, "flexible");
    assertEquals(result.instant_book, true);
    assertEquals(result.check_in_time, "15:00");
    assertEquals(result.pet_policy, "allowed_with_fee");

    // Platform defaults still present
    assertEquals(result.check_out_time, "11:00");
    assertEquals(result.min_stay_nights, 1);
  });

  it("should apply property overrides on top of brand", async () => {
    const propertySettings = {
      check_in_time: "14:00",
      min_stay_nights: 2,
    };

    const result = await resolver.resolveUnitSettings(
      "brand-wander",
      propertySettings,
      {},
      "unit-3"
    );

    // Property overrides
    assertEquals(result.check_in_time, "14:00");
    assertEquals(result.min_stay_nights, 2);

    // Brand overrides still present
    assertEquals(result.cancellation_policy, "flexible");
    assertEquals(result.instant_book, true);

    // Platform defaults still present
    assertEquals(result.check_out_time, "11:00");
  });

  it("should apply unit overrides on top of everything (full 4-tier)", async () => {
    const propertySettings = {
      check_in_time: "14:00",
      min_stay_nights: 2,
    };

    const unitSettings = {
      check_in_time: "13:00",
      max_stay_nights: 14,
      special_amenity: "hot_tub",
    };

    const result = await resolver.resolveUnitSettings(
      "brand-wander",
      propertySettings,
      unitSettings,
      "unit-4"
    );

    // Unit overrides (highest priority)
    assertEquals(result.check_in_time, "13:00");
    assertEquals(result.max_stay_nights, 14);
    assertEquals(result.special_amenity, "hot_tub");

    // Property overrides
    assertEquals(result.min_stay_nights, 2);

    // Brand overrides
    assertEquals(result.cancellation_policy, "flexible");
    assertEquals(result.instant_book, true);
    assertEquals(result.pet_policy, "allowed_with_fee");

    // Platform defaults
    assertEquals(result.check_out_time, "11:00");
  });

  it("should deep merge nested notification preferences across tiers", async () => {
    const propertySettings = {
      notification_preferences: {
        booking_cancelled: false,
        review_request: true,
      },
    };

    const unitSettings = {
      notification_preferences: {
        check_in_reminder: false,
      },
    };

    const result = await resolver.resolveUnitSettings(
      null,
      propertySettings,
      unitSettings,
      "unit-5"
    );

    const prefs = result.notification_preferences as Record<string, boolean>;
    assertEquals(prefs.booking_confirmed, true); // platform default
    assertEquals(prefs.booking_cancelled, false); // property override
    assertEquals(prefs.check_in_reminder, false); // unit override
    assertEquals(prefs.review_request, true); // property addition
  });

  it("should use cached result on second call", async () => {
    const unitSettings = { check_in_time: "13:00" };

    // First call - populates cache
    const result1 = await resolver.resolveUnitSettings(null, {}, unitSettings, "unit-cache");
    assertEquals(result1.check_in_time, "13:00");

    // Second call - should hit cache
    const result2 = await resolver.resolveUnitSettings(null, {}, unitSettings, "unit-cache");
    assertEquals(result2.check_in_time, "13:00");
  });

  it("should invalidate unit cache", async () => {
    const unitSettings = { check_in_time: "13:00" };

    // Populate cache
    await resolver.resolveUnitSettings(null, {}, unitSettings, "unit-inv");

    // Invalidate
    await resolver.invalidateUnit("unit-inv");

    // Should recompute (verify no error)
    const result = await resolver.resolveUnitSettings(null, {}, unitSettings, "unit-inv");
    assertEquals(result.check_in_time, "13:00");
  });

  it("should resolve property settings (tiers 1-3)", async () => {
    const propertySettings = {
      check_in_time: "14:00",
      pool_heated: true,
    };

    const result = await resolver.resolvePropertySettings(
      "brand-wander",
      propertySettings,
      "prop-1"
    );

    assertEquals(result.check_in_time, "14:00"); // property wins
    assertEquals(result.cancellation_policy, "flexible"); // brand wins over platform
    assertEquals(result.check_out_time, "11:00"); // platform default
    assertEquals(result.pool_heated, true); // property addition
  });

  it("should handle missing platform defaults gracefully", async () => {
    const emptyPlatformRepo = new InMemoryPlatformRepository();
    const resolverNoDefaults = new SettingsResolver({
      platformRepo: emptyPlatformRepo,
      brandRepo,
      cache: new InMemoryCache(),
    });

    const result = await resolverNoDefaults.resolveUnitSettings(
      null,
      { check_in_time: "14:00" },
      { min_stay: 3 },
      "unit-no-platform"
    );

    assertEquals(result.check_in_time, "14:00");
    assertEquals(result.min_stay, 3);
  });

  it("should handle missing brand gracefully", async () => {
    const result = await resolver.resolveUnitSettings(
      "non-existent-brand",
      {},
      {},
      "unit-no-brand"
    );

    // Should fall back to platform defaults
    assertEquals(result.check_in_time, "16:00");
    assertEquals(result.cancellation_policy, "moderate");
  });
});
