// ============================================================
// Property Service - Business Logic
// ============================================================

import type {
  Property,
  CreatePropertyRequest,
  UpdatePropertyRequest,
} from "../models/types.ts";
import type { PropertyRepository } from "../repositories/property_repository.ts";
import type { CacheClient } from "../cache/redis_cache.ts";
import type { SettingsResolver } from "./settings_resolver.ts";

export class PropertyService {
  constructor(
    private propertyRepo: PropertyRepository,
    private cache: CacheClient,
    private settingsResolver: SettingsResolver
  ) {}

  async getById(id: string): Promise<(Property & { resolved_settings?: Record<string, unknown> }) | null> {
    // Check cache
    const cacheKey = `property:${id}`;
    const cached = await this.cache.get(cacheKey);
    if (cached) {
      try {
        return JSON.parse(cached);
      } catch {
        // Invalid cache
      }
    }

    const property = await this.propertyRepo.findById(id);
    if (!property) return null;

    // Resolve settings (tiers 1-3)
    const resolvedSettings = await this.settingsResolver.resolvePropertySettings(
      property.brand_id,
      property.settings,
      property.id
    );

    const result = { ...property, resolved_settings: resolvedSettings };

    // Cache
    await this.cache.set(cacheKey, JSON.stringify(result));

    return result;
  }

  async create(data: CreatePropertyRequest): Promise<Property> {
    // Validate slug uniqueness
    const existing = await this.propertyRepo.findBySlug(data.slug);
    if (existing) {
      throw new PropertyError("Property with this slug already exists", 409);
    }

    // Validate required fields
    if (!data.name || !data.slug) {
      throw new PropertyError("Name and slug are required", 400);
    }

    return await this.propertyRepo.create(data);
  }

  async update(id: string, data: UpdatePropertyRequest): Promise<Property | null> {
    const existing = await this.propertyRepo.findById(id);
    if (!existing) return null;

    // If slug is being changed, check uniqueness
    if (data.slug && data.slug !== existing.slug) {
      const slugExists = await this.propertyRepo.findBySlug(data.slug);
      if (slugExists) {
        throw new PropertyError("Property with this slug already exists", 409);
      }
    }

    const updated = await this.propertyRepo.update(id, data);

    // Invalidate caches
    await this.cache.del(`property:${id}`);
    await this.settingsResolver.invalidateProperty(id);

    return updated;
  }

  async list(options?: { page?: number; perPage?: number; status?: string }) {
    return this.propertyRepo.list(options);
  }
}

export class PropertyError extends Error {
  constructor(message: string, public statusCode: number) {
    super(message);
    this.name = "PropertyError";
  }
}
