import type { Context } from "../deps.ts";
import { BookingError } from "../types.ts";

export async function errorHandler(
  ctx: Context,
  next: () => Promise<unknown>,
): Promise<void> {
  try {
    await next();
  } catch (error) {
    if (error instanceof BookingError) {
      ctx.response.status = error.statusCode;
      ctx.response.body = {
        error: {
          code: error.code,
          message: error.message,
        },
      };
    } else {
      console.error("Unhandled error:", error);
      ctx.response.status = 500;
      ctx.response.body = {
        error: {
          code: "INTERNAL_ERROR",
          message: "An internal error occurred",
        },
      };
    }
  }
}
