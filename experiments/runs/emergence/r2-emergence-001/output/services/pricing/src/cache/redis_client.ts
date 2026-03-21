import { connect as redisConnect, type Redis } from "redis";
import type { Config } from "../config.ts";
import { getShardIndex } from "../utils/hash.ts";

/**
 * Hash-sharded Redis client manager.
 * In production, each shard would be a separate Redis instance.
 * For single-instance dev, we simulate shards via key prefixes.
 */
export class ShardedRedisCache {
  private clients: (Redis | null)[];
  private shardCount: number;
  private ttlSeconds: number;
  private config: Config;

  constructor(config: Config) {
    this.shardCount = config.redisShardCount;
    this.ttlSeconds = config.cacheTtlSeconds;
    this.config = config;
    this.clients = new Array(this.shardCount).fill(null);
  }

  async connect(): Promise<void> {
    const url = new URL(this.config.redisUrl);
    const hostname = url.hostname || "localhost";
    const port = parseInt(url.port || "6379", 10);
    const password = url.password || undefined;

    for (let i = 0; i < this.shardCount; i++) {
      try {
        this.clients[i] = await redisConnect({
          hostname,
          port,
          password,
          // In production, each shard would connect to a different host/port
          // For dev we connect to the same instance and use key prefixes
          db: i % 16, // use different Redis databases as shard simulation
        });
      } catch (err) {
        console.warn(`[pricing] Redis shard ${i} connection failed:`, err);
        this.clients[i] = null;
      }
    }
  }

  private getClient(unitId: string): Redis | null {
    const shard = getShardIndex(unitId, this.shardCount);
    return this.clients[shard];
  }

  async get(key: string, unitId: string): Promise<string | null> {
    const client = this.getClient(unitId);
    if (!client) return null;
    try {
      const value = await client.get(key);
      return value ?? null;
    } catch (err) {
      console.warn(`[pricing] Cache get error:`, err);
      return null;
    }
  }

  async set(key: string, unitId: string, value: string): Promise<void> {
    const client = this.getClient(unitId);
    if (!client) return;
    try {
      await client.set(key, value, { ex: this.ttlSeconds });
    } catch (err) {
      console.warn(`[pricing] Cache set error:`, err);
    }
  }

  async invalidateUnit(unitId: string): Promise<void> {
    const client = this.getClient(unitId);
    if (!client) return;
    try {
      // SCAN and delete keys matching the unit pattern
      const pattern = `pricing:rates:${unitId}:*`;
      let cursor = "0";
      do {
        const [nextCursor, keys] = await client.scan(cursor, { pattern, count: 100 });
        cursor = nextCursor;
        if (keys.length > 0) {
          await client.del(...keys);
        }
      } while (cursor !== "0");
    } catch (err) {
      console.warn(`[pricing] Cache invalidation error:`, err);
    }
  }

  async close(): Promise<void> {
    for (const client of this.clients) {
      if (client) {
        try {
          await client.close();
        } catch { /* ignore */ }
      }
    }
    this.clients = new Array(this.shardCount).fill(null);
  }
}

/**
 * A no-op cache for testing or when Redis is unavailable.
 */
export class NoOpCache {
  async connect(): Promise<void> {}
  async get(_key: string, _unitId: string): Promise<string | null> { return null; }
  async set(_key: string, _unitId: string, _value: string): Promise<void> {}
  async invalidateUnit(_unitId: string): Promise<void> {}
  async close(): Promise<void> {}
}

export type CacheClient = ShardedRedisCache | NoOpCache;
