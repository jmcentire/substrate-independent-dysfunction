/**
 * Data access layer for availability records.
 * Handles all PostgreSQL interactions.
 */

import type { DbPool, DbClient } from "./db.ts";
import type { AvailabilityRecord } from "./types.ts";
import { fullMonthMask } from "./bitmask.ts";

export class AvailabilityRepository {
  private pool: DbPool;

  constructor(pool: DbPool) {
    this.pool = pool;
  }

  /**
   * Get availability record for a unit/year/month.
   * Returns null if no record exists (which means fully available by default).
   */
  async getMonthAvailability(
    unitId: string,
    year: number,
    month: number,
  ): Promise<AvailabilityRecord | null> {
    const client = await this.pool.connect();
    try {
      const result = await client.queryObject<{
        id: string;
        unit_id: string;
        year: number;
        month: number;
        bitmask: number | string;
        updated_at: Date;
      }>(
        `SELECT id, unit_id, year, month, bitmask, updated_at 
         FROM availability 
         WHERE unit_id = $1 AND year = $2 AND month = $3`,
        [unitId, year, month],
      );

      if (result.rows.length === 0) {
        return null;
      }

      const row = result.rows[0];
      return {
        id: row.id,
        unitId: row.unit_id,
        year: row.year,
        month: row.month,
        bitmask: typeof row.bitmask === 'string' ? parseInt(row.bitmask, 10) : Number(row.bitmask),
        updatedAt: row.updated_at,
      };
    } finally {
      client.release();
    }
  }

  /**
   * Get availability records for multiple months at once.
   */
  async getMultiMonthAvailability(
    unitId: string,
    months: Array<{ year: number; month: number }>,
  ): Promise<Map<string, AvailabilityRecord>> {
    if (months.length === 0) return new Map();

    const client = await this.pool.connect();
    try {
      // Build query for all requested months
      const conditions = months.map((_, i) => `(year = $${i * 2 + 2} AND month = $${i * 2 + 3})`);
      const params: unknown[] = [unitId];
      for (const m of months) {
        params.push(m.year, m.month);
      }

      const result = await client.queryObject<{
        id: string;
        unit_id: string;
        year: number;
        month: number;
        bitmask: number | string;
        updated_at: Date;
      }>(
        `SELECT id, unit_id, year, month, bitmask, updated_at 
         FROM availability 
         WHERE unit_id = $1 AND (${conditions.join(" OR ")})`,
        params,
      );

      const map = new Map<string, AvailabilityRecord>();
      for (const row of result.rows) {
        const key = `${row.year}:${row.month}`;
        map.set(key, {
          id: row.id,
          unitId: row.unit_id,
          year: row.year,
          month: row.month,
          bitmask: typeof row.bitmask === 'string' ? parseInt(row.bitmask, 10) : Number(row.bitmask),
          updatedAt: row.updated_at,
        });
      }
      return map;
    } finally {
      client.release();
    }
  }

  /**
   * Upsert availability bitmask for a unit/year/month.
   */
  async upsertMonthAvailability(
    unitId: string,
    year: number,
    month: number,
    bitmask: number,
  ): Promise<AvailabilityRecord> {
    const client = await this.pool.connect();
    try {
      const result = await client.queryObject<{
        id: string;
        unit_id: string;
        year: number;
        month: number;
        bitmask: number | string;
        updated_at: Date;
      }>(
        `INSERT INTO availability (unit_id, year, month, bitmask, updated_at)
         VALUES ($1, $2, $3, $4, NOW())
         ON CONFLICT (unit_id, year, month)
         DO UPDATE SET bitmask = $4, updated_at = NOW()
         RETURNING id, unit_id, year, month, bitmask, updated_at`,
        [unitId, year, month, bitmask],
      );

      const row = result.rows[0];
      return {
        id: row.id,
        unitId: row.unit_id,
        year: row.year,
        month: row.month,
        bitmask: typeof row.bitmask === 'string' ? parseInt(row.bitmask, 10) : Number(row.bitmask),
        updatedAt: row.updated_at,
      };
    } finally {
      client.release();
    }
  }

  /**
   * Log an availability change for audit trail.
   */
  async logChange(
    unitId: string,
    changeType: "block" | "unblock",
    startDate: string,
    endDate: string,
    changedBy?: string,
  ): Promise<void> {
    const client = await this.pool.connect();
    try {
      await client.queryObject(
        `INSERT INTO availability_log (unit_id, change_type, start_date, end_date, changed_by)
         VALUES ($1, $2, $3, $4, $5)`,
        [unitId, changeType, startDate, endDate, changedBy || null],
      );
    } finally {
      client.release();
    }
  }

  /**
   * Get all availability records for a unit (for cache warming).
   */
  async getAllForUnit(unitId: string): Promise<AvailabilityRecord[]> {
    const client = await this.pool.connect();
    try {
      const result = await client.queryObject<{
        id: string;
        unit_id: string;
        year: number;
        month: number;
        bitmask: number | string;
        updated_at: Date;
      }>(
        `SELECT id, unit_id, year, month, bitmask, updated_at 
         FROM availability 
         WHERE unit_id = $1
         ORDER BY year, month`,
        [unitId],
      );

      return result.rows.map((row) => ({
        id: row.id,
        unitId: row.unit_id,
        year: row.year,
        month: row.month,
        bitmask: typeof row.bitmask === 'string' ? parseInt(row.bitmask, 10) : Number(row.bitmask),
        updatedAt: row.updated_at,
      }));
    } finally {
      client.release();
    }
  }

  /**
   * Get the effective bitmask for a unit/year/month.
   * If no record exists, returns full month mask (all available).
   */
  async getEffectiveBitmask(
    unitId: string,
    year: number,
    month: number,
  ): Promise<number> {
    const record = await this.getMonthAvailability(unitId, year, month);
    if (record === null) {
      return fullMonthMask(year, month);
    }
    return record.bitmask;
  }
}
