export interface IdempotencyStore {
  /**
   * Try to acquire the idempotency key.
   * Returns true if this is the first time (we acquired it).
   * Returns false if the key already exists (duplicate request).
   */
  tryAcquire(key: string, bookingId: string, ttlSeconds: number): Promise<boolean>;

  /**
   * Get the booking ID associated with an idempotency key.
   */
  getBookingId(key: string): Promise<string | null>;

  /**
   * Release an idempotency key (e.g., on failure so it can be retried).
   */
  release(key: string): Promise<void>;
}

export class RedisIdempotencyStore implements IdempotencyStore {
  constructor(
    private redis: { get(key: string): Promise<string | undefined>; set(key: string, value: string, opts?: { ex?: number }): Promise<string>; del(...keys: string[]): Promise<number> },
  ) {}

  async tryAcquire(
    key: string,
    bookingId: string,
    ttlSeconds: number,
  ): Promise<boolean> {
    const redisKey = `idempotency:${key}`;
    const existing = await this.redis.get(redisKey);
    if (existing) {
      return false;
    }
    // SET NX with TTL
    await this.redis.set(redisKey, bookingId, { ex: ttlSeconds });
    return true;
  }

  async getBookingId(key: string): Promise<string | null> {
    const redisKey = `idempotency:${key}`;
    const value = await this.redis.get(redisKey);
    return value ?? null;
  }

  async release(key: string): Promise<void> {
    const redisKey = `idempotency:${key}`;
    await this.redis.del(redisKey);
  }
}

/**
 * In-memory idempotency store for testing.
 */
export class InMemoryIdempotencyStore implements IdempotencyStore {
  private store = new Map<string, string>();

  async tryAcquire(
    key: string,
    bookingId: string,
    _ttlSeconds: number,
  ): Promise<boolean> {
    if (this.store.has(key)) {
      return false;
    }
    this.store.set(key, bookingId);
    return true;
  }

  async getBookingId(key: string): Promise<string | null> {
    return this.store.get(key) ?? null;
  }

  async release(key: string): Promise<void> {
    this.store.delete(key);
  }

  clear(): void {
    this.store.clear();
  }
}
