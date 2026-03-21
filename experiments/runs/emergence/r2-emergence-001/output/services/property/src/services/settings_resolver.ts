// ============================================================
// Settings Resolver - 4-Tier Override Hierarchy
// ============================================================
//
// Resolution order (later tiers override earlier ones):
//   Tier 1: Platform defaults
//   Tier 2: Brand overrides
//   Tier 3: Property overrides
//   Tier 4: Unit overrides
//
// Deep merge: nested objects are merged recursively.
// Arrays and primitives are replaced entirely by higher tiers.
// ============================================================

import type { PlatformRepository } from "../repositories/platform_repository.ts";
import type { BrandRepository } from "../repositories/brand_repository.ts";
import type { CacheClient } from "../cache/redis_cache.ts";

export interface SettingsResolverDeps {
  platformRepo: PlatformRepository;
  brandRepo: BrandRepository;
  cache: CacheClient;
}

export class SettingsResolver {
  private platformRepo: PlatformRepository;
  private brandRepo: BrandRepository;
  private cache: CacheClient;

  constructor(deps: SettingsResolverDeps) {
    this.platformRepo = deps.platformRepo;
    this.brandRepo = deps.brandRepo;
    this.cache = deps.cache;
  }

  /**
   * Resolve settings for a unit by merging all 4 tiers.
   */
  async resolveUnitSettings(
    brandId: string | null,
    propertySettings: Record<string, unknown>,
    unitSettings: Record<string, unknown>,
    unitId: string
  ): Promise<Record<string, unknown>> {
    // Check cache first
    const cacheKey = `settings:unit:${unitId}`;
    const cached = await this.cache.get(cacheKey);
    if (cached) {
      try {
        return JSON.parse(cached);
      } catch {
        // Invalid cache, continue
      }
    }

    // Tier 1: Platform defaults
    const platformDefaults = await this.getPlatformSettings();

    // Tier 2: Brand overrides
    const brandSettings = brandId ? await this.getBrandSettings(brandId) : {};

    // Tier 3: Property overrides (passed in)
    // Tier 4: Unit overrides (passed in)

    // Merge all tiers
    const resolved = deepMerge(
      deepMerge(
        deepMerge(platformDefaults, brandSettings),
        propertySettings
      ),
      unitSettings
    );

    // Cache the resolved settings
    await this.cache.set(cacheKey, JSON.stringify(resolved));

    return resolved;
  }

  /**
   * Resolve settings for a property by merging tiers 1-3.
   */
  async resolvePropertySettings(
    brandId: string | null,
    propertySettings: Record<string, unknown>,
    propertyId: string
  ): Promise<Record<string, unknown>> {
    const cacheKey = `settings:property:${propertyId}`;
    const cached = await this.cache.get(cacheKey);
    if (cached) {
      try {
        return JSON.parse(cached);
      } catch {
        // Invalid cache, continue
      }
    }

    const platformDefaults = await this.getPlatformSettings();
    const brandSettings = brandId ? await this.getBrandSettings(brandId) : {};

    const resolved = deepMerge(
      deepMerge(platformDefaults, brandSettings),
      propertySettings
    );

    await this.cache.set(cacheKey, JSON.stringify(resolved));
    return resolved;
  }

  private async getPlatformSettings(): Promise<Record<string, unknown>> {
    const cacheKey = `settings:platform`;
    const cached = await this.cache.get(cacheKey);
    if (cached) {
      try {
        return JSON.parse(cached);
      } catch {
        // Invalid cache
      }
    }

    const defaults = await this.platformRepo.getDefaults();
    const settings = defaults?.settings ?? {};

    await this.cache.set(cacheKey, JSON.stringify(settings), 600); // Cache longer
    return settings;
  }

  private async getBrandSettings(brandId: string): Promise<Record<string, unknown>> {
    const cacheKey = `settings:brand:${brandId}`;
    const cached = await this.cache.get(cacheKey);
    if (cached) {
      try {
        return JSON.parse(cached);
      } catch {
        // Invalid cache
      }
    }

    const brand = await this.brandRepo.findById(brandId);
    const settings = brand?.settings ?? {};

    await this.cache.set(cacheKey, JSON.stringify(settings), 600);
    return settings;
  }

  /**
   * Invalidate cached settings for a unit and its parent entities.
   */
  async invalidateUnit(unitId: string): Promise<void> {
    await this.cache.del(`settings:unit:${unitId}`);
  }

  async invalidateProperty(propertyId: string): Promise<void> {
    await this.cache.del(`settings:property:${propertyId}`);
    // Also invalidate all units under this property
    await this.cache.delPattern(`settings:unit:*`);
  }

  async invalidateBrand(brandId: string): Promise<void> {
    await this.cache.del(`settings:brand:${brandId}`);
    // Cascade: invalidate properties and units
    await this.cache.delPattern(`settings:property:*`);
    await this.cache.delPattern(`settings:unit:*`);
  }

  async invalidatePlatform(): Promise<void> {
    await this.cache.del(`settings:platform`);
    // Cascade: invalidate everything
    await this.cache.delPattern(`settings:brand:*`);
    await this.cache.delPattern(`settings:property:*`);
    await this.cache.delPattern(`settings:unit:*`);
  }
}

/**
 * Deep merge two objects. Values from `override` take precedence.
 * - Nested plain objects are merged recursively.
 * - Arrays and primitives are replaced entirely.
 * - null/undefined in override replaces the base value.
 */
export function deepMerge(
  base: Record<string, unknown>,
  override: Record<string, unknown>
): Record<string, unknown> {
  const result: Record<string, unknown> = { ...base };

  for (const [key, overrideValue] of Object.entries(override)) {
    const baseValue = result[key];

    if (
      isPlainObject(baseValue) &&
      isPlainObject(overrideValue)
    ) {
      // Recursively merge nested objects
      result[key] = deepMerge(
        baseValue as Record<string, unknown>,
        overrideValue as Record<string, unknown>
      );
    } else {
      // Override (arrays, primitives, null)
      result[key] = overrideValue;
    }
  }

  return result;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}
