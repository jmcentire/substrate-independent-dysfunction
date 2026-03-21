import { ServiceUnavailableError, TimeoutError } from "../types.ts";

export class BaseServiceClient {
  constructor(
    protected baseUrl: string,
    protected timeoutMs: number = 800,
  ) {}

  protected async fetchWithTimeout(
    path: string,
    options: RequestInit = {},
  ): Promise<Response> {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const url = `${this.baseUrl}${path}`;
      const response = await fetch(url, {
        ...options,
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          ...options.headers,
        },
      });
      return response;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new TimeoutError(
          `Service call to ${this.baseUrl}${path} timed out after ${this.timeoutMs}ms`,
        );
      }
      throw new ServiceUnavailableError(
        `Service at ${this.baseUrl} is unavailable: ${(error as Error).message}`,
      );
    } finally {
      clearTimeout(timeoutId);
    }
  }

  protected async parseJsonResponse<T>(response: Response): Promise<T> {
    if (!response.ok) {
      const body = await response.text();
      throw new ServiceUnavailableError(
        `Service returned ${response.status}: ${body}`,
      );
    }
    return await response.json() as T;
  }
}
