/**
 * Tests for route handlers.
 * Tests input validation and response formatting.
 */

import { assertEquals } from "testing";
import { describe, it } from "testing/bdd";

import { createRouteHandlers } from "../src/routes.ts";
import { AvailabilityService } from "../src/service.ts";
import { AvailabilityRepository } from "../src/repository.ts";
import { AvailabilityCache, type RedisClient, type RedisPipeline } from "../src/cache.ts";
import type { DbPool, DbClient } from "../src/db.ts";

/** Minimal mock setup for route handler tests */
function createTestService() {
  const records = new Map<string, { bitmask: number }>();
  const store = new Map<string, string>();

  const pool: DbPool = {
    async connect(): Promise<DbClient> {
      return {
        async queryObject<T = Record<string, unknown>>(query: string, args?: unknown[]): Promise<{ rows: T[] }> {
          if (query.includes("SELECT") && query.includes("FROM availability") && !query.includes("availability_log")) {
            const key = `${args?.[0]}:${args?.[1]}:${args?.[2]}`;
            const r = records.get(key);
            if (r) return { rows: [{ id: "id", unit_id: args?.[0], year: args?.[1], month: args?.[2], bitmask: r.bitmask, updated_at: new Date() }] as unknown as T[] };
            return { rows: [] };
          }
          if (query.includes("INSERT INTO availability ")) {
            const key = `${args?.[0]}:${args?.[1]}:${args?.[2]}`;
            records.set(key, { bitmask: args?.[3] as number });
            return { rows: [{ id: "id", unit_id: args?.[0], year: args?.[1], month: args?.[2], bitmask: args?.[3], updated_at: new Date() }] as unknown as T[] };
          }
          return { rows: [] };
        },
        release() {},
      };
    },
    async end() {},
  };

  const redis: RedisClient = {
    async get(key) { return store.get(key) ?? null; },
    async set(key, value) { store.set(key, value); return "OK"; },
    async del(...keys) { let c = 0; for (const k of keys) { if (store.delete(k)) c++; } return c; },
    async mget(...keys) { return keys.map((k) => store.get(k) ?? null); },
    pipeline() {
      const ops: Array<{ key: string; value: string }> = [];
      const pipe: RedisPipeline = {
        set(key, value) { ops.push({ key, value }); return pipe; },
        async flush() { for (const o of ops) store.set(o.key, o.value); return []; },
      };
      return pipe;
    },
    async keys(pattern) { const p = pattern.replace("*", ""); return [...store.keys()].filter((k) => k.startsWith(p)); },
  };

  const repo = new AvailabilityRepository(pool);
  const cache = new AvailabilityCache(redis);
  return new AvailabilityService(repo, cache);
}

describe("Route Handlers", () => {
  describe("getAvailability", () => {
    it("should return 400 when start/end missing", async () => {
      const service = createTestService();
      const handlers = createRouteHandlers(service);
      const ctx = {
        params: { unit_id: "unit-1" },
        request: { url: new URL("http://localhost/availability/unit-1") },
        response: { status: 0, body: null as unknown },
      };

      await handlers.getAvailability(ctx);
      assertEquals(ctx.response.status, 400);
    });

    it("should return 400 for invalid date format", async () => {
      const service = createTestService();
      const handlers = createRouteHandlers(service);
      const ctx = {
        params: { unit_id: "unit-1" },
        request: { url: new URL("http://localhost/availability/unit-1?start=bad&end=dates") },
        response: { status: 0, body: null as unknown },
      };

      await handlers.getAvailability(ctx);
      assertEquals(ctx.response.status, 400);
    });

    it("should return 200 with valid params", async () => {
      const service = createTestService();
      const handlers = createRouteHandlers(service);
      const ctx = {
        params: { unit_id: "unit-1" },
        request: { url: new URL("http://localhost/availability/unit-1?start=2024-03-10&end=2024-03-15") },
        response: { status: 0, body: null as unknown },
      };

      await handlers.getAvailability(ctx);
      assertEquals(ctx.response.status, 200);
      const body = ctx.response.body as { unitId: string; available: boolean };
      assertEquals(body.unitId, "unit-1");
      assertEquals(body.available, true);
    });

    it("should return 400 when start > end", async () => {
      const service = createTestService();
      const handlers = createRouteHandlers(service);
      const ctx = {
        params: { unit_id: "unit-1" },
        request: { url: new URL("http://localhost/availability/unit-1?start=2024-03-20&end=2024-03-10") },
        response: { status: 0, body: null as unknown },
      };

      await handlers.getAvailability(ctx);
      assertEquals(ctx.response.status, 400);
    });
  });

  describe("healthCheck", () => {
    it("should return 200 with status", () => {
      const service = createTestService();
      const handlers = createRouteHandlers(service);
      const ctx = {
        response: { status: 0, body: null as unknown },
      };

      handlers.healthCheck(ctx);
      assertEquals(ctx.response.status, 200);
      const body = ctx.response.body as { status: string; service: string };
      assertEquals(body.status, "healthy");
      assertEquals(body.service, "availability");
    });
  });
});
