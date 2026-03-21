/**
 * Redis bitmask cache for O(1) availability checks.
 *
 * Cache key format: avail:{unit_id}:{year}:{month}
 * Cache value: hex-encoded bitmask string
 *
 * Strategy:
 * - Read-through: on cache miss, load from DB and populate cache
 * - Write-through: on update, write to DB first then update cache
 * - TTL-based expiry with configurable duration
 * - Bulk operations for cache warming
 */

import { bitmaskToHex, hexToBitmask } from "./bitmask.ts";

/** Interface for Redis operations (allows mocking in tests) */
export interface RedisClient {
  get(key: string): Promise<string | null | undefined>;
  set(key: string, value: string, opts?: { ex?: number }): Promise<unknown>;
  del(...keys: string[]): Promise<number>;
  mget(...keys: string[]): Promise<(string | null | undefined)[]>;
  pipeline(): RedisPipeline;
  keys(pattern: string): Promise<string[]>;
}

export interface RedisPipeline {
  set(key: string, value: string, opts?: { ex?: number }): RedisPipeline;
  flush(): Promise<unknown[]>;
}

export class AvailabilityCache {
  private redis: RedisClient;
  private keyPrefix: string;
  private ttlSeconds: number;

  constructor(redis: RedisClient, keyPrefix: string = "avail:", ttlSeconds: number = 3600) {
    this.redis = redis;
    this.keyPrefix = keyPrefix;
    this.ttlSeconds = ttlSeconds;
  }

  /** Build cache key */
  buildKey(unitId: string, year: number, month: number): string {
    return `${this.keyPrefix}${unitId}:${year}:${month}`;
  }

  /** Get bitmask from cache. Returns null on cache miss. */
  async get(unitId: string, year: number, month: number): Promise<number | null> {
    const key = this.buildKey(unitId, year, month);
    const value = await this.redis.get(key);
    if (value === null || value === undefined) {
      return null;
    }
    return hexToBitmask(value);
  }

  /** Set bitmask in cache with TTL */
  async set(unitId: string, year: number, month: number, bitmask: number): Promise<void> {
    const key = this.buildKey(unitId, year, month);
    const value = bitmaskToHex(bitmask);
    await this.redis.set(key, value, { ex: this.ttlSeconds });
  }

  /** Invalidate cache entry */
  async invalidate(unitId: string, year: number, month: number): Promise<void> {
    const key = this.buildKey(unitId, year, month);
    await this.redis.del(key);
  }

  /** Invalidate all cache entries for a unit */
  async invalidateUnit(unitId: string): Promise<void> {
    const pattern = `${this.keyPrefix}${unitId}:*`;
    const keys = await this.redis.keys(pattern);
    if (keys.length > 0) {
      await this.redis.del(...keys);
    }
  }

  /** Bulk get bitmasks for multiple month keys */
  async getMulti(
    queries: Array<{ unitId: string; year: number; month: number }>,
  ): Promise<Map<string, number | null>> {
    if (queries.length === 0) {
      return new Map();
    }

    const keys = queries.map((q) => this.buildKey(q.unitId, q.year, q.month));
    const values = await this.redis.mget(...keys);
    const results = new Map<string, number | null>();

    for (let i = 0; i < queries.length; i++) {
      const key = keys[i];
      const value = values[i];
      results.set(key, value != null ? hexToBitmask(value) : null);
    }

    return results;
  }

  /** Bulk set bitmasks (cache warming) */
  async setMulti(
    entries: Array<{ unitId: string; year: number; month: number; bitmask: number }>,
  ): Promise<void> {
    if (entries.length === 0) return;

    const pipeline = this.redis.pipeline();
    for (const entry of entries) {
      const key = this.buildKey(entry.unitId, entry.year, entry.month);
      const value = bitmaskToHex(entry.bitmask);
      pipeline.set(key, value, { ex: this.ttlSeconds });
    }
    await pipeline.flush();
  }
}
