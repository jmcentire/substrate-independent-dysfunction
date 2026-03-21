/**
 * Unit tests for the hash-sharding module.
 */
import {
  assertEquals,
  assertStrictEquals,
} from "https://deno.land/std@0.208.0/assert/mod.ts";
import {
  fnv1aHash,
  getShardIndex,
  rateCacheKey,
  singleDateCacheKey,
  unitCachePrefix,
} from "../../src/cache/hash_shard.ts";

Deno.test("fnv1aHash - deterministic for same input", () => {
  const h1 = fnv1aHash("unit-123");
  const h2 = fnv1aHash("unit-123");
  assertStrictEquals(h1, h2);
});

Deno.test("fnv1aHash - different for different inputs", () => {
  const h1 = fnv1aHash("unit-123");
  const h2 = fnv1aHash("unit-456");
  // While collisions are theoretically possible, these inputs should differ
  assertEquals(h1 !== h2, true);
});

Deno.test("fnv1aHash - returns unsigned 32-bit integer", () => {
  const hash = fnv1aHash("test-string");
  assertEquals(hash >= 0, true);
  assertEquals(hash <= 0xFFFFFFFF, true);
});

Deno.test("getShardIndex - returns valid index for 1 shard", () => {
  const idx = getShardIndex("unit-abc", 1);
  assertStrictEquals(idx, 0);
});

Deno.test("getShardIndex - returns valid index for multiple shards", () => {
  const numShards = 4;
  const idx = getShardIndex("unit-xyz", numShards);
  assertEquals(idx >= 0, true);
  assertEquals(idx < numShards, true);
});

Deno.test("getShardIndex - consistent for same unit_id", () => {
  const idx1 = getShardIndex("unit-consistent", 8);
  const idx2 = getShardIndex("unit-consistent", 8);
  assertStrictEquals(idx1, idx2);
});

Deno.test("getShardIndex - distributes across shards", () => {
  const numShards = 4;
  const shardCounts = new Array(numShards).fill(0);

  // Generate many unit IDs and check distribution
  for (let i = 0; i < 100; i++) {
    const unitId = `unit-${crypto.randomUUID()}`;
    const idx = getShardIndex(unitId, numShards);
    shardCounts[idx]++;
  }

  // Each shard should have at least some entries (rough check)
  for (let i = 0; i < numShards; i++) {
    assertEquals(shardCounts[i] > 0, true, `Shard ${i} got no entries`);
  }
});

Deno.test("getShardIndex - handles 0 shards gracefully", () => {
  const idx = getShardIndex("unit-1", 0);
  assertStrictEquals(idx, 0);
});

Deno.test("rateCacheKey - correct format", () => {
  const key = rateCacheKey("unit-123", "2024-01-01", "2024-01-05");
  assertStrictEquals(key, "pricing:rates:unit-123:2024-01-01:2024-01-05");
});

Deno.test("singleDateCacheKey - correct format", () => {
  const key = singleDateCacheKey("unit-456", "2024-06-15");
  assertStrictEquals(key, "pricing:rate:unit-456:2024-06-15");
});

Deno.test("unitCachePrefix - correct format for invalidation", () => {
  const prefix = unitCachePrefix("unit-789");
  assertStrictEquals(prefix, "pricing:*:unit-789:*");
});
