// ============================================================
// Platform Defaults Repository (Tier 1)
// ============================================================

import type { PlatformDefaults } from "../models/types.ts";

export interface PlatformRepository {
  getDefaults(): Promise<PlatformDefaults | null>;
}

export class PostgresPlatformRepository implements PlatformRepository {
  constructor(private pool: any) {}

  async getDefaults(): Promise<PlatformDefaults | null> {
    const result = await this.pool.queryObject<PlatformDefaults>(
      `SELECT id, settings, created_at, updated_at FROM platform_defaults LIMIT 1`
    );
    return result.rows[0] ?? null;
  }
}

/**
 * In-memory implementation for testing.
 */
export class InMemoryPlatformRepository implements PlatformRepository {
  private defaults: PlatformDefaults | null = null;

  setDefaults(defaults: PlatformDefaults): void {
    this.defaults = defaults;
  }

  async getDefaults(): Promise<PlatformDefaults | null> {
    return this.defaults;
  }
}
