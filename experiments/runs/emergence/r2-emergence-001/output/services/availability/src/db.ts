/**
 * Database connection management.
 */

import type { ServiceConfig } from "./types.ts";

/** Interface for database pool (allows mocking) */
export interface DbPool {
  connect(): Promise<DbClient>;
  end(): Promise<void>;
}

export interface DbClient {
  queryObject<T = Record<string, unknown>>(query: string, args?: unknown[]): Promise<{ rows: T[] }>;
  release(): void;
}

/**
 * Create a database pool from config.
 * In production, this wraps deno-postgres Pool.
 * The interface is kept simple for testability.
 */
export async function createPool(config: ServiceConfig["database"]): Promise<DbPool> {
  const { Pool } = await import("postgres");
  const pool = new Pool({
    hostname: config.host,
    port: config.port,
    database: config.database,
    user: config.user,
    password: config.password,
  }, config.poolSize);

  return {
    async connect() {
      const client = await pool.connect();
      return {
        async queryObject<T = Record<string, unknown>>(query: string, args?: unknown[]) {
          return await client.queryObject<T>(query, args);
        },
        release() {
          client.release();
        },
      };
    },
    async end() {
      await pool.end();
    },
  };
}
