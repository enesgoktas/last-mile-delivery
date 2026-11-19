describe("registerCourierPushToken", () => {
  const courierId = "courier-123";
  const expoProjectId = "expo-project-id";

  beforeEach(() => {
    jest.resetModules();
    jest.clearAllMocks();
  });

  function setupHarness() {
    const updateCourierPushToken = jest.fn(async () => ({}));
    const getPermissionsAsync = jest.fn(async () => ({ status: "granted" }));
    const requestPermissionsAsync = jest.fn(async () => ({ status: "granted" }));
    const getExpoPushTokenAsync = jest.fn(async () => ({ data: "ExponentPushToken[expo-token-abc]" }));
    const setNotificationChannelAsync = jest.fn(async () => undefined);

    jest.doMock("react-native", () => ({
      Platform: { OS: "android" },
    }));
    jest.doMock("expo-constants", () => ({
      __esModule: true,
      default: {
        easConfig: { projectId: expoProjectId },
        expoConfig: { extra: { eas: { projectId: expoProjectId } } },
      },
    }));
    jest.doMock("expo-device", () => ({
      isDevice: true,
    }));
    jest.doMock("expo-notifications", () => ({
      getPermissionsAsync,
      requestPermissionsAsync,
      getExpoPushTokenAsync,
      setNotificationChannelAsync,
      AndroidImportance: {
        DEFAULT: 3,
      },
    }));
    jest.doMock("../api", () => ({
      updateCourierPushToken,
    }));

    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { registerCourierPushToken } =
      require("../pushService") as typeof import("../pushService");

    return {
      registerCourierPushToken,
      updateCourierPushToken,
      getPermissionsAsync,
      requestPermissionsAsync,
      getExpoPushTokenAsync,
      setNotificationChannelAsync,
    };
  }

  test("registers push token and calls backend when permissions granted", async () => {
    const {
      registerCourierPushToken,
      updateCourierPushToken,
      getPermissionsAsync,
      getExpoPushTokenAsync,
      setNotificationChannelAsync,
    } = setupHarness();

    const result = await registerCourierPushToken(courierId);

    expect(getPermissionsAsync).toHaveBeenCalledTimes(1);
    expect(getExpoPushTokenAsync).toHaveBeenCalledWith({ projectId: expoProjectId });
    expect(setNotificationChannelAsync).toHaveBeenCalledTimes(1);
    expect(updateCourierPushToken).toHaveBeenCalledWith(courierId, {
      push_provider: "EXPO",
      push_token: "ExponentPushToken[expo-token-abc]",
    });
    expect(result).toEqual({
      status: "registered",
      message: "Push notifications are ready.",
      token: "ExponentPushToken[expo-token-abc]",
    });
  });

  test("returns null when permission is denied", async () => {
    const updateCourierPushToken = jest.fn(async () => ({}));

    jest.doMock("react-native", () => ({
      Platform: { OS: "android" },
    }));
    jest.doMock("expo-constants", () => ({
      __esModule: true,
      default: {
        easConfig: { projectId: expoProjectId },
      },
    }));
    jest.doMock("expo-device", () => ({
      isDevice: true,
    }));
    jest.doMock("expo-notifications", () => ({
      getPermissionsAsync: jest.fn(async () => ({ status: "denied" })),
      requestPermissionsAsync: jest.fn(async () => ({ status: "denied" })),
      getExpoPushTokenAsync: jest.fn(),
      setNotificationChannelAsync: jest.fn(),
      AndroidImportance: {
        DEFAULT: 3,
      },
    }));
    jest.doMock("../api", () => ({
      updateCourierPushToken,
    }));

    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { registerCourierPushToken } =
      require("../pushService") as typeof import("../pushService");

    const result = await registerCourierPushToken(courierId);

    expect(result).toEqual({
      status: "permission_denied",
      message: "Notifications are disabled. Enable them to receive route updates.",
    });
    expect(updateCourierPushToken).not.toHaveBeenCalled();
  });

  test("returns failed state when backend registration errors", async () => {
    const error = new Error("network failure");

    jest.doMock("react-native", () => ({
      Platform: { OS: "android" },
    }));
    jest.doMock("expo-constants", () => ({
      __esModule: true,
      default: {
        easConfig: { projectId: expoProjectId },
      },
    }));
    jest.doMock("expo-device", () => ({
      isDevice: true,
    }));
    jest.doMock("expo-notifications", () => ({
      getPermissionsAsync: jest.fn(async () => ({ status: "granted" })),
      requestPermissionsAsync: jest.fn(async () => ({ status: "granted" })),
      getExpoPushTokenAsync: jest.fn(async () => ({ data: "ExponentPushToken[expo-token-xyz]" })),
      setNotificationChannelAsync: jest.fn(async () => undefined),
      AndroidImportance: {
        DEFAULT: 3,
      },
    }));
    jest.doMock("../api", () => ({
      updateCourierPushToken: jest.fn(async () => {
        throw error;
      }),
    }));

    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { registerCourierPushToken } =
      require("../pushService") as typeof import("../pushService");

    await expect(registerCourierPushToken(courierId)).resolves.toEqual({
      status: "failed",
      message: "network failure",
    });
  });

  test("returns a clear failure when the Expo project id is missing", async () => {
    const updateCourierPushToken = jest.fn(async () => ({}));

    jest.doMock("react-native", () => ({
      Platform: { OS: "ios" },
    }));
    jest.doMock("expo-constants", () => ({
      __esModule: true,
      default: {
        easConfig: null,
        expoConfig: { extra: {} },
      },
    }));
    jest.doMock("expo-device", () => ({
      isDevice: true,
    }));
    jest.doMock("expo-notifications", () => ({
      getPermissionsAsync: jest.fn(async () => ({ status: "granted" })),
      requestPermissionsAsync: jest.fn(async () => ({ status: "granted" })),
      getExpoPushTokenAsync: jest.fn(async () => ({ data: "ExponentPushToken[expo-token-xyz]" })),
      setNotificationChannelAsync: jest.fn(async () => undefined),
      AndroidImportance: {
        DEFAULT: 3,
      },
    }));
    jest.doMock("../api", () => ({
      updateCourierPushToken,
    }));

    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { registerCourierPushToken } =
      require("../pushService") as typeof import("../pushService");

    await expect(registerCourierPushToken(courierId)).resolves.toEqual({
      status: "failed",
      message:
        "Expo project ID is missing. Configure EAS project metadata before enabling push notifications.",
    });
    expect(updateCourierPushToken).not.toHaveBeenCalled();
  });
});
