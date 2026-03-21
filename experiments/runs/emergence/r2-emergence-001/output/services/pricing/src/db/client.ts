import { Pool } from "postgres";
import type { Config } from "../config.ts";

let pool: Pool | null = null;

export function getPool(config: Config): Pool {
  if (!pool) {
    pool = new Pool(config.databaseUrl, 10, true);
  }
  return pool;
}

export async function closePool(): Promise<void> {
  if (pool) {
    await pool.end();
    pool = null;
  }
}

export async function initializeDatabase(config: Config): Promise<void> {
  const p = getPool(config);
  const client = await p.connect();
  try {
    const schema = await Deno.readTextFile(
      new URL("./schema.sql", import.meta.url).pathname
    );
    await client.queryArray(schema);
    console.log("[pricing] Database schema initialized");
  } finally {
    client.release();
  }
}
