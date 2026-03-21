// ============================================================
// Unit Repository (Tier 4)
// ============================================================

import type { Unit, CreateUnitRequest, UpdateUnitRequest } from "../models/types.ts";

export interface UnitRepository {
  findById(id: string): Promise<Unit | null>;
  findByPropertyId(propertyId: string, options?: { page?: number; perPage?: number }): Promise<{ units: Unit[]; total: number }>;
  create(propertyId: string, data: CreateUnitRequest): Promise<Unit>;
  update(id: string, data: UpdateUnitRequest): Promise<Unit | null>;
}

export class PostgresUnitRepository implements UnitRepository {
  constructor(private pool: any) {}

  async findById(id: string): Promise<Unit | null> {
    const result = await this.pool.queryObject<Unit>(
      `SELECT id, property_id, name, slug, description, unit_type,
              bedrooms, bathrooms, max_guests, amenities, images,
              settings, status, is_active, created_at, updated_at
       FROM units WHERE id = $1`,
      [id]
    );
    return result.rows[0] ?? null;
  }

  async findByPropertyId(propertyId: string, options?: { page?: number; perPage?: number }): Promise<{ units: Unit[]; total: number }> {
    const page = options?.page ?? 1;
    const perPage = options?.perPage ?? 50;
    const offset = (page - 1) * perPage;

    const countResult = await this.pool.queryObject<{ count: number }>(
      `SELECT COUNT(*) as count FROM units WHERE property_id = $1`,
      [propertyId]
    );

    const result = await this.pool.queryObject<Unit>(
      `SELECT id, property_id, name, slug, description, unit_type,
              bedrooms, bathrooms, max_guests, amenities, images,
              settings, status, is_active, created_at, updated_at
       FROM units WHERE property_id = $1
       ORDER BY name ASC
       LIMIT $2 OFFSET $3`,
      [propertyId, perPage, offset]
    );

    return {
      units: result.rows,
      total: Number(countResult.rows[0]?.count ?? 0),
    };
  }

  async create(propertyId: string, data: CreateUnitRequest): Promise<Unit> {
    const result = await this.pool.queryObject<Unit>(
      `INSERT INTO units (property_id, name, slug, description, unit_type, bedrooms, bathrooms, max_guests, amenities, images, settings, status)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
       RETURNING id, property_id, name, slug, description, unit_type,
                 bedrooms, bathrooms, max_guests, amenities, images,
                 settings, status, is_active, created_at, updated_at`,
      [
        propertyId,
        data.name,
        data.slug,
        data.description ?? null,
        data.unit_type ?? null,
        data.bedrooms ?? 0,
        data.bathrooms ?? 0,
        data.max_guests ?? 1,
        JSON.stringify(data.amenities ?? []),
        JSON.stringify(data.images ?? []),
        JSON.stringify(data.settings ?? {}),
        data.status ?? 'draft',
      ]
    );
    return result.rows[0];
  }

  async update(id: string, data: UpdateUnitRequest): Promise<Unit | null> {
    const fields: string[] = [];
    const values: unknown[] = [];
    let paramIdx = 1;

    const addField = (name: string, value: unknown, isJson = false) => {
      fields.push(`${name} = $${paramIdx}`);
      values.push(isJson ? JSON.stringify(value) : value);
      paramIdx++;
    };

    if (data.name !== undefined) addField('name', data.name);
    if (data.slug !== undefined) addField('slug', data.slug);
    if (data.description !== undefined) addField('description', data.description);
    if (data.unit_type !== undefined) addField('unit_type', data.unit_type);
    if (data.bedrooms !== undefined) addField('bedrooms', data.bedrooms);
    if (data.bathrooms !== undefined) addField('bathrooms', data.bathrooms);
    if (data.max_guests !== undefined) addField('max_guests', data.max_guests);
    if (data.amenities !== undefined) addField('amenities', data.amenities, true);
    if (data.images !== undefined) addField('images', data.images, true);
    if (data.settings !== undefined) addField('settings', data.settings, true);
    if (data.status !== undefined) addField('status', data.status);
    if (data.is_active !== undefined) addField('is_active', data.is_active);

    if (fields.length === 0) {
      return this.findById(id);
    }

    values.push(id);
    const query = `UPDATE units SET ${fields.join(', ')} WHERE id = $${paramIdx}
      RETURNING id, property_id, name, slug, description, unit_type,
                bedrooms, bathrooms, max_guests, amenities, images,
                settings, status, is_active, created_at, updated_at`;

    const result = await this.pool.queryObject<Unit>(query, values);
    return result.rows[0] ?? null;
  }
}

/**
 * In-memory implementation for testing.
 */
export class InMemoryUnitRepository implements UnitRepository {
  private units = new Map<string, Unit>();

  addUnit(unit: Unit): void {
    this.units.set(unit.id, unit);
  }

  async findById(id: string): Promise<Unit | null> {
    return this.units.get(id) ?? null;
  }

  async findByPropertyId(propertyId: string, options?: { page?: number; perPage?: number }): Promise<{ units: Unit[]; total: number }> {
    const allUnits = Array.from(this.units.values()).filter(u => u.property_id === propertyId);
    const page = options?.page ?? 1;
    const perPage = options?.perPage ?? 50;
    const offset = (page - 1) * perPage;
    return {
      units: allUnits.slice(offset, offset + perPage),
      total: allUnits.length,
    };
  }

  async create(propertyId: string, data: CreateUnitRequest): Promise<Unit> {
    const unit: Unit = {
      id: crypto.randomUUID(),
      property_id: propertyId,
      name: data.name,
      slug: data.slug,
      description: data.description ?? null,
      unit_type: data.unit_type ?? null,
      bedrooms: data.bedrooms ?? 0,
      bathrooms: data.bathrooms ?? 0,
      max_guests: data.max_guests ?? 1,
      amenities: data.amenities ?? [],
      images: data.images ?? [],
      settings: data.settings ?? {},
      status: data.status ?? 'draft',
      is_active: true,
      created_at: new Date(),
      updated_at: new Date(),
    };
    this.units.set(unit.id, unit);
    return unit;
  }

  async update(id: string, data: UpdateUnitRequest): Promise<Unit | null> {
    const existing = this.units.get(id);
    if (!existing) return null;

    const updated: Unit = {
      ...existing,
      ...Object.fromEntries(
        Object.entries(data).filter(([_, v]) => v !== undefined)
      ),
      updated_at: new Date(),
    } as Unit;

    this.units.set(id, updated);
    return updated;
  }
}
