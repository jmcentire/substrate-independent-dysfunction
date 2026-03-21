import type { Pool } from "../db/client.ts";
import type { Booking, BookingListQuery, BookingStatus } from "../types.ts";

export interface BookingRepository {
  create(booking: Omit<Booking, "created_at" | "updated_at">): Promise<Booking>;
  findById(id: string): Promise<Booking | null>;
  findByIdempotencyKey(key: string): Promise<Booking | null>;
  list(query: BookingListQuery): Promise<Booking[]>;
  updateStatus(
    id: string,
    status: BookingStatus,
    extra?: Partial<Pick<Booking, "cancelled_at" | "cancellation_reason" | "payment_id">>,
  ): Promise<Booking | null>;
}

export class PostgresBookingRepository implements BookingRepository {
  constructor(private pool: Pool) {}

  async create(
    booking: Omit<Booking, "created_at" | "updated_at">,
  ): Promise<Booking> {
    const client = await this.pool.connect();
    try {
      const result = await client.queryObject<Booking>(
        `INSERT INTO bookings (
          id, property_id, unit_id, guest_id,
          check_in, check_out, status, total_price, currency,
          guests, payment_id, idempotency_key, cancelled_at, cancellation_reason
        ) VALUES (
          $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
        ) RETURNING *`,
        [
          booking.id,
          booking.property_id,
          booking.unit_id,
          booking.guest_id,
          booking.check_in,
          booking.check_out,
          booking.status,
          booking.total_price,
          booking.currency,
          booking.guests,
          booking.payment_id,
          booking.idempotency_key,
          booking.cancelled_at,
          booking.cancellation_reason,
        ],
      );
      return this.mapRow(result.rows[0]);
    } finally {
      client.release();
    }
  }

  async findById(id: string): Promise<Booking | null> {
    const client = await this.pool.connect();
    try {
      const result = await client.queryObject<Booking>(
        `SELECT * FROM bookings WHERE id = $1`,
        [id],
      );
      return result.rows.length > 0 ? this.mapRow(result.rows[0]) : null;
    } finally {
      client.release();
    }
  }

  async findByIdempotencyKey(key: string): Promise<Booking | null> {
    const client = await this.pool.connect();
    try {
      const result = await client.queryObject<Booking>(
        `SELECT * FROM bookings WHERE idempotency_key = $1`,
        [key],
      );
      return result.rows.length > 0 ? this.mapRow(result.rows[0]) : null;
    } finally {
      client.release();
    }
  }

  async list(query: BookingListQuery): Promise<Booking[]> {
    const conditions: string[] = [];
    const params: unknown[] = [];
    let paramIdx = 1;

    if (query.guest_id) {
      conditions.push(`guest_id = $${paramIdx++}`);
      params.push(query.guest_id);
    }
    if (query.property_id) {
      conditions.push(`property_id = $${paramIdx++}`);
      params.push(query.property_id);
    }
    if (query.status) {
      conditions.push(`status = $${paramIdx++}`);
      params.push(query.status);
    }

    const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
    const limit = query.limit ?? 50;
    const offset = query.offset ?? 0;

    const sql = `SELECT * FROM bookings ${where} ORDER BY created_at DESC LIMIT $${paramIdx++} OFFSET $${paramIdx++}`;
    params.push(limit, offset);

    const client = await this.pool.connect();
    try {
      const result = await client.queryObject<Booking>(sql, params);
      return result.rows.map((r) => this.mapRow(r));
    } finally {
      client.release();
    }
  }

  async updateStatus(
    id: string,
    status: BookingStatus,
    extra?: Partial<Pick<Booking, "cancelled_at" | "cancellation_reason" | "payment_id">>,
  ): Promise<Booking | null> {
    const sets: string[] = [`status = $2`, `updated_at = NOW()`];
    const params: unknown[] = [id, status];
    let paramIdx = 3;

    if (extra?.cancelled_at) {
      sets.push(`cancelled_at = $${paramIdx++}`);
      params.push(extra.cancelled_at);
    }
    if (extra?.cancellation_reason) {
      sets.push(`cancellation_reason = $${paramIdx++}`);
      params.push(extra.cancellation_reason);
    }
    if (extra?.payment_id) {
      sets.push(`payment_id = $${paramIdx++}`);
      params.push(extra.payment_id);
    }

    const client = await this.pool.connect();
    try {
      const result = await client.queryObject<Booking>(
        `UPDATE bookings SET ${sets.join(", ")} WHERE id = $1 RETURNING *`,
        params,
      );
      return result.rows.length > 0 ? this.mapRow(result.rows[0]) : null;
    } finally {
      client.release();
    }
  }

  private mapRow(row: Record<string, unknown>): Booking {
    return {
      id: String(row.id),
      property_id: String(row.property_id),
      unit_id: String(row.unit_id),
      guest_id: String(row.guest_id),
      check_in: String(row.check_in),
      check_out: String(row.check_out),
      status: String(row.status) as BookingStatus,
      total_price: Number(row.total_price),
      currency: String(row.currency),
      guests: Number(row.guests),
      payment_id: row.payment_id ? String(row.payment_id) : null,
      idempotency_key: row.idempotency_key ? String(row.idempotency_key) : null,
      cancelled_at: row.cancelled_at ? String(row.cancelled_at) : null,
      cancellation_reason: row.cancellation_reason ? String(row.cancellation_reason) : null,
      created_at: String(row.created_at),
      updated_at: String(row.updated_at),
    };
  }
}
