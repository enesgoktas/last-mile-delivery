type HarnessOptions = {
  backgroundGranted?: boolean;
  updateFails?: boolean;
};

function makeLocation() {
  return {
    coords: {
      latitude: 41.01,
      longitude: 29.02,
      heading: 90,
      speed: 8,
      accuracy: 10,
    },
    timestamp: Date.now(),
  };
}

function setupHarness(options: HarnessOptions = {}) {
  const backgroundGranted = options.backgroundGranted ?? true;
  const updateCourierLocation = jest.fn(async () => {
    if (options.updateFails) {
      throw new Error("upload failed");
    }
    return { status: "ok" };
  });
  const requestForegroundPermissionsAsync = jest.fn(async () => ({ granted: true }));
  const requestBackgroundPermissionsAsync = jest.fn(async () => ({ granted: backgroundGranted }));
  const getCurrentPositionAsync = jest.fn(async () => makeLocation());
  const getBackgroundPermissionsAsync = jest.fn(async () => ({ granted: backgroundGranted }));
  const hasStartedLocationUpdatesAsync = jest.fn(async () => false);
  const startLocationUpdatesAsync = jest.fn(async () => undefined);
  const stopLocationUpdatesAsync = jest.fn(async () => undefined);
  const watchPositionAsync = jest.fn(async () => ({ remove: jest.fn() }));

  jest.doMock("react-native", () => ({
    Platform: { OS: "android" },
  }));
  jest.doMock("expo-location", () => ({
    Accuracy: { Balanced: 3 },
    requestForegroundPermissionsAsync,
    requestBackgroundPermissionsAsync,
    getCurrentPositionAsync,
    getBackgroundPermissionsAsync,
    hasStartedLocationUpdatesAsync,
    startLocationUpdatesAsync,
    stopLocationUpdatesAsync,
    watchPositionAsync,
  }));
  jest.doMock("expo-task-manager", () => ({
    defineTask: jest.fn(),
  }));
  jest.doMock("../api", () => ({
    updateCourierLocation,
  }));

  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const service = require("../locationService") as typeof import("../locationService");

  return {
    ...service,
    mocks: {
      getCurrentPositionAsync,
      hasStartedLocationUpdatesAsync,
      startLocationUpdatesAsync,
      stopLocationUpdatesAsync,
      updateCourierLocation,
      watchPositionAsync,
    },
  };
}

describe("locationService", () => {
  beforeEach(() => {
    jest.resetModules();
    jest.clearAllMocks();
  });

  test("uses background updates when background permission is granted", async () => {
    const { startCourierLocationTracking, getLocationTrackingHealth, mocks } =
      setupHarness({ backgroundGranted: true });

    await startCourierLocationTracking("courier-1");

    expect(mocks.updateCourierLocation).toHaveBeenCalledTimes(1);
    expect(mocks.startLocationUpdatesAsync).toHaveBeenCalledTimes(1);
    expect(mocks.watchPositionAsync).not.toHaveBeenCalled();
    expect(getLocationTrackingHealth()).toMatchObject({
      mode: "background",
      lastUploadError: null,
    });
  });

  test("falls back to foreground mode when background permission is denied", async () => {
    const { startCourierLocationTracking, getLocationTrackingHealth, mocks } =
      setupHarness({ backgroundGranted: false });

    await startCourierLocationTracking("courier-2");

    expect(mocks.updateCourierLocation).toHaveBeenCalledTimes(1);
    expect(mocks.watchPositionAsync).toHaveBeenCalledTimes(1);
    expect(mocks.startLocationUpdatesAsync).not.toHaveBeenCalled();
    expect(getLocationTrackingHealth()).toMatchObject({
      mode: "foreground",
      lastUploadError: null,
    });
  });

  test("captures upload failures in tracking health", async () => {
    const { startCourierLocationTracking, getLocationTrackingHealth } =
      setupHarness({ updateFails: true });

    await expect(startCourierLocationTracking("courier-3")).rejects.toThrow("upload failed");
    expect(getLocationTrackingHealth()).toMatchObject({
      mode: "idle",
      lastUploadError: "upload failed",
    });
    expect(getLocationTrackingHealth().lastAttemptAt).not.toBeNull();
  });

  test("ignores task-not-found while stopping background updates", async () => {
    const { stopCourierLocationTracking, getLocationTrackingHealth, mocks } = setupHarness();
    mocks.hasStartedLocationUpdatesAsync.mockRejectedValueOnce(new Error("_E_TASK_NOT_FOUND error 0."));

    await expect(stopCourierLocationTracking()).resolves.toBeUndefined();
    expect(getLocationTrackingHealth()).toMatchObject({
      mode: "idle",
      lastUploadError: null,
    });
  });

  test("ignores task-not-found when error arrives as code object", async () => {
    const { stopCourierLocationTracking, getLocationTrackingHealth, mocks } = setupHarness();
    mocks.hasStartedLocationUpdatesAsync.mockRejectedValueOnce({
      code: "E_TASK_NOT_FOUND",
      message: "The operation couldn't be completed.",
    });

    await expect(stopCourierLocationTracking()).resolves.toBeUndefined();
    expect(getLocationTrackingHealth()).toMatchObject({
      mode: "idle",
      lastUploadError: null,
    });
  });
});
