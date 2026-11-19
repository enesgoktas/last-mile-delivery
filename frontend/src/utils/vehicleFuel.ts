import type { FuelType, VehicleSpec, VehicleType } from "../types";

export const VEHICLE_LABELS: Record<VehicleType, string> = {
  MOTORCYCLE: "Motorcycle",
  CAR_VAN: "Car / Van",
  TRUCK: "Truck",
};

export const FUEL_LABELS: Record<FuelType, string> = {
  GASOLINE: "Gasoline",
  DIESEL: "Diesel",
  LPG: "LPG",
  ELECTRIC: "Electric",
  HYBRID: "Hybrid",
};

export const VEHICLE_OPTIONS: VehicleType[] = ["MOTORCYCLE", "CAR_VAN", "TRUCK"];
export const FUEL_OPTIONS: FuelType[] = ["GASOLINE", "DIESEL", "LPG", "ELECTRIC", "HYBRID"];

export const VEHICLE_TYPE_FUEL_DEFAULTS: Record<
  VehicleType,
  { fuel_type: FuelType; fuel_efficiency_km_per_liter: number }
> = {
  MOTORCYCLE: { fuel_type: "GASOLINE", fuel_efficiency_km_per_liter: 36 },
  CAR_VAN: { fuel_type: "DIESEL", fuel_efficiency_km_per_liter: 16 },
  TRUCK: { fuel_type: "DIESEL", fuel_efficiency_km_per_liter: 5 },
};

export function vehicleFuelDefaultLabel(vehicleType: VehicleType): string {
  const profile = VEHICLE_TYPE_FUEL_DEFAULTS[vehicleType];
  return `${FUEL_LABELS[profile.fuel_type]} · ${profile.fuel_efficiency_km_per_liter.toFixed(1)} km/L`;
}

export function vehicleSpecLabel(spec: VehicleSpec): string {
  const parts = [spec.make, spec.model];
  if (spec.year) parts.push(String(spec.year));
  parts.push(`· ${FUEL_LABELS[spec.fuel_type] ?? spec.fuel_type}`);
  parts.push(`· ${spec.fuel_efficiency_km_per_liter.toFixed(1)} km/L`);
  return parts.join(" ");
}
