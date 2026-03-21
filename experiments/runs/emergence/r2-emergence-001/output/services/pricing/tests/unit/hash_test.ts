import { assertEquals, assertNotEquals } from "https://deno.land/std@0.208.0/assert/mod.ts";
import { fnv1aHash, getShardIndex, buildRateCacheKey, buildUnitCachePattern } from "../../src/utils/hash.ts";

Deno.test("fnv1aHash - produces consistent hash for same input", () => {
  const h1 = fnv1aHash("unit-123");
  const h2 = fnv1aHash("unit-123");
  assertEquals(h1, h2);
});

Deno.test("fnv1aHash - different inputs produce different hashes", () => {
  const h1 = fnv1aHash("unit-123");
  const h2 = fnv1aHash("unit-456");
  assertNotEquals(h1, h2);
});

Deno.test("fnv1aHash - returns a positive integer", () => {
  const h = fnv1aHash("test-unit");
  assertEquals(typeof h, "number");
  assertEquals(h >= 0, true);
});

Deno.test("getShardIndex - returns value within range", () => {
  const shardCount = 4;
  for (let i = 0; i < 100; i++) {
    const idx = getShardIndex(`unit-${i}`, shardCount);
    assertEquals(idx >= 0 && idx < shardCount, true, `Shard index ${idx} out of range for unit-${i}`);
  }
});

Deno.test("getShardIndex - consistent for same unit_id", () => {
  const idx1 = getShardIndex("unit-abc", 8);
  const idx2 = getShardIndex("unit-abc", 8);
  assertEquals(idx1, idx2);
});

Deno.test("getShardIndex - distributes across shards", () => {
  const shardCount = 4;
  const shardsUsed = new Set<number>();
  // With enough units, we should hit all shards
  for (let i = 0; i < 100; i++) {
    shardsUsed.add(getShardIndex(`unit-${i}`, shardCount));
  }
  assertEquals(shardsUsed.size, shardCount, "Not all shards were used");
});

Deno.test("buildRateCacheKey - correct format", () => {
  const key = buildRateCacheKey("unit-123", "2024-01-01", "2024-01-05");
  assertEquals(key, "pricing:rates:unit-123:2024-01-01:2024-01-05");
});

Deno.test("buildUnitCachePattern - correct format", () => {
  const pattern = buildUnitCachePattern("unit-123");
  assertEquals(pattern, "pricing:rates:unit-123:*");
});
