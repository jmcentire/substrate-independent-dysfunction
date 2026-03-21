/**
 * Core types for the Availability Service
 */

/** Represents a date range for availability queries */
export interface DateRange {
  start: string; // ISO date string YYYY-MM-DD
  end: string;   // ISO date string YYYY-MM-DD
}

/** Availability status for a single unit */
export interface AvailabilityResult {
  unitId: string;
  available: boolean;
  dates: DateAvailability[];
}

/** Per-day availability */
export interface DateAvailability {
  date: string;
  available: boolean;
}

/** Availability record stored in the database (per month) */
export interface AvailabilityRecord {
  id?: string;
  unitId: string;
  year: number;
  month: number;
  bitmask: number; // 32-bit integer, each bit = one day
  updatedAt?: Date;
}

/** Request to update availability */
export interface UpdateAvailabilityRequest {
  dates: DateRange;
  available: boolean; // true = unblock, false = block
  changedBy?: string;
}

/** Bulk availability check request */
export interface BulkAvailabilityRequest {
  unitIds: string[];
  start: string;
  end: string;
}

/** Bulk availability check response */
export interface BulkAvailabilityResponse {
  results: AvailabilityResult[];
}

/** Cache key components */
export interface CacheKey {
  unitId: string;
  year: number;
  month: number;
}

/** Configuration for the service */
export interface ServiceConfig {
  port: number;
  database: {
    host: string;
    port: number;
    database: string;
    user: string;
    password: string;
    poolSize: number;
  };
  redis: {
    host: string;
    port: number;
    password?: string;
    db?: number;
  };
  cache: {
    ttlSeconds: number;
    warmupEnabled: boolean;
    keyPrefix: string;
  };
}
