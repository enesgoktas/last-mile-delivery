import type { QueueSyncReport } from "../services/offlineQueue";
import type { LocationTrackingHealth } from "../services/locationService";
import type { PushRegistrationStatus } from "../services/pushService";
import type { CourierRoute, RouteStop } from "../types";

export function buildSyncSummary(
  report: QueueSyncReport,
  includeConflictPreview = false,
): string {
  const base = [
    `Processed: ${report.processed}`,
    `Deferred: ${report.deferred}`,
    `Skipped: ${report.skipped}`,
    `Conflicts: ${report.conflicts}`,
    `Remaining: ${report.remaining}`,
  ].join("\n");

  if (!includeConflictPreview || report.conflictItems.length === 0) {
    return base;
  }

  const preview = report.conflictItems
    .slice(0, 3)
    .map((item) => `• ${item.actionType} ${item.stopId.slice(0, 8)}: ${item.reason}`)
    .join("\n");

  return `${base}\n\nConflict samples:\n${preview}`;
}

type CourierSystemWarningInput = {
  courierStatus: "ONLINE" | "OFFLINE" | "ON_BREAK";
  platformOs: string;
  pushTokenState: PushRegistrationStatus | "not_registered";
  pushWarning: string | null;
  locationTrackingHealth: LocationTrackingHealth;
};

export function buildCourierSystemWarnings(
  input: CourierSystemWarningInput,
): string[] {
  const warnings: string[] = [];

  if (input.pushWarning) {
    warnings.push(input.pushWarning);
  } else if (
    input.courierStatus === "ONLINE" &&
    input.platformOs === "ios" &&
    input.pushTokenState === "registered"
  ) {
    warnings.push(
      "iOS push delivery is not wired to the backend yet. Keep the app open for route updates.",
    );
  }

  if (input.courierStatus !== "ONLINE") {
    return warnings;
  }

  if (input.locationTrackingHealth.mode === "foreground") {
    warnings.push(
      "Background location permission is not granted. Keep the app open or enable Always Allow location access.",
    );
  }

  if (input.locationTrackingHealth.lastUploadError) {
    warnings.push(
      `Location uploads are failing: ${input.locationTrackingHealth.lastUploadError}`,
    );
  }

  return warnings;
}

export function isRouteTerminal(route: CourierRoute | null): boolean {
  if (!route) {
    return false;
  }
  const stops = route.stops ?? [];
  if (stops.length === 0) {
    return false;
  }
  return stops.every((stop) => stop.status === "COMPLETED" || stop.status === "FAILED");
}

export type RouteProgress = {
  total: number;
  terminal: number;
  percent: number;
};

export function calculateRouteProgress(stops: RouteStop[] | undefined): RouteProgress {
  const safeStops = stops ?? [];
  const total = safeStops.length;
  if (total === 0) {
    return { total: 0, terminal: 0, percent: 0 };
  }
  const terminal = safeStops.filter(
    (stop) => stop.status === "COMPLETED" || stop.status === "FAILED",
  ).length;
  const percent = Math.round((terminal / total) * 100);
  return { total, terminal, percent };
}

export function formatRelativeSyncTime(isoTimestamp: string, nowMs = Date.now()): string {
  const parsedMs = Date.parse(isoTimestamp);
  if (Number.isNaN(parsedMs)) {
    return "just now";
  }
  const deltaSeconds = Math.max(0, Math.floor((nowMs - parsedMs) / 1000));
  if (deltaSeconds < 5) {
    return "just now";
  }
  if (deltaSeconds < 60) {
    return `${deltaSeconds}s ago`;
  }
  const deltaMinutes = Math.floor(deltaSeconds / 60);
  if (deltaMinutes < 60) {
    return `${deltaMinutes}m ago`;
  }
  const deltaHours = Math.floor(deltaMinutes / 60);
  return `${deltaHours}h ago`;
}
