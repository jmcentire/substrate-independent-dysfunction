/**
 * Rate repository — PostgreSQL data access for rates.
 */
import type { Pool } from "postgres";
import type { Rate } from "../types.ts";

export class RateRepository {
  constructor(private pool: Pool) {}

  /**
   * Get rates for a unit within a date range.
   */
  async getRates(
    unitId: string,
    startDate: string,
    endDate: string,
  ): Promise<Rate[]> {
    const client = await this.pool.connect();
    try {
      const result = await client.queryObject<{
        id: string;
        unit_id: string;
        date: Date;
        base_rate: number;
        weekend_rate: number | null;
        seasonal_multiplier: string;
        minimum_stay: number;
        currency: string;
        created_at: Date;
        updated_at: Date;
      }>(
        `SELECT id, unit_id, date, base_rate, weekend_rate, seasonal_multiplier,
                minimum_stay, currency, created_at, updated_at
         FROM rates
         WHERE unit_id = $1 AND date >= $2::date AND date <= $3::date
         ORDER BY date ASC`,
        [unitId, startDate, endDate],
      );

      return result.rows.map((row) => ({
        id: row.id,
        unitId: row.unit_id,
        date: this.formatDate(row.date),
        baseRate: row.base_rate,
        weekendRate: row.weekend_rate,
        seasonalMultiplier: parseFloat(row.seasonal_multiplier),
        minimumStay: row.minimum_stay,
        currency: row.currency,
        createdAt: row.created_at,
        updatedAt: row.updated_at,
      }));
    } finally {
      client.release();
    }
  }

  /**
   * Upsert rates for a unit (insert or update on conflict).
   */
  async upsertRates(
    unitId: string,
    rates: {
      date: string;
      baseRate: number;
      weekendRate?: number | null;
      seasonalMultiplier?: number;
      minimumStay?: number;
      currency?: string;
    }[],
  ): Promise<Rate[]> {
    const client = await this.pool.connect();
    try {
      const upserted: Rate[] = [];

      // Use a transaction for batch upsert
      const tx = client.createTransaction("upsert_rates");
      await tx.begin();

      for (const rate of rates) {
        const result = await tx.queryObject<{
          id: string;
          unit_id: string;
          date: Date;
          base_rate: number;
          weekend_rate: number | null;
          seasonal_multiplier: string;
          minimum_stay: number;
          currency: string;
          created_at: Date;
          updated_at: Date;
        }>(
          `INSERT INTO rates (unit_id, date, base_rate, weekend_rate, seasonal_multiplier, minimum_stay, currency)
           VALUES ($1, $2::date, $3, $4, $5, $6, $7)
           ON CONFLICT (unit_id, date)
           DO UPDATE SET
             base_rate = EXCLUDED.base_rate,
             weekend_rate = EXCLUDED.weekend_rate,
             seasonal_multiplier = EXCLUDED.seasonal_multiplier,
             minimum_stay = EXCLUDED.minimum_stay,
             currency = EXCLUDED.currency,
             updated_at = NOW()
           RETURNING id, unit_id, date, base_rate, weekend_rate, seasonal_multiplier,
                     minimum_stay, currency, created_at, updated_at`,
          [
            unitId,
            rate.date,
            rate.baseRate,
            rate.weekendRate ?? null,
            rate.seasonalMultiplier ?? 1.0,
            rate.minimumStay ?? 1,
            rate.currency ?? "USD",
          ],
        );

        if (result.rows.length > 0) {
          const row = result.rows[0];
          upserted.push({
            id: row.id,
            unitId: row.unit_id,
            date: this.formatDate(row.date),
            baseRate: row.base_rate,
            weekendRate: row.weekend_rate,
            seasonalMultiplier: parseFloat(row.seasonal_multiplier),
            minimumStay: row.minimum_stay,
            currency: row.currency,
            createdAt: row.created_at,
            updatedAt: row.updated_at,
          });
        }
      }

      await tx.commit();
      return upserted;
    } finally {
      client.release();
    }
  }

  /**
   * Delete rates for a unit within a date range.
   */
  async deleteRates(
    unitId: string,
    startDate: string,
    endDate: string,
  ): Promise<number> {
    const client = await this.pool.connect();
    try {
      const result = await client.queryObject(
        `DELETE FROM rates
         WHERE unit_id = $1 AND date >= $2::date AND date <= $3::date`,
        [unitId, startDate, endDate],
      );
      return result.rowCount ?? 0;
    } finally {
      client.release();
    }
  }

  private formatDate(d: Date): string {
    return d.toISOString().split("T")[0];
  }
}
