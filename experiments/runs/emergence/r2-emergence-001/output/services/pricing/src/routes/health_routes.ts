/**
 * Health check routes.
 */
import { Router, type Context } from "oak";
import type { RedisCache } from "../cache/redis_cache.ts";

export function createHealthRouter(cache: RedisCache): Router {
  const router = new Router();

  router.get("/health", (ctx: Context) => {
    ctx.response.status = 200;
    ctx.response.body = {
      status: "ok",
      service: "pricing",
      timestamp: new Date().toISOString(),
      redis: cache.isConnected ? "connected" : "disconnected",
      redisShards: cache.shardCount,
    };
  });

  router.get("/health/ready", (ctx: Context) => {
    const ready = cache.isConnected;
    ctx.response.status = ready ? 200 : 503;
    ctx.response.body = {
      status: ready ? "ready" : "not_ready",
      service: "pricing",
    };
  });

  return router;
}
