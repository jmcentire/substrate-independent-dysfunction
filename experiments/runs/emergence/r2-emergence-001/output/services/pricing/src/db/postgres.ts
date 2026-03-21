/**
 * PostgreSQL client wrapper for the Pricing Service.
 */
import { Pool } from "postgres";
import type { Config } from "../config.ts";

let pool: Pool | null = null;

export function getPool(config: Config): Pool {
  if (!pool) {
    pool = new Pool(
      {
        hostname: config.pgHost,
        port: config.pgPort,
        user: config.pgUser,
        password: config.pgPassword,
        database: config.pgDatabase,
      },
      config.pgPoolSize,
      true, // lazy
    );
  }
  return pool;
}

export async function closePool(): Promise<void> {
  if (pool) {
    await pool.end();
    pool = null;
  }
}

/**
 * Run database migrations for the pricing service.
 */
export async function runMigrations(config: Config): Promise<void> {
  const p = getPool(config);
  const client = await p.connect();
  try {
    await client.queryObject(`
      CREATE TABLE IF NOT EXISTS rates (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        unit_id UUID NOT NULL,
        date DATE NOT NULL,
        base_rate INTEGER NOT NULL,
        weekend_rate INTEGER,
        seasonal_multiplier NUMERIC(5,3) NOT NULL DEFAULT 1.000,
        minimum_stay INTEGER NOT NULL DEFAULT 1,
        currency VARCHAR(3) NOT NULL DEFAULT 'USD',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(unit_id, date)
      );
    `);

    await client.queryObject(`
      CREATE INDEX IF NOT EXISTS idx_rates_unit_date
      ON rates (unit_id, date);
    `);

    await client.queryObject(`
      CREATE INDEX IF NOT EXISTS idx_rates_unit_date_range
      ON rates (unit_id, date ASC);
    `);

    console.log("[DB] Migrations completed successfully.");
  } finally {
    client.release();
  }
}
