import { Platform } from "react-native";
import * as Location from "expo-location";
import * as TaskManager from "expo-task-manager";
import { updateCourierLocation } from "./api";

const LOCATION_TASK_NAME = "lmd-courier-location-updates";
const LOCATION_INTERVAL_MS = 30_000;
const LOCATION_DISTANCE_METERS = 25;
const MAX_ACCURACY_M = 100;
const MAX_STALE_MS = 120_000;

let activeCourierId: string | null = null;
let lastSentAt = 0;
let foregroundSubscription: Location.LocationSubscription | null = null;
const trackingListeners = new Set<(state: LocationTrackingHealth) => void>();

export type LocationTrackingHealth = {
  mode: "idle" | "foreground" | "background";
  lastUploadError: string | null;
  lastAttemptAt: string | null;
  lastSuccessfulUploadAt: string | null;
};

let trackingHealth: LocationTrackingHealth = {
  mode: "idle",
  lastUploadError: null,
  lastAttemptAt: null,
  lastSuccessfulUploadAt: null,
};

function toErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  if (
    typeof error === "object" &&
    error !== null &&
    "message" in error &&
    typeof (error as { message?: unknown }).message === "string"
  ) {
    return (error as { message: string }).message;
  }
  return "Unknown error";
}

function isTaskNotFoundError(error: unknown): boolean {
  const message = toErrorMessage(error).toUpperCase();
  const code =
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    typeof (error as { code?: unknown }).code === "string"
      ? (error as { code: string }).code.toUpperCase()
      : "";
  return message.includes("TASK_NOT_FOUND") || code.includes("TASK_NOT_FOUND");
}

function emitTrackingHealth() {
  const snapshot = getLocationTrackingHealth();
  for (const listener of trackingListeners) {
    listener(snapshot);
  }
}

function updateTrackingHealth(
  patch: Partial<LocationTrackingHealth>,
) {
  trackingHealth = {
    ...trackingHealth,
    ...patch,
  };
  emitTrackingHealth();
}

export function getLocationTrackingHealth(): LocationTrackingHealth {
  return { ...trackingHealth };
}

export function subscribeToLocationTrackingHealth(
  listener: (state: LocationTrackingHealth) => void,
): () => void {
  trackingListeners.add(listener);
  listener(getLocationTrackingHealth());
  return () => {
    trackingListeners.delete(listener);
  };
}

function isValidLocation(location: Location.LocationObject) {
  const accuracy = location.coords.accuracy ?? MAX_ACCURACY_M;
  const ageMs = Date.now() - (location.timestamp ?? Date.now());
  return accuracy <= MAX_ACCURACY_M && ageMs <= MAX_STALE_MS;
}

async function postLocation(courierId: string, location: Location.LocationObject) {
  const now = Date.now();
  if (now - lastSentAt < LOCATION_INTERVAL_MS - 1_000) {
    return;
  }
  if (!isValidLocation(location)) {
    return;
  }
  lastSentAt = now;
  const attemptedAt = new Date(now).toISOString();
  try {
    await updateCourierLocation(courierId, {
      lat: location.coords.latitude,
      lon: location.coords.longitude,
      heading: location.coords.heading ?? undefined,
      speed_kmh:
        typeof location.coords.speed === "number"
          ? Math.max(0, location.coords.speed) * 3.6
          : undefined,
      accuracy_m: location.coords.accuracy ?? MAX_ACCURACY_M,
    });
    updateTrackingHealth({
      lastAttemptAt: attemptedAt,
      lastSuccessfulUploadAt: attemptedAt,
      lastUploadError: null,
    });
  } catch (error) {
    updateTrackingHealth({
      lastAttemptAt: attemptedAt,
      lastUploadError: toErrorMessage(error),
    });
    throw error;
  }
}

async function maybeUploadLatest(
  locations: Location.LocationObject[] | undefined,
) {
  if (!activeCourierId || !locations?.length) {
    return;
  }
  const latest = locations[locations.length - 1];
  if (!latest) return;
  try {
    await postLocation(activeCourierId, latest);
  } catch {
    // Location delivery should not crash UI flow; next interval will retry.
  }
}

try {
  TaskManager.defineTask(
    LOCATION_TASK_NAME,
    async ({ data, error }: TaskManager.TaskManagerTaskBody<{ locations: Location.LocationObject[] }>) => {
      if (error) {
        return;
      }
      await maybeUploadLatest(data?.locations);
    },
  );
} catch {
  // Task may already be defined during hot reload.
}

async function ensurePermissions() {
  const fg = await Location.requestForegroundPermissionsAsync();
  if (!fg.granted) {
    throw new Error("Location permission denied.");
  }
  if (Platform.OS !== "web") {
    await Location.requestBackgroundPermissionsAsync();
  }
}

async function startForegroundWatcher(courierId: string) {
  if (foregroundSubscription) return;
  foregroundSubscription = await Location.watchPositionAsync(
    {
      accuracy: Location.Accuracy.Balanced,
      timeInterval: LOCATION_INTERVAL_MS,
      distanceInterval: LOCATION_DISTANCE_METERS,
      mayShowUserSettingsDialog: true,
    },
    async (location) => {
      if (!activeCourierId || activeCourierId !== courierId) return;
      try {
        await postLocation(courierId, location);
      } catch {
        // swallow, upload loop retries
      }
    },
  );
  updateTrackingHealth({ mode: "foreground" });
}

export async function startCourierLocationTracking(courierId: string) {
  activeCourierId = courierId;
  updateTrackingHealth({ lastUploadError: null });
  await ensurePermissions();

  const current = await Location.getCurrentPositionAsync({
    accuracy: Location.Accuracy.Balanced,
  });
  await postLocation(courierId, current);

  const hasBackgroundPermission = await Location.getBackgroundPermissionsAsync();
  if (hasBackgroundPermission.granted) {
    const started = await Location.hasStartedLocationUpdatesAsync(LOCATION_TASK_NAME);
    if (!started) {
      await Location.startLocationUpdatesAsync(LOCATION_TASK_NAME, {
        accuracy: Location.Accuracy.Balanced,
        timeInterval: LOCATION_INTERVAL_MS,
        distanceInterval: LOCATION_DISTANCE_METERS,
        pausesUpdatesAutomatically: true,
        foregroundService: {
          notificationTitle: "Courier tracking active",
          notificationBody: "Location updates are running for active deliveries.",
          killServiceOnDestroy: false,
        },
      });
    }
    updateTrackingHealth({ mode: "background" });
  } else {
    await startForegroundWatcher(courierId);
  }
}

export async function stopCourierLocationTracking() {
  activeCourierId = null;
  if (foregroundSubscription) {
    foregroundSubscription.remove();
    foregroundSubscription = null;
  }
  try {
    const started = await Location.hasStartedLocationUpdatesAsync(LOCATION_TASK_NAME);
    if (started) {
      await Location.stopLocationUpdatesAsync(LOCATION_TASK_NAME);
    }
  } catch (error) {
    if (!isTaskNotFoundError(error)) {
      throw error;
    }
  }
  updateTrackingHealth({
    mode: "idle",
    lastUploadError: null,
  });
}
