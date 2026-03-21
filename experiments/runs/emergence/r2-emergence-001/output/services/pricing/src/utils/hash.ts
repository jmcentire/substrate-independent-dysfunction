/**
 * FNV-1a 32-bit hash for deterministic shard assignment.
 * Used to hash-shard Redis cache keys by unit_id.
 */
export function fnv1aHash(input: string): number {
  let hash = 0x811c9dc5; // FNV offset basis
  for (let i = 0; i < input.length; i++) {
    hash ^= input.charCodeAt(i);
    hash = (hash * 0x01000193) >>> 0; // FNV prime, keep as uint32
  }
  return hash;
}

/**
 * Determine which shard a unit_id maps to.
 */
export function getShardIndex(unitId: string, shardCount: number): number {
  return fnv1aHash(unitId) % shardCount;
}

/**
 * Build a cache key for rate data.
 */
export function buildRateCacheKey(unitId: string, startDate: string, endDate: string): string {
  return `pricing:rates:${unitId}:${startDate}:${endDate}`;
}

/**
 * Build a pattern for invalidating all cache entries for a unit.
 */
export function buildUnitCachePattern(unitId: string): string {
  return `pricing:rates:${unitId}:*`;
}
