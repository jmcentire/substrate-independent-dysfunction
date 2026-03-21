/**
 * Unit tests for the Redis cache layer.
 * Tests: cache hit, cache miss, invalidation, bulk operations.
 */

import { assertEquals } from "testing";
import { describe, it, beforeEach } from "testing/bdd";

import { AvailabilityCache, type RedisClient, type RedisPipeline } from "../../src/cache.ts";
import { fullMonthMask, bitmaskToHex, hexToBitmask } from "../../src/bitmask.ts";

/** In-memory mock Redis client for testing */
function createMockRedis(): RedisClient & { store: Map<string, string> } {
  const store = new Map<string, string>();

  const mockRedis: RedisClient & { store: Map<string, string> } = {
    store,
    async get(key: string) {
      return store.get(key) ?? null;
    },
    async set(key: string, value: string, _opts?: { ex?: number }) {
      store.set(key, value);
      return "OK";
    },
    async del(...keys: string[]) {
      let count = 0;
      for (const key of keys) {
        if (store.delete(key)) count++;
      }
      return count;
    },
    async mget(...keys: string[]) {
      return keys.map((k) => store.get(k) ?? null);
    },
    pipeline() {
      const ops: Array<{ key: string; value: string }> = [];
      const pipe: RedisPipeline = {
        set(key: string, value: string, _opts?: { ex?: number }) {
          ops.push({ key, value });
          return pipe;
        },
        async flush() {
          for (const op of ops) {
            store.set(op.key, op.value);
          }
          return ops.map(() => "OK");
        },
      };
      return pipe;
    },
    async keys(pattern: string) {
      const prefix = pattern.replace("*", "");
      return [...store.keys()].filter((k) => k.startsWith(prefix));
    },
  };

  return mockRedis;
}

describe("AvailabilityCache", () => {
  let redis: ReturnType<typeof createMockRedis>;
  let cache: AvailabilityCache;

  beforeEach(() => {
    redis = createMockRedis();
    cache = new AvailabilityCache(redis, "avail:", 3600);
  });

  describe("cache miss", () => {
    it("should return null on cache miss", async () => {
      const result = await cache.get("unit-1", 2024, 3);
      assertEquals(result, null);
    });
  });

  describe("cache hit", () => {
    it("should return bitmask on cache hit", async () => {
      const mask = fullMonthMask(2024, 3);
      await cache.set("unit-1", 2024, 3, mask);

      const result = await cache.get("unit-1", 2024, 3);
      assertEquals(result, mask);
    });

    it("should store as hex in Redis", async () => {
      const mask = fullMonthMask(2024, 3);
      await cache.set("unit-1", 2024, 3, mask);

      const rawValue = redis.store.get("avail:unit-1:2024:3");
      assertEquals(rawValue, bitmaskToHex(mask));
    });
  });

  describe("cache key format", () => {
    it("should build correct cache key", () => {
      const key = cache.buildKey("abc-123", 2024, 6);
      assertEquals(key, "avail:abc-123:2024:6");
    });
  });

  describe("invalidation", () => {
    it("should invalidate a specific entry", async () => {
      await cache.set("unit-1", 2024, 3, 123);
      assertEquals(await cache.get("unit-1", 2024, 3), 123);

      await cache.invalidate("unit-1", 2024, 3);
      assertEquals(await cache.get("unit-1", 2024, 3), null);
    });

    it("should invalidate all entries for a unit", async () => {
      await cache.set("unit-1", 2024, 1, 100);
      await cache.set("unit-1", 2024, 2, 200);
      await cache.set("unit-1", 2024, 3, 300);
      await cache.set("unit-2", 2024, 1, 400); // different unit

      await cache.invalidateUnit("unit-1");

      assertEquals(await cache.get("unit-1", 2024, 1), null);
      assertEquals(await cache.get("unit-1", 2024, 2), null);
      assertEquals(await cache.get("unit-1", 2024, 3), null);
      assertEquals(await cache.get("unit-2", 2024, 1), 400); // unaffected
    });
  });

  describe("bulk operations", () => {
    it("should get multiple entries at once", async () => {
      await cache.set("unit-1", 2024, 1, 100);
      await cache.set("unit-1", 2024, 2, 200);

      const results = await cache.getMulti([
        { unitId: "unit-1", year: 2024, month: 1 },
        { unitId: "unit-1", year: 2024, month: 2 },
        { unitId: "unit-1", year: 2024, month: 3 }, // miss
      ]);

      assertEquals(results.get("avail:unit-1:2024:1"), 100);
      assertEquals(results.get("avail:unit-1:2024:2"), 200);
      assertEquals(results.get("avail:unit-1:2024:3"), null);
    });

    it("should set multiple entries at once (cache warming)", async () => {
      await cache.setMulti([
        { unitId: "unit-1", year: 2024, month: 1, bitmask: 100 },
        { unitId: "unit-1", year: 2024, month: 2, bitmask: 200 },
        { unitId: "unit-1", year: 2024, month: 3, bitmask: 300 },
      ]);

      assertEquals(await cache.get("unit-1", 2024, 1), 100);
      assertEquals(await cache.get("unit-1", 2024, 2), 200);
      assertEquals(await cache.get("unit-1", 2024, 3), 300);
    });
  });
});
