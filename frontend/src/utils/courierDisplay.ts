import type { Courier } from "../types";
import { VEHICLE_LABELS } from "./vehicleFuel";

/** Human-readable vehicle category for a courier. */
export function courierVehicleLabel(courier: Courier): string {
  return VEHICLE_LABELS[courier.vehicle_type] ?? courier.vehicle_type.replace(/_/g, " ");
}

/**
 * Identifies which courier is assigned (vehicle category + short id).
 * Used when the API exposes `courier_id` without a separate display name.
 */
export function courierAssignmentLabel(courier: Courier | null | undefined, courierId: string): string {
  const shortId = courierId.slice(0, 8);
  if (!courier) {
    return `Courier ${shortId}`;
  }
  return `${courierVehicleLabel(courier)} · ${shortId}`;
}
