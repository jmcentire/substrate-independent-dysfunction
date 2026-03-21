/**
 * Hash-sharded Redis cache for pricing data.
 *
 * Uses consistent hashing (FNV-1a) on unit_id to distribute
 * cache entries across multiple Redis nodes/shards.
 */

/**
 * FNV-1a hash function for strings, returns a 32-bit unsigned integer.
 */
export function fnv1aHash(str: string): number {
  let hash = 0x811c9dc5; // FNV offset basis
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash = (hash * 0x01000193) >>> 0; // FNV prime, keep as uint32
  }
  return hash;
}

/**
 * Determine which shard index a unit_id maps to.
 */
export function getShardIndex(unitId: string, numShards: number): number {
  if (numShards <= 0) return 0;
  return fnv1aHash(unitId) % numShards;
}

/**
 * Build a cache key for rate data.
 */
export function rateCacheKey(
  unitId: string,
  startDate: string,
  endDate: string,
): string {
  return `pricing:rates:${unitId}:${startDate}:${endDate}`;
}

/**
 * Build a cache key for a single date rate.
 */
export function singleDateCacheKey(unitId: string, date: string): string {
  return `pricing:rate:${unitId}:${date}`;
}

/**
 * Build a cache key prefix for invalidation.
 */
export function unitCachePrefix(unitId: string): string {
  return `pricing:*:${unitId}:*`;
}
