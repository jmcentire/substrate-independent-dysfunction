import { crypto, encodeHex } from "../deps.ts";

/**
 * Produces a canonical JSON string from an object.
 * - Keys sorted recursively (alphabetically)
 * - No extra whitespace
 * - Deterministic serialization
 */
export function canonicalJson(obj: unknown): string {
  if (obj === null || obj === undefined) {
    return JSON.stringify(obj);
  }

  if (typeof obj !== "object") {
    return JSON.stringify(obj);
  }

  if (Array.isArray(obj)) {
    const items = obj.map((item) => canonicalJson(item));
    return `[${items.join(",")}]`;
  }

  // Object: sort keys and recurse
  const record = obj as Record<string, unknown>;
  const sortedKeys = Object.keys(record).sort();
  const pairs = sortedKeys.map(
    (key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`
  );
  return `{${pairs.join(",")}}`;
}

/**
 * Compute content-addressable event ID.
 * event_id = SHA-256(canonical JSON of the event's identifying content)
 * 
 * We hash the combination of event_type, source, and payload to produce
 * a deterministic ID. Same content = same ID = automatic deduplication.
 */
export async function computeEventId(
  eventType: string,
  source: string,
  payload: Record<string, unknown>
): Promise<string> {
  const content = canonicalJson({ event_type: eventType, payload, source });
  const encoder = new TextEncoder();
  const data = encoder.encode(content);
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  return encodeHex(new Uint8Array(hashBuffer));
}
