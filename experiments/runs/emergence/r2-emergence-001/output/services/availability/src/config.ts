import type { ServiceConfig } from "./types.ts";

/**
 * Load configuration from environment variables with defaults.
 */
export function loadConfig(): ServiceConfig {
  return {
    port: parseInt(Deno.env.get("PORT") || "8001", 10),
    database: {
      host: Deno.env.get("DB_HOST") || "localhost",
      port: parseInt(Deno.env.get("DB_PORT") || "5432", 10),
      database: Deno.env.get("DB_NAME") || "availability",
      user: Deno.env.get("DB_USER") || "postgres",
      password: Deno.env.get("DB_PASSWORD") || "postgres",
      poolSize: parseInt(Deno.env.get("DB_POOL_SIZE") || "10", 10),
    },
    redis: {
      host: Deno.env.get("REDIS_HOST") || "localhost",
      port: parseInt(Deno.env.get("REDIS_PORT") || "6379", 10),
      password: Deno.env.get("REDIS_PASSWORD") || undefined,
      db: Deno.env.get("REDIS_DB") ? parseInt(Deno.env.get("REDIS_DB")!, 10) : 0,
    },
    cache: {
      ttlSeconds: parseInt(Deno.env.get("CACHE_TTL_SECONDS") || "3600", 10),
      warmupEnabled: Deno.env.get("CACHE_WARMUP_ENABLED") !== "false",
      keyPrefix: Deno.env.get("CACHE_KEY_PREFIX") || "avail:",
    },
  };
}
