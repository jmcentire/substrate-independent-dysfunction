// ============================================================
// Property Service - Configuration
// ============================================================

export interface Config {
  port: number;
  databaseUrl: string;
  redisUrl: string;
  redisHost: string;
  redisPort: number;
  cacheTtlSeconds: number;
  environment: string;
}

export function loadConfig(): Config {
  const redisUrl = Deno.env.get("REDIS_URL") || "redis://localhost:6379";
  const redisUrlParsed = new URL(redisUrl);

  return {
    port: parseInt(Deno.env.get("PORT") || "8003", 10),
    databaseUrl: Deno.env.get("DATABASE_URL") || "postgresql://localhost:5432/wander_property",
    redisUrl,
    redisHost: redisUrlParsed.hostname || "localhost",
    redisPort: parseInt(redisUrlParsed.port || "6379", 10),
    cacheTtlSeconds: parseInt(Deno.env.get("CACHE_TTL_SECONDS") || "300", 10),
    environment: Deno.env.get("DENO_ENV") || "development",
  };
}
