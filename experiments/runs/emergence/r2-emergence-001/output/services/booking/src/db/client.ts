import { Pool } from "postgres";
import { Config } from "../config.ts";

let pool: Pool | null = null;

export function getPool(config: Config): Pool {
  if (!pool) {
    pool = new Pool({
      hostname: config.pgHost,
      port: config.pgPort,
      user: config.pgUser,
      password: config.pgPassword,
      database: config.pgDatabase,
    }, config.pgPoolSize, true);
  }
  return pool;
}

export async function closePool(): Promise<void> {
  if (pool) {
    await pool.end();
    pool = null;
  }
}

export type { Pool };
