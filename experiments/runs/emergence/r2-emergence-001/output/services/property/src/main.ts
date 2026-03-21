// ============================================================
// Property Service - Entry Point
// ============================================================

import { Application } from "https://deno.land/x/oak@v12.6.1/mod.ts";
import { Pool } from "https://deno.land/x/postgres@v0.17.0/mod.ts";
import { loadConfig } from "./config.ts";
import { RedisCache } from "./cache/redis_cache.ts";
import { PostgresPlatformRepository } from "./repositories/platform_repository.ts";
import { PostgresBrandRepository } from "./repositories/brand_repository.ts";
import { PostgresPropertyRepository } from "./repositories/property_repository.ts";
import { PostgresUnitRepository } from "./repositories/unit_repository.ts";
import { SettingsResolver } from "./services/settings_resolver.ts";
import { PropertyService } from "./services/property_service.ts";
import { UnitService } from "./services/unit_service.ts";
import { PropertyHandler } from "./handlers/property_handler.ts";
import { UnitHandler } from "./handlers/unit_handler.ts";
import { createRouter } from "./router.ts";
import { errorHandler, requestLogger } from "./middleware/error_handler.ts";

const config = loadConfig();

// --- Database ---
const pool = new Pool(config.databaseUrl, 10, true);

// --- Cache ---
const cache = new RedisCache(config.redisHost, config.redisPort, config.cacheTtlSeconds);
await cache.connect();

// --- Repositories ---
const platformRepo = new PostgresPlatformRepository(pool);
const brandRepo = new PostgresBrandRepository(pool);
const propertyRepo = new PostgresPropertyRepository(pool);
const unitRepo = new PostgresUnitRepository(pool);

// --- Services ---
const settingsResolver = new SettingsResolver({
  platformRepo,
  brandRepo,
  cache,
});

const propertyService = new PropertyService(propertyRepo, cache, settingsResolver);
const unitService = new UnitService(unitRepo, propertyRepo, cache, settingsResolver);

// --- Handlers ---
const propertyHandler = new PropertyHandler(propertyService);
const unitHandler = new UnitHandler(unitService);

// --- Router ---
const router = createRouter(propertyHandler, unitHandler);

// --- Application ---
const app = new Application();

// Middleware
app.use(errorHandler);
app.use(requestLogger);

// Routes
app.use(router.routes());
app.use(router.allowedMethods());

// Start
console.log(`[PropertyService] Starting on port ${config.port}...`);
console.log(`[PropertyService] Environment: ${config.environment}`);

await app.listen({ port: config.port });
