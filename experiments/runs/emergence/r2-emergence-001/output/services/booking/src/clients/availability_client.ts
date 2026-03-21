import { BaseServiceClient } from "./base_client.ts";
import type { AvailabilityCheckResult, AvailabilityHold } from "../types.ts";

export interface AvailabilityClient {
  checkAvailability(
    propertyId: string,
    unitId: string,
    checkIn: string,
    checkOut: string,
  ): Promise<AvailabilityCheckResult>;

  holdDates(
    propertyId: string,
    unitId: string,
    checkIn: string,
    checkOut: string,
  ): Promise<AvailabilityHold>;

  releaseDates(holdId: string): Promise<void>;
}

export class HttpAvailabilityClient extends BaseServiceClient implements AvailabilityClient {
  async checkAvailability(
    propertyId: string,
    unitId: string,
    checkIn: string,
    checkOut: string,
  ): Promise<AvailabilityCheckResult> {
    const params = new URLSearchParams({
      property_id: propertyId,
      unit_id: unitId,
      check_in: checkIn,
      check_out: checkOut,
    });
    const response = await this.fetchWithTimeout(
      `/availability/check?${params.toString()}`,
    );
    return this.parseJsonResponse<AvailabilityCheckResult>(response);
  }

  async holdDates(
    propertyId: string,
    unitId: string,
    checkIn: string,
    checkOut: string,
  ): Promise<AvailabilityHold> {
    const response = await this.fetchWithTimeout(`/availability/hold`, {
      method: "POST",
      body: JSON.stringify({
        property_id: propertyId,
        unit_id: unitId,
        check_in: checkIn,
        check_out: checkOut,
      }),
    });
    return this.parseJsonResponse<AvailabilityHold>(response);
  }

  async releaseDates(holdId: string): Promise<void> {
    await this.fetchWithTimeout(`/availability/hold/${holdId}`, {
      method: "DELETE",
    });
  }
}
