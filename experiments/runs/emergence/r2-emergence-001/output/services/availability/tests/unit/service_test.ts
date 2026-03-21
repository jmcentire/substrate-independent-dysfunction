/**
 * Unit tests for the AvailabilityService.
 * Uses mock repository and cache to test business logic.
 */

import { assertEquals } from "testing";
import { describe, it, beforeEach } from "testing/bdd";

import { AvailabilityService } from "../../src/service.ts";
import { AvailabilityRepository } from "../../src/repository.ts";
import { AvailabilityCache, type RedisClient, type RedisPipeline } from "../../src/cache.ts";
import { fullMonthMask, blockRange } from "../../src/bitmask.ts";
import type { DbPool, DbClient } from "../../src/db.ts";
import type { AvailabilityRecord } from "../../src/types.ts";

/** In-memory store to simulate DB */
function createMockPool(): DbPool & { records: Map<string, { bitmask: number }> } {
  const records = new Map<string, { bitmask: number }>();
  const logs: Array<unknown> = [];

  const pool: DbPool & { records: Map<string, { bitmask: number }> } = {
    records,
    async connect(): Promise<DbClient> {
      return {
        async queryObject<T = Record<string, unknown>>(query: string, args?: unknown[]): Promise<{ rows: T[] }> {
          // Simulate SELECT
          if (query.includes("SELECT") && query.includes("FROM availability") && !query.includes("availability_log")) {
            const unitId = args?.[0] as string;
            const year = args?.[1] as number;
            const month = args?.[2] as number;
            const key = `${unitId}:${year}:${month}`;
            const record = records.get(key);
            if (record) {
              return {
                rows: [{
                  id: "mock-id",
                  unit_id: unitId,
                  year,
                  month,
                  bitmask: record.bitmask,
                  updated_at: new Date(),
                }] as unknown as T[],
              };
            }
            return { rows: [] };
          }
          // Simulate INSERT/UPSERT
          if (query.includes("INSERT INTO availability")) {
            const unitId = args?.[0] as string;
            const year = args?.[1] as number;
            const month = args?.[2] as number;
            const bitmask = args?.[3] as number;
            const key = `${unitId}:${year}:${month}`;
            records.set(key, { bitmask });
            return {
              rows: [{
                id: "mock-id",
                unit_id: unitId,
                year,
                month,
                bitmask,
                updated_at: new Date(),
              }] as unknown as T[],
            };
          }
          // Simulate log insert
          if (query.includes("INSERT INTO availability_log")) {
            logs.push(args);
            return { rows: [] };
          }
          return { rows: [] };
        },
        release() {},
      };
    },
    async end() {},
  };

  return pool;
}

function createMockRedis(): RedisClient & { store: Map<string, string> } {
  const store = new Map<string, string>();
  return {
    store,
    async get(key: string) { return store.get(key) ?? null; },
    async set(key: string, value: string) { store.set(key, value); return "OK"; },
    async del(...keys: string[]) {
      let c = 0;
      for (const k of keys) { if (store.delete(k)) c++; }
      return c;
    },
    async mget(...keys: string[]) { return keys.map((k) => store.get(k) ?? null); },
    pipeline() {
      const ops: Array<{ key: string; value: string }> = [];
      const pipe: RedisPipeline = {
        set(key: string, value: string) { ops.push({ key, value }); return pipe; },
        async flush() { for (const o of ops) store.set(o.key, o.value); return []; },
      };
      return pipe;
    },
    async keys(pattern: string) {
      const prefix = pattern.replace("*", "");
      return [...store.keys()].filter((k) => k.startsWith(prefix));
    },
  };
}

describe("AvailabilityService", () => {
  let pool: ReturnType<typeof createMockPool>;
  let redis: ReturnType<typeof createMockRedis>;
  let service: AvailabilityService;

  beforeEach(() => {
    pool = createMockPool();
    redis = createMockRedis();
    const repo = new AvailabilityRepository(pool);
    const cache = new AvailabilityCache(redis, "avail:", 3600);
    service = new AvailabilityService(repo, cache);
  });

  describe("checkAvailability", () => {
    it("should return all available for new unit (no DB records)", async () => {
      const result = await service.checkAvailability(
        "unit-1",
        "2024-03-10",
        "2024-03-15",
      );

      assertEquals(result.unitId, "unit-1");
      assertEquals(result.available, true);
      assertEquals(result.dates.length, 6); // days 10-15
      for (const d of result.dates) {
        assertEquals(d.available, true);
      }
    });

    it("should return unavailable when days are blocked", async () => {
      // Pre-populate DB with blocked days
      const mask = blockRange(fullMonthMask(2024, 3), 12, 14);
      pool.records.set("unit-1:2024:3", { bitmask: mask });

      const result = await service.checkAvailability(
        "unit-1",
        "2024-03-10",
        "2024-03-15",
      );

      assertEquals(result.available, false);
      assertEquals(result.dates[0].available, true);  // day 10
      assertEquals(result.dates[1].available, true);  // day 11
      assertEquals(result.dates[2].available, false); // day 12
      assertEquals(result.dates[3].available, false); // day 13
      assertEquals(result.dates[4].available, false); // day 14
      assertEquals(result.dates[5].available, true);  // day 15
    });

    it("should use cache on second call (cache hit)", async () => {
      // First call populates cache
      await service.checkAvailability("unit-1", "2024-03-10", "2024-03-15");

      // Verify cache was populated
      const cached = redis.store.get("avail:unit-1:2024:3");
      assertEquals(cached !== undefined, true);

      // Modify DB directly (cache should still return old value)
      pool.records.set("unit-1:2024:3", { bitmask: 0 });

      // Second call should use cache (still available)
      const result = await service.checkAvailability("unit-1", "2024-03-10", "2024-03-15");
      assertEquals(result.available, true); // from cache, not DB
    });
  });

  describe("updateAvailability", () => {
    it("should block dates", async () => {
      const result = await service.updateAvailability("unit-1", {
        dates: { start: "2024-03-10", end: "2024-03-15" },
        available: false,
      });

      assertEquals(result.available, false);
      for (const d of result.dates) {
        assertEquals(d.available, false);
      }
    });

    it("should unblock dates", async () => {
      // First block
      await service.updateAvailability("unit-1", {
        dates: { start: "2024-03-10", end: "2024-03-15" },
        available: false,
      });

      // Then unblock
      const result = await service.updateAvailability("unit-1", {
        dates: { start: "2024-03-10", end: "2024-03-15" },
        available: true,
      });

      assertEquals(result.available, true);
      for (const d of result.dates) {
        assertEquals(d.available, true);
      }
    });

    it("should update cache after write (write-through)", async () => {
      await service.updateAvailability("unit-1", {
        dates: { start: "2024-03-10", end: "2024-03-15" },
        available: false,
      });

      // Cache should reflect the blocked state
      const cached = redis.store.get("avail:unit-1:2024:3");
      assertEquals(cached !== undefined, true);

      // Check that cached value has blocked days
      const result = await service.checkAvailability("unit-1", "2024-03-10", "2024-03-15");
      assertEquals(result.available, false);
    });

    it("should handle cross-month updates", async () => {
      const result = await service.updateAvailability("unit-1", {
        dates: { start: "2024-03-25", end: "2024-04-05" },
        available: false,
      });

      assertEquals(result.available, false);
      // Check March days (25-31) + April days (1-5)
      assertEquals(result.dates.length, 12);
    });
  });

  describe("bulkCheckAvailability", () => {
    it("should check multiple units", async () => {
      // Block some days for unit-2
      const mask = blockRange(fullMonthMask(2024, 3), 10, 15);
      pool.records.set("unit-2:2024:3", { bitmask: mask });

      const result = await service.bulkCheckAvailability(
        ["unit-1", "unit-2"],
        "2024-03-10",
        "2024-03-15",
      );

      assertEquals(result.results.length, 2);

      const unit1 = result.results.find((r) => r.unitId === "unit-1");
      const unit2 = result.results.find((r) => r.unitId === "unit-2");

      assertEquals(unit1?.available, true);
      assertEquals(unit2?.available, false);
    });

    it("should handle empty unit list", async () => {
      const result = await service.bulkCheckAvailability(
        [],
        "2024-03-10",
        "2024-03-15",
      );

      assertEquals(result.results.length, 0);
    });
  });
});
