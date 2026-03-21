// ============================================================
// Property Service - Redis Cache
// ============================================================

export interface CacheClient {
  get(key: string): Promise<string | null>;
  set(key: string, value: string, ttlSeconds?: number): Promise<void>;
  del(key: string): Promise<void>;
  delPattern(pattern: string): Promise<void>;
  close(): void;
}

export class RedisCache implements CacheClient {
  private client: any;
  private ttlSeconds: number;
  private connected = false;

  constructor(private host: string, private port: number, ttlSeconds = 300) {
    this.ttlSeconds = ttlSeconds;
  }

  async connect(): Promise<void> {
    try {
      const { connect } = await import("https://deno.land/x/redis@v0.31.0/mod.ts");
      this.client = await connect({
        hostname: this.host,
        port: this.port,
      });
      this.connected = true;
      console.log(`[RedisCache] Connected to ${this.host}:${this.port}`);
    } catch (error) {
      console.warn(`[RedisCache] Failed to connect: ${error}. Running without cache.`);
      this.connected = false;
    }
  }

  async get(key: string): Promise<string | null> {
    if (!this.connected) return null;
    try {
      const value = await this.client.get(key);
      return value ?? null;
    } catch (error) {
      console.warn(`[RedisCache] GET error for key ${key}: ${error}`);
      return null;
    }
  }

  async set(key: string, value: string, ttlSeconds?: number): Promise<void> {
    if (!this.connected) return;
    try {
      const ttl = ttlSeconds ?? this.ttlSeconds;
      await this.client.set(key, value, { ex: ttl });
    } catch (error) {
      console.warn(`[RedisCache] SET error for key ${key}: ${error}`);
    }
  }

  async del(key: string): Promise<void> {
    if (!this.connected) return;
    try {
      await this.client.del(key);
    } catch (error) {
      console.warn(`[RedisCache] DEL error for key ${key}: ${error}`);
    }
  }

  async delPattern(pattern: string): Promise<void> {
    if (!this.connected) return;
    try {
      const keys: string[] = [];
      const iter = this.client.scan(0, { pattern, count: 100 });
      // Simplified: scan once
      const [_cursor, matchedKeys] = await this.client.scan(0, { pattern, count: 1000 });
      if (matchedKeys && matchedKeys.length > 0) {
        await this.client.del(...matchedKeys);
      }
    } catch (error) {
      console.warn(`[RedisCache] DEL pattern error for ${pattern}: ${error}`);
    }
  }

  close(): void {
    if (this.connected && this.client) {
      try {
        this.client.close();
      } catch (_) {
        // ignore
      }
    }
  }
}

/**
 * In-memory cache for testing / environments without Redis.
 */
export class InMemoryCache implements CacheClient {
  private store = new Map<string, { value: string; expiresAt: number }>();
  private defaultTtl: number;

  constructor(ttlSeconds = 300) {
    this.defaultTtl = ttlSeconds;
  }

  async get(key: string): Promise<string | null> {
    const entry = this.store.get(key);
    if (!entry) return null;
    if (Date.now() > entry.expiresAt) {
      this.store.delete(key);
      return null;
    }
    return entry.value;
  }

  async set(key: string, value: string, ttlSeconds?: number): Promise<void> {
    const ttl = (ttlSeconds ?? this.defaultTtl) * 1000;
    this.store.set(key, { value, expiresAt: Date.now() + ttl });
  }

  async del(key: string): Promise<void> {
    this.store.delete(key);
  }

  async delPattern(pattern: string): Promise<void> {
    const regex = new RegExp("^" + pattern.replace(/\*/g, ".*") + "$");
    for (const key of this.store.keys()) {
      if (regex.test(key)) {
        this.store.delete(key);
      }
    }
  }

  close(): void {
    this.store.clear();
  }
}
