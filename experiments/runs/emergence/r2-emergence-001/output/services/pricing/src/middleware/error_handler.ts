/**
 * Global error handling middleware for Oak.
 */
import type { Context, Next } from "oak";

export async function errorHandler(ctx: Context, next: Next): Promise<void> {
  try {
    await next();
  } catch (err) {
    console.error("[ErrorHandler] Unhandled error:", err);

    const status = (err as { status?: number }).status ?? 500;
    const message =
      err instanceof Error ? err.message : "Internal server error";

    ctx.response.status = status;
    ctx.response.body = {
      error: message,
      timestamp: new Date().toISOString(),
    };
  }
}
