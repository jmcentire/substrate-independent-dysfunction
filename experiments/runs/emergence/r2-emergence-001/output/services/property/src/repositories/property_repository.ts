// ============================================================
// Property Repository (Tier 3)
// ============================================================

import type { Property, CreatePropertyRequest, UpdatePropertyRequest } from "../models/types.ts";

export interface PropertyRepository {
  findById(id: string): Promise<Property | null>;
  findBySlug(slug: string): Promise<Property | null>;
  create(data: CreatePropertyRequest): Promise<Property>;
  update(id: string, data: UpdatePropertyRequest): Promise<Property | null>;
  list(options?: { page?: number; perPage?: number; status?: string }): Promise<{ properties: Property[]; total: number }>;
}

export class PostgresPropertyRepository implements PropertyRepository {
  constructor(private pool: any) {}

  async findById(id: string): Promise<Property | null> {
    const result = await this.pool.queryObject<Property>(
      `SELECT id, brand_id, name, slug, description, address, location,
              amenities, images, settings, status, is_active, created_at, updated_at
       FROM properties WHERE id = $1`,
      [id]
    );
    return result.rows[0] ?? null;
  }

  async findBySlug(slug: string): Promise<Property | null> {
    const result = await this.pool.queryObject<Property>(
      `SELECT id, brand_id, name, slug, description, address, location,
              amenities, images, settings, status, is_active, created_at, updated_at
       FROM properties WHERE slug = $1`,
      [slug]
    );
    return result.rows[0] ?? null;
  }

  async create(data: CreatePropertyRequest): Promise<Property> {
    const result = await this.pool.queryObject<Property>(
      `INSERT INTO properties (brand_id, name, slug, description, address, location, amenities, images, settings, status)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
       RETURNING id, brand_id, name, slug, description, address, location,
                 amenities, images, settings, status, is_active, created_at, updated_at`,
      [
        data.brand_id ?? null,
        data.name,
        data.slug,
        data.description ?? null,
        JSON.stringify(data.address ?? null),
        JSON.stringify(data.location ?? null),
        JSON.stringify(data.amenities ?? []),
        JSON.stringify(data.images ?? []),
        JSON.stringify(data.settings ?? {}),
        data.status ?? 'draft',
      ]
    );
    return result.rows[0];
  }

  async update(id: string, data: UpdatePropertyRequest): Promise<Property | null> {
    // Build dynamic update
    const fields: string[] = [];
    const values: unknown[] = [];
    let paramIdx = 1;

    const addField = (name: string, value: unknown, isJson = false) => {
      fields.push(`${name} = $${paramIdx}`);
      values.push(isJson ? JSON.stringify(value) : value);
      paramIdx++;
    };

    if (data.brand_id !== undefined) addField('brand_id', data.brand_id);
    if (data.name !== undefined) addField('name', data.name);
    if (data.slug !== undefined) addField('slug', data.slug);
    if (data.description !== undefined) addField('description', data.description);
    if (data.address !== undefined) addField('address', data.address, true);
    if (data.location !== undefined) addField('location', data.location, true);
    if (data.amenities !== undefined) addField('amenities', data.amenities, true);
    if (data.images !== undefined) addField('images', data.images, true);
    if (data.settings !== undefined) addField('settings', data.settings, true);
    if (data.status !== undefined) addField('status', data.status);
    if (data.is_active !== undefined) addField('is_active', data.is_active);

    if (fields.length === 0) {
      return this.findById(id);
    }

    values.push(id);
    const query = `UPDATE properties SET ${fields.join(', ')} WHERE id = $${paramIdx}
      RETURNING id, brand_id, name, slug, description, address, location,
                amenities, images, settings, status, is_active, created_at, updated_at`;

    const result = await this.pool.queryObject<Property>(query, values);
    return result.rows[0] ?? null;
  }

  async list(options?: { page?: number; perPage?: number; status?: string }): Promise<{ properties: Property[]; total: number }> {
    const page = options?.page ?? 1;
    const perPage = options?.perPage ?? 20;
    const offset = (page - 1) * perPage;

    let whereClause = '';
    const params: unknown[] = [];
    let paramIdx = 1;

    if (options?.status) {
      whereClause = `WHERE status = $${paramIdx}`;
      params.push(options.status);
      paramIdx++;
    }

    const countResult = await this.pool.queryObject<{ count: number }>(
      `SELECT COUNT(*) as count FROM properties ${whereClause}`,
      params
    );

    params.push(perPage, offset);
    const result = await this.pool.queryObject<Property>(
      `SELECT id, brand_id, name, slug, description, address, location,
              amenities, images, settings, status, is_active, created_at, updated_at
       FROM properties ${whereClause}
       ORDER BY created_at DESC
       LIMIT $${paramIdx} OFFSET $${paramIdx + 1}`,
      params
    );

    return {
      properties: result.rows,
      total: Number(countResult.rows[0]?.count ?? 0),
    };
  }
}

/**
 * In-memory implementation for testing.
 */
export class InMemoryPropertyRepository implements PropertyRepository {
  private properties = new Map<string, Property>();
  private counter = 0;

  addProperty(property: Property): void {
    this.properties.set(property.id, property);
  }

  async findById(id: string): Promise<Property | null> {
    return this.properties.get(id) ?? null;
  }

  async findBySlug(slug: string): Promise<Property | null> {
    for (const p of this.properties.values()) {
      if (p.slug === slug) return p;
    }
    return null;
  }

  async create(data: CreatePropertyRequest): Promise<Property> {
    this.counter++;
    const property: Property = {
      id: crypto.randomUUID(),
      brand_id: data.brand_id ?? null,
      name: data.name,
      slug: data.slug,
      description: data.description ?? null,
      address: data.address ?? null,
      location: data.location ?? null,
      amenities: data.amenities ?? [],
      images: data.images ?? [],
      settings: data.settings ?? {},
      status: data.status ?? 'draft',
      is_active: true,
      created_at: new Date(),
      updated_at: new Date(),
    };
    this.properties.set(property.id, property);
    return property;
  }

  async update(id: string, data: UpdatePropertyRequest): Promise<Property | null> {
    const existing = this.properties.get(id);
    if (!existing) return null;

    const updated: Property = {
      ...existing,
      ...Object.fromEntries(
        Object.entries(data).filter(([_, v]) => v !== undefined)
      ),
      updated_at: new Date(),
    } as Property;

    this.properties.set(id, updated);
    return updated;
  }

  async list(options?: { page?: number; perPage?: number; status?: string }): Promise<{ properties: Property[]; total: number }> {
    let props = Array.from(this.properties.values());
    if (options?.status) {
      props = props.filter(p => p.status === options.status);
    }
    const page = options?.page ?? 1;
    const perPage = options?.perPage ?? 20;
    const offset = (page - 1) * perPage;
    return {
      properties: props.slice(offset, offset + perPage),
      total: props.length,
    };
  }
}
