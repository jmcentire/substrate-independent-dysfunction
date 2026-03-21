// ============================================================
// Brand Repository (Tier 2)
// ============================================================

import type { Brand } from "../models/types.ts";

export interface BrandRepository {
  findById(id: string): Promise<Brand | null>;
  findBySlug(slug: string): Promise<Brand | null>;
}

export class PostgresBrandRepository implements BrandRepository {
  constructor(private pool: any) {}

  async findById(id: string): Promise<Brand | null> {
    const result = await this.pool.queryObject<Brand>(
      `SELECT id, name, slug, settings, is_active, created_at, updated_at
       FROM brands WHERE id = $1`,
      [id]
    );
    return result.rows[0] ?? null;
  }

  async findBySlug(slug: string): Promise<Brand | null> {
    const result = await this.pool.queryObject<Brand>(
      `SELECT id, name, slug, settings, is_active, created_at, updated_at
       FROM brands WHERE slug = $1`,
      [slug]
    );
    return result.rows[0] ?? null;
  }
}

/**
 * In-memory implementation for testing.
 */
export class InMemoryBrandRepository implements BrandRepository {
  private brands = new Map<string, Brand>();

  addBrand(brand: Brand): void {
    this.brands.set(brand.id, brand);
  }

  async findById(id: string): Promise<Brand | null> {
    return this.brands.get(id) ?? null;
  }

  async findBySlug(slug: string): Promise<Brand | null> {
    for (const brand of this.brands.values()) {
      if (brand.slug === slug) return brand;
    }
    return null;
  }
}
