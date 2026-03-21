/**
 * Availability Service - business logic layer.
 *
 * Coordinates between the repository (DB) and cache (Redis)
 * to provide O(1) availability checks via bitmask operations.
 */

import { AvailabilityRepository } from "./repository.ts";
import { AvailabilityCache } from "./cache.ts";
import {
  splitDateRangeByMonth,
  isRangeAvailable,
  blockRange,
  unblockRange,
  fullMonthMask,
  isDayAvailable,
  formatDate,
  daysInMonth,
} from "./bitmask.ts";
import type {
  AvailabilityResult,
  DateAvailability,
  UpdateAvailabilityRequest,
  BulkAvailabilityResponse,
} from "./types.ts";

export class AvailabilityService {
  private repo: AvailabilityRepository;
  private cache: AvailabilityCache;

  constructor(repo: AvailabilityRepository, cache: AvailabilityCache) {
    this.repo = repo;
    this.cache = cache;
  }

  /**
   * Check availability for a unit over a date range.
   * Uses cache-first strategy with DB fallback.
   */
  async checkAvailability(
    unitId: string,
    startDate: string,
    endDate: string,
  ): Promise<AvailabilityResult> {
    const segments = splitDateRangeByMonth(startDate, endDate);
    let allAvailable = true;
    const dates: DateAvailability[] = [];

    for (const seg of segments) {
      const bitmask = await this.getBitmaskWithCache(unitId, seg.year, seg.month);

      // O(1) range check
      const segAvailable = isRangeAvailable(bitmask, seg.startDay, seg.endDay);
      if (!segAvailable) {
        allAvailable = false;
      }

      // Build per-day details
      for (let d = seg.startDay; d <= seg.endDay; d++) {
        const dayAvail = isDayAvailable(bitmask, d);
        dates.push({
          date: formatDate(seg.year, seg.month, d),
          available: dayAvail,
        });
      }
    }

    return {
      unitId,
      available: allAvailable,
      dates,
    };
  }

  /**
   * Update availability for a unit (block or unblock dates).
   */
  async updateAvailability(
    unitId: string,
    request: UpdateAvailabilityRequest,
  ): Promise<AvailabilityResult> {
    const segments = splitDateRangeByMonth(request.dates.start, request.dates.end);

    for (const seg of segments) {
      // Get current bitmask (from DB, not cache, for accuracy)
      const currentBitmask = await this.repo.getEffectiveBitmask(
        unitId,
        seg.year,
        seg.month,
      );

      // Apply change
      const newBitmask = request.available
        ? unblockRange(currentBitmask, seg.startDay, seg.endDay)
        : blockRange(currentBitmask, seg.startDay, seg.endDay);

      // Write to DB
      await this.repo.upsertMonthAvailability(
        unitId,
        seg.year,
        seg.month,
        newBitmask,
      );

      // Update cache (write-through)
      await this.cache.set(unitId, seg.year, seg.month, newBitmask);
    }

    // Log the change
    await this.repo.logChange(
      unitId,
      request.available ? "unblock" : "block",
      request.dates.start,
      request.dates.end,
      request.changedBy,
    );

    // Return updated availability
    return this.checkAvailability(unitId, request.dates.start, request.dates.end);
  }

  /**
   * Bulk availability check for multiple units.
   */
  async bulkCheckAvailability(
    unitIds: string[],
    startDate: string,
    endDate: string,
  ): Promise<BulkAvailabilityResponse> {
    const results: AvailabilityResult[] = [];

    // Process in parallel for better performance
    const promises = unitIds.map((unitId) =>
      this.checkAvailability(unitId, startDate, endDate)
    );

    const settled = await Promise.allSettled(promises);
    for (const result of settled) {
      if (result.status === "fulfilled") {
        results.push(result.value);
      }
    }

    return { results };
  }

  /**
   * Get bitmask with cache-first strategy.
   * On cache miss, loads from DB and populates cache.
   */
  private async getBitmaskWithCache(
    unitId: string,
    year: number,
    month: number,
  ): Promise<number> {
    // Try cache first
    const cached = await this.cache.get(unitId, year, month);
    if (cached !== null) {
      return cached;
    }

    // Cache miss - load from DB
    const bitmask = await this.repo.getEffectiveBitmask(unitId, year, month);

    // Populate cache for next time
    await this.cache.set(unitId, year, month, bitmask);

    return bitmask;
  }

  /**
   * Warm cache for a unit (load all months from DB into cache).
   */
  async warmCache(unitId: string): Promise<number> {
    const records = await this.repo.getAllForUnit(unitId);
    if (records.length === 0) return 0;

    const entries = records.map((r) => ({
      unitId: r.unitId,
      year: r.year,
      month: r.month,
      bitmask: r.bitmask,
    }));

    await this.cache.setMulti(entries);
    return entries.length;
  }

  /**
   * Invalidate all cache entries for a unit.
   */
  async invalidateCache(unitId: string): Promise<void> {
    await this.cache.invalidateUnit(unitId);
  }
}
