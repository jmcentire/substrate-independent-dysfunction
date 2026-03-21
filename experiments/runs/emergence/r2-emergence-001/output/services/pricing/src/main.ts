/**
 * Pricing Service — Entry point.
 *
 * Starts the Oak HTTP server with pricing routes.
 */
import { Application } from "oak";
import { loadConfig } from "./config.ts";
import { getPool, runMigrations } from "./db/postgres.ts";
import { RateRepository } from "./db/rate_repository.ts";
import { RedisCache } from "./cache/redis_cache.ts";
import { PricingService } from "./services/pricing_service.ts";
import { createPricingRouter } from "./routes/pricing_routes.ts";
import { createHealthRouter } from "./routes/health_routes.ts";

async function main() {
  const config = loadConfig();
  console.log(`[Pricing] Starting service on port ${config.port}...`);

  // Initialize PostgreSQL
  const pool = getPool(config);
  await runMigrations(config);
  const rateRepo = new RateRepository(pool);

  // Initialize Redis cache
  const cache = new RedisCache(config);
  try {
    await cache.connect();
  } catch (err) {
    console.warn(
      "[Pricing] Redis not available, running without cache:",
      err,
    );
  }

  // Initialize services
  const pricingService = new PricingService(rateRepo, cache, config);

  // Create Oak application
  const app = new Application();

  // Request logging middleware
  app.use(async (ctx, next) => {
    const start = Date.now();
    await next();
    const ms = Date.now() - start;
    console.log(
      `[${ctx.request.method}] ${ctx.request.url.pathname} - ${ctx.response.status} (${ms}ms)`,
    );
  });

  // Register routes
  const pricingRouter = createPricingRouter(pricingService);
  const healthRouter = createHealthRouter(cache);

  app.use(healthRouter.routes());
  app.use(healthRouter.allowedMethods());
  app.use(pricingRouter.routes());
  app.use(pricingRouter.allowedMethods());

  // Start listening
  console.log(`[Pricing] Service ready on ${config.hostname}:${config.port}`);
  await app.listen({ hostname: config.hostname, port: config.port });
}

main().catch((err) => {
  console.error("[Pricing] Fatal error:", err);
  Deno.exit(1);
});
