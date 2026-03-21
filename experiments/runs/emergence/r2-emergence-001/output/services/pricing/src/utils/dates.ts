/**
 * Date utility functions for the pricing service.
 */

/** Check if a date string (YYYY-MM-DD) falls on a weekend (Sat/Sun) */
export function isWeekend(dateStr: string): boolean {
  const date = new Date(dateStr + "T00:00:00Z");
  const day = date.getUTCDay();
  return day === 0 || day === 6; // Sunday = 0, Saturday = 6
}

/** Generate an array of date strings from start (inclusive) to end (exclusive) */
export function generateDateRange(startDate: string, endDate: string): string[] {
  const dates: string[] = [];
  const current = new Date(startDate + "T00:00:00Z");
  const end = new Date(endDate + "T00:00:00Z");

  while (current < end) {
    dates.push(current.toISOString().split("T")[0]);
    current.setUTCDate(current.getUTCDate() + 1);
  }

  return dates;
}

/** Calculate number of nights between two dates */
export function calculateNights(startDate: string, endDate: string): number {
  const start = new Date(startDate + "T00:00:00Z");
  const end = new Date(endDate + "T00:00:00Z");
  const diffMs = end.getTime() - start.getTime();
  return Math.floor(diffMs / (1000 * 60 * 60 * 24));
}

/** Validate that start < end */
export function isValidDateRange(startDate: string, endDate: string): boolean {
  return new Date(startDate + "T00:00:00Z") < new Date(endDate + "T00:00:00Z");
}

/** Format a Date as YYYY-MM-DD */
export function formatDate(date: Date): string {
  return date.toISOString().split("T")[0];
}
