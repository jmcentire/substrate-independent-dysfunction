/**
 * Pricing Service Configuration
 * Reads from environment variables with sensible defaults.
 */
export interface Config {
  port: number;
  hostname: string;

  // PostgreSQL
  pgHost: string;
  pgPort: number;
  pgUser: string;
  pgPassword: string;
  pgDatabase: string;
  pgPoolSize: number;

  // Redis (multiple shards)
  redisNodes: { host: string; port: number }[];
  redisTtlSeconds: number;

  // Availability Service integration
  availabilityServiceUrl: string;

  // Logging
  logLevel: string;
}

function parseRedisNodes(raw: string): { host: string; port: number }[] {
  // Format: "host1:port1,host2:port2,..."
  if (!raw) return [{ host: "localhost", port: 6379 }];
  return raw.split(",").map((entry) => {
    const [host, portStr] = entry.trim().split(":");
    return { host, port: parseInt(portStr || "6379", 10) };
  });
}

export function loadConfig(): Config {
  const env = (key: string, fallback: string): string =>
    Deno.env.get(key) ?? fallback;

  return {
    port: parseInt(env("PORT", "8020"), 10),
    hostname: env("HOSTNAME", "0.0.0.0"),

    pgHost: env("PG_HOST", "localhost"),
    pgPort: parseInt(env("PG_PORT", "5432"), 10),
    pgUser: env("PG_USER", "pricing"),
    pgPassword: env("PG_PASSWORD", "pricing"),
    pgDatabase: env("PG_DATABASE", "pricing"),
    pgPoolSize: parseInt(env("PG_POOL_SIZE", "10"), 10),

    redisNodes: parseRedisNodes(env("REDIS_NODES", "localhost:6379")),
    redisTtlSeconds: parseInt(env("REDIS_TTL_SECONDS", "3600"), 10),

    availabilityServiceUrl: env(
      "AVAILABILITY_SERVICE_URL",
      "http://localhost:8010",
    ),

    logLevel: env("LOG_LEVEL", "INFO"),
  };
}
