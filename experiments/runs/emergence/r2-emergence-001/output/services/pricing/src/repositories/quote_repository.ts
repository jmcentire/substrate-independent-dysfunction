import type { Pool } from "postgres";
import type { QuoteResponse } from "../types.ts";

export class QuoteRepository {
  constructor(private pool: Pool) {}

  /**
   * Persist a quote for audit trail.
   */
  async saveQuote(quote: QuoteResponse): Promise<void> {
    const client = await this.pool.connect();
    try {
      await client.queryArray(
        `INSERT INTO quotes (id, unit_id, start_date, end_date, guests, subtotal,
                            los_discount, cleaning_fee, service_fee, taxes,
                            grand_total, currency, expires_at)
         VALUES ($1, $2, $3::date, $4::date, $5, $6, $7, $8, $9, $10, $11, $12, $13::timestamptz)`,
        [
          quote.quoteId,
          quote.unitId,
          quote.startDate,
          quote.endDate,
          1, // guests — can be extended
          quote.subtotal,
          quote.losDiscount,
          quote.cleaningFee,
          quote.serviceFee,
          quote.taxes,
          quote.grandTotal,
          quote.currency,
          quote.expiresAt,
        ]
      );
    } finally {
      client.release();
    }
  }

  /**
   * Retrieve a quote by ID if not expired.
   */
  async getQuote(quoteId: string): Promise<QuoteResponse | null> {
    const client = await this.pool.connect();
    try {
      const result = await client.queryObject<{
        id: string;
        unit_id: string;
        start_date: Date;
        end_date: Date;
        subtotal: number;
        los_discount: number;
        cleaning_fee: number;
        service_fee: number;
        taxes: number;
        grand_total: number;
        currency: string;
        expires_at: Date;
      }>(
        `SELECT id, unit_id, start_date, end_date, subtotal, los_discount,
                cleaning_fee, service_fee, taxes, grand_total, currency, expires_at
         FROM quotes
         WHERE id = $1 AND expires_at > NOW()`,
        [quoteId]
      );

      if (result.rows.length === 0) return null;

      const row = result.rows[0];
      return {
        quoteId: row.id,
        unitId: row.unit_id,
        startDate: row.start_date instanceof Date ? row.start_date.toISOString().split("T")[0] : String(row.start_date),
        endDate: row.end_date instanceof Date ? row.end_date.toISOString().split("T")[0] : String(row.end_date),
        currency: row.currency,
        nights: [], // Not stored in DB; would need to recalculate
        subtotal: Number(row.subtotal),
        totalBeforeDiscount: Number(row.subtotal),
        losDiscount: Number(row.los_discount),
        total: Number(row.subtotal) - Number(row.los_discount),
        averageNightlyRate: 0,
        expiresAt: row.expires_at instanceof Date ? row.expires_at.toISOString() : String(row.expires_at),
        cleaningFee: Number(row.cleaning_fee),
        serviceFee: Number(row.service_fee),
        taxes: Number(row.taxes),
        grandTotal: Number(row.grand_total),
      };
    } finally {
      client.release();
    }
  }
}
