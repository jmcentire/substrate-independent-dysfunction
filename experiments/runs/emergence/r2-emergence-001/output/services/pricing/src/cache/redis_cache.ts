/**
 * Redis cache manager with hash-sharded connections.
 *
 * Each shard is a separate Redis connection. unit_id is hashed
 * to determine which shard handles a given unit's data.
 */
import { connect as redisConnect, type Redis } from "redis";
import type { Config } from "../config.ts";
import { getShardIndex, rateCacheKey, unitCachePrefix } from "./hash_shard.ts";
import type { DailyPrice } from "../types.ts";

export class RedisCache {
  private shards: Redis[] = [];
  private connected = false;
  private ttlSeconds: number;
  private nodes: { host: string; port: number }[];

  constructor(config: Config) {
    this.ttlSeconds = config.redisTtlSeconds;
    this.nodes = config.redisNodes;
  }

  async connect(): Promise<void> {
    if (this.connected) return;

    for (const node of this.nodes) {
      try {
        const client = await redisConnect({
          hostname: node.host,
          port: node.port,
        });
        this.shards.push(client);
      } catch (err) {
        console.error(
          `[RedisCache] Failed to connect to ${node.host}:${node.port}:`,
          err,
        );
        // Push null-like placeholder — we'll handle gracefully
        throw err;
      }
    }
    this.connected = true;
    console.log(
      `[RedisCache] Connected to ${this.shards.length} shard(s).`,
    );
  }

  /**
   * Get the Redis shard for a given unit_id.
   */
  private getShard(unitId: string): Redis {
    const idx = getShardIndex(unitId, this.shards.length);
    return this.shards[idx];
  }

  /**
   * Get cached rates for a unit + date range.
   */
  async getRates(
    unitId: string,
    startDate: string,
    endDate: string,
  ): Promise<DailyPrice[] | null> {
    if (!this.connected || this.shards.length === 0) return null;

    try {
      const key = rateCacheKey(unitId, startDate, endDate);
      const shard = this.getShard(unitId);
      const cached = await shard.get(key);
      if (cached) {
        return JSON.parse(cached) as DailyPrice[];
      }
      return null;
    } catch (err) {
      console.error("[RedisCache] getRates error:", err);
      return null;
    }
  }

  /**
   * Cache rates for a unit + date range.
   */
  async setRates(
    unitId: string,
    startDate: string,
    endDate: string,
    prices: DailyPrice[],
  ): Promise<void> {
    if (!this.connected || this.shards.length === 0) return;

    try {
      const key = rateCacheKey(unitId, startDate, endDate);
      const shard = this.getShard(unitId);
      await shard.set(key, JSON.stringify(prices), { ex: this.ttlSeconds });
    } catch (err) {
      console.error("[RedisCache] setRates error:", err);
    }
  }

  /**
   * Invalidate all cached rates for a unit.
   * Uses SCAN + DEL for safety in production.
   */
  async invalidateUnit(unitId: string): Promise<void> {
    if (!this.connected || this.shards.length === 0) return;

    try {
      // We need to invalidate across all shards since the unit might
      // have data cached on its designated shard
      const shard = this.getShard(unitId);
      const pattern = unitCachePrefix(unitId);
      let cursor = "0";
      do {
        // deno-redis returns [cursor, keys]
        const [nextCursor, keys] = await shard.scan(cursor, {
          pattern,
          count: 100,
        });
        cursor = nextCursor;
        if (keys.length > 0) {
          await shard.del(...keys);
        }
      } while (cursor !== "0");
    } catch (err) {
      console.error("[RedisCache] invalidateUnit error:", err);
    }
  }

  async close(): Promise<void> {
    for (const shard of this.shards) {
      try {
        shard.close();
      } catch {
        // ignore close errors
      }
    }
    this.shards = [];
    this.connected = false;
  }

  get isConnected(): boolean {
    return this.connected;
  }

  get shardCount(): number {
    return this.shards.length;
  }
}
