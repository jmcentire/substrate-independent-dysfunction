/**
 * Server setup and initialization.
 */

import { Application } from "oak";
import { createRouter } from "./router.ts";
import { AvailabilityService } from "./service.ts";
import { AvailabilityRepository } from "./repository.ts";
import { AvailabilityCache } from "./cache.ts";
import type { DbPool } from "./db.ts";
import type { RedisClient } from "./cache.ts";
import type { ServiceConfig } from "./types.ts";

export interface ServerDeps {
  pool: DbPool;
  redis: RedisClient;
  config: ServiceConfig;
}

export function createApp(deps: ServerDeps): Application {
  const repo = new AvailabilityRepository(deps.pool);
  const cache = new AvailabilityCache(
    deps.redis,
    deps.config.cache.keyPrefix,
    deps.config.cache.ttlSeconds,
  );
  const service = new AvailabilityService(repo, cache);
  const router = createRouter(service);

  const app = new Application();

  // Error handling middleware
  app.use(async (ctx, next) => {
    try {
      await next();
    } catch (err) {
      console.error("Unhandled error:", err);
      ctx.response.status = 500;
      ctx.response.body = { error: "Internal server error" };
    }
  });

  // Request logging middleware
  app.use(async (ctx, next) => {
    const start = Date.now();
    await next();
    const ms = Date.now() - start;
    console.log(`${ctx.request.method} ${ctx.request.url} - ${ctx.response.status} (${ms}ms)`);
  });

  app.use(router.routes());
  app.use(router.allowedMethods());

  return app;
}

export async function startServer(deps: ServerDeps): Promise<void> {
  const app = createApp(deps);
  const port = deps.config.port;

  console.log(`Availability service starting on port ${port}...`);
  await app.listen({ port });
}
