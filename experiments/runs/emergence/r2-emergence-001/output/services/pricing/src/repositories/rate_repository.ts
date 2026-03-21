import type { Pool } from "postgres";
import type { DailyRate, LosDiscount, RateUpdate } from "../types.ts";
import { generateDateRange, isWeekend } from "../utils/dates.ts";

export class RateRepository {
  constructor(private pool: Pool) {}

  /**
   * Get daily rates for a unit within a date range.
   * Returns rates for each day from startDate (inclusive) to endDate (exclusive).
   */
  async getRates(unitId: string, startDate: string, endDate: string): Promise<DailyRate[]> {
    const client = await this.pool.connect();
    try {
      const result = await client.queryObject<{
        id: string;
        unit_id: string;
        date: Date;
        base_rate: number;
        currency: string;
        rate_type: string;
        seasonal_multiplier: number;
        weekend_multiplier: number;
        min_stay: number;
        created_at: Date;
        updated_at: Date;
      }>(
        `SELECT id, unit_id, date, base_rate, currency, rate_type,
                seasonal_multiplier, weekend_multiplier, min_stay,
                created_at, updated_at
         FROM daily_rates
         WHERE unit_id = $1 AND date >= $2::date AND date < $3::date
         ORDER BY date ASC`,
        [unitId, startDate, endDate]
      );

      return result.rows.map((row) => ({
        id: row.id,
        unitId: row.unit_id,
        date: row.date instanceof Date ? row.date.toISOString().split("T")[0] : String(row.date),
        baseRate: Number(row.base_rate),
        currency: row.currency,
        rateType: row.rate_type as DailyRate["rateType"],
        seasonalMultiplier: Number(row.seasonal_multiplier),
        weekendMultiplier: Number(row.weekend_multiplier),
        minStay: Number(row.min_stay),
        createdAt: row.created_at instanceof Date ? row.created_at.toISOString() : String(row.created_at),
        updatedAt: row.updated_at instanceof Date ? row.updated_at.toISOString() : String(row.updated_at),
      }));
    } finally {
      client.release();
    }
  }

  /**
   * Upsert rates for a unit over a date range.
   * Uses INSERT ... ON CONFLICT to handle existing dates.
   */
  async upsertRates(unitId: string, updates: RateUpdate[]): Promise<number> {
    const client = await this.pool.connect();
    let totalUpserted = 0;

    try {
      await client.queryArray("BEGIN");

      for (const update of updates) {
        const dates = generateDateRange(update.startDate, update.endDate);
        const currency = update.currency || "USD";
        const rateType = update.rateType || "standard";
        const seasonalMultiplier = update.seasonalMultiplier ?? 1.0;
        const weekendMultiplier = update.weekendMultiplier ?? 1.0;
        const minStay = update.minStay ?? 1;

        for (const date of dates) {
          // If an explicit weekend rate is provided and this is a weekend day,
          // use it as the base rate for that day
          let effectiveBaseRate = update.baseRate;
          if (update.weekendRate && isWeekend(date)) {
            effectiveBaseRate = update.weekendRate;
          }

          await client.queryArray(
            `INSERT INTO daily_rates (unit_id, date, base_rate, currency, rate_type,
                                     seasonal_multiplier, weekend_multiplier, min_stay, updated_at)
             VALUES ($1, $2::date, $3, $4, $5, $6, $7, $8, NOW())
             ON CONFLICT (unit_id, date) DO UPDATE SET
               base_rate = EXCLUDED.base_rate,
               currency = EXCLUDED.currency,
               rate_type = EXCLUDED.rate_type,
               seasonal_multiplier = EXCLUDED.seasonal_multiplier,
               weekend_multiplier = EXCLUDED.weekend_multiplier,
               min_stay = EXCLUDED.min_stay,
               updated_at = NOW()`,
            [unitId, date, effectiveBaseRate, currency, rateType, seasonalMultiplier, weekendMultiplier, minStay]
          );
          totalUpserted++;
        }
      }

      await client.queryArray("COMMIT");
    } catch (err) {
      await client.queryArray("ROLLBACK");
      throw err;
    } finally {
      client.release();
    }

    return totalUpserted;
  }

  /**
   * Get length-of-stay discounts for a unit, ordered by min_nights descending.
   */
  async getLosDiscounts(unitId: string): Promise<LosDiscount[]> {
    const client = await this.pool.connect();
    try {
      const result = await client.queryObject<{
        id: string;
        unit_id: string;
        min_nights: number;
        discount_percent: number;
      }>(
        `SELECT id, unit_id, min_nights, discount_percent
         FROM los_discounts
         WHERE unit_id = $1
         ORDER BY min_nights DESC`,
        [unitId]
      );

      return result.rows.map((row) => ({
        id: row.id,
        unitId: row.unit_id,
        minNights: Number(row.min_nights),
        discountPercent: Number(row.discount_percent),
      }));
    } finally {
      client.release();
    }
  }

  /**
   * Upsert length-of-stay discounts for a unit.
   */
  async upsertLosDiscounts(unitId: string, discounts: { minNights: number; discountPercent: number }[]): Promise<void> {
    const client = await this.pool.connect();
    try {
      await client.queryArray("BEGIN");
      for (const d of discounts) {
        await client.queryArray(
          `INSERT INTO los_discounts (unit_id, min_nights, discount_percent, updated_at)
           VALUES ($1, $2, $3, NOW())
           ON CONFLICT (unit_id, min_nights) DO UPDATE SET
             discount_percent = EXCLUDED.discount_percent,
             updated_at = NOW()`,
          [unitId, d.minNights, d.discountPercent]
        );
      }
      await client.queryArray("COMMIT");
    } catch (err) {
      await client.queryArray("ROLLBACK");
      throw err;
    } finally {
      client.release();
    }
  }
}
