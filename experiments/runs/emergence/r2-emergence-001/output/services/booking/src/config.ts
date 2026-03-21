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

  // Redis
  redisHost: string;
  redisPort: number;
  redisPassword: string | undefined;

  // Downstream services
  availabilityServiceUrl: string;
  pricingServiceUrl: string;
  propertyServiceUrl: string;
  paymentsServiceUrl: string;
  eventServiceUrl: string;

  // Timeouts (ms)
  serviceCallTimeout: number;
  totalFlowTimeout: number;

  // Idempotency
  idempotencyTtlSeconds: number;
}

export function loadConfig(): Config {
  const env = (key: string, fallback: string): string =>
    Deno.env.get(key) ?? fallback;

  return {
    port: parseInt(env("PORT", "8007")),
    hostname: env("HOSTNAME", "0.0.0.0"),

    pgHost: env("PG_HOST", "localhost"),
    pgPort: parseInt(env("PG_PORT", "5432")),
    pgUser: env("PG_USER", "booking"),
    pgPassword: env("PG_PASSWORD", "booking"),
    pgDatabase: env("PG_DATABASE", "booking"),
    pgPoolSize: parseInt(env("PG_POOL_SIZE", "10")),

    redisHost: env("REDIS_HOST", "localhost"),
    redisPort: parseInt(env("REDIS_PORT", "6379")),
    redisPassword: Deno.env.get("REDIS_PASSWORD") || undefined,

    availabilityServiceUrl: env("AVAILABILITY_SERVICE_URL", "http://localhost:8001"),
    pricingServiceUrl: env("PRICING_SERVICE_URL", "http://localhost:8002"),
    propertyServiceUrl: env("PROPERTY_SERVICE_URL", "http://localhost:8003"),
    paymentsServiceUrl: env("PAYMENTS_SERVICE_URL", "http://localhost:8004"),
    eventServiceUrl: env("EVENT_SERVICE_URL", "http://localhost:8005"),

    serviceCallTimeout: parseInt(env("SERVICE_CALL_TIMEOUT_MS", "800")),
    totalFlowTimeout: parseInt(env("TOTAL_FLOW_TIMEOUT_MS", "3000")),

    idempotencyTtlSeconds: parseInt(env("IDEMPOTENCY_TTL_SECONDS", "86400")),
  };
}
