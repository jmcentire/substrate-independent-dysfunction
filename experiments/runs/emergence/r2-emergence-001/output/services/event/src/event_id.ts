import { crypto, encodeHex } from "../deps.ts";

/**
 * Generate a content-addressable event ID.
 * event_id = SHA256(topic + JSON(payload) + timestamp)
 * 
 * This provides natural deduplication — identical events produce the same ID.
 */
export async function generateEventId(
  topic: string,
  payload: Record<string, unknown>,
  timestamp: string,
): Promise<string> {
  const content = topic + JSON.stringify(payload, Object.keys(payload).sort()) + timestamp;
  const encoder = new TextEncoder();
  const data = encoder.encode(content);
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  return encodeHex(new Uint8Array(hashBuffer));
}
