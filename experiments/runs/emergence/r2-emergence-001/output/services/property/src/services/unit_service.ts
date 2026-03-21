// ============================================================
// Unit Service - Business Logic
// ============================================================

import type {
  Unit,
  UnitWithResolvedSettings,
  CreateUnitRequest,
  UpdateUnitRequest,
} from "../models/types.ts";
import type { UnitRepository } from "../repositories/unit_repository.ts";
import type { PropertyRepository } from "../repositories/property_repository.ts";
import type { CacheClient } from "../cache/redis_cache.ts";
import type { SettingsResolver } from "./settings_resolver.ts";

export class UnitService {
  constructor(
    private unitRepo: UnitRepository,
    private propertyRepo: PropertyRepository,
    private cache: CacheClient,
    private settingsResolver: SettingsResolver
  ) {}

  async getById(id: string): Promise<UnitWithResolvedSettings | null> {
    // Check cache
    const cacheKey = `unit:${id}`;
    const cached = await this.cache.get(cacheKey);
    if (cached) {
      try {
        return JSON.parse(cached);
      } catch {
        // Invalid cache
      }
    }

    const unit = await this.unitRepo.findById(id);
    if (!unit) return null;

    // Get parent property for brand_id and property settings
    const property = await this.propertyRepo.findById(unit.property_id);
    if (!property) return null;

    // Resolve settings through all 4 tiers
    const resolvedSettings = await this.settingsResolver.resolveUnitSettings(
      property.brand_id,
      property.settings,
      unit.settings,
      unit.id
    );

    const result: UnitWithResolvedSettings = {
      ...unit,
      resolved_settings: resolvedSettings,
    };

    // Cache
    await this.cache.set(cacheKey, JSON.stringify(result));

    return result;
  }

  async getByPropertyId(propertyId: string, options?: { page?: number; perPage?: number }) {
    // Verify property exists
    const property = await this.propertyRepo.findById(propertyId);
    if (!property) {
      throw new UnitError("Property not found", 404);
    }

    return this.unitRepo.findByPropertyId(propertyId, options);
  }

  async create(propertyId: string, data: CreateUnitRequest): Promise<Unit> {
    // Verify property exists
    const property = await this.propertyRepo.findById(propertyId);
    if (!property) {
      throw new UnitError("Property not found", 404);
    }

    // Validate required fields
    if (!data.name || !data.slug) {
      throw new UnitError("Name and slug are required", 400);
    }

    return await this.unitRepo.create(propertyId, data);
  }

  async update(id: string, data: UpdateUnitRequest): Promise<Unit | null> {
    const existing = await this.unitRepo.findById(id);
    if (!existing) return null;

    const updated = await this.unitRepo.update(id, data);

    // Invalidate caches
    await this.cache.del(`unit:${id}`);
    await this.settingsResolver.invalidateUnit(id);

    return updated;
  }
}

export class UnitError extends Error {
  constructor(message: string, public statusCode: number) {
    super(message);
    this.name = "UnitError";
  }
}
