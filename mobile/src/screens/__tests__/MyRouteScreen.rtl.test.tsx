import React from "react";
import { render, fireEvent, waitFor } from "@testing-library/react-native";

const mockNavigate = jest.fn();
const mockReplace = jest.fn();

const mockGetMyCourierProfile = jest.fn();
const mockGetActiveRoute = jest.fn();
const mockGetNextStop = jest.fn();
const mockGetOfflineQueue = jest.fn();
const mockSyncOfflineQueue = jest.fn();
const mockStartCourierLocationTracking = jest.fn();
const mockStopCourierLocationTracking = jest.fn();
const mockAsyncStorageGetItem = jest.fn();
const mockAsyncStorageSetItem = jest.fn();

jest.mock("@react-navigation/native", () => ({
  useFocusEffect: () => undefined,
}));

jest.mock("../../services/api", () => ({
  getMyCourierProfile: (...args: unknown[]) => mockGetMyCourierProfile(...args),
  getActiveRoute: (...args: unknown[]) => mockGetActiveRoute(...args),
  getNextStop: (...args: unknown[]) => mockGetNextStop(...args),
  updateCourierStatus: jest.fn(),
  startRoute: jest.fn(),
  logout: jest.fn(),
}));

jest.mock("../../services/offlineQueue", () => ({
  getOfflineQueue: (...args: unknown[]) => mockGetOfflineQueue(...args),
  syncOfflineQueue: (...args: unknown[]) => mockSyncOfflineQueue(...args),
}));

jest.mock("../../services/locationService", () => ({
  getLocationTrackingHealth: () => ({
    mode: "idle",
    lastUploadError: null,
    lastAttemptAt: null,
    lastSuccessfulUploadAt: null,
  }),
  startCourierLocationTracking: (...args: unknown[]) => mockStartCourierLocationTracking(...args),
  stopCourierLocationTracking: (...args: unknown[]) => mockStopCourierLocationTracking(...args),
  subscribeToLocationTrackingHealth: () => () => undefined,
}));

jest.mock("../../services/pushService", () => ({
  registerCourierPushToken: jest.fn(async () => ({ status: "registered", message: "ok" })),
  getPushRegistrationWarning: jest.fn(() => null),
}));

jest.mock("@react-native-async-storage/async-storage", () => {
  const api = {
    getItem: (...args: unknown[]) => mockAsyncStorageGetItem(...args),
    setItem: (...args: unknown[]) => mockAsyncStorageSetItem(...args),
  };
  return {
    __esModule: true,
    ...api,
    default: api,
  };
});

function renderScreen() {
  const { MyRouteScreen } = require("../MyRouteScreen") as typeof import("../MyRouteScreen");
  return render(
    <MyRouteScreen
      navigation={{ navigate: mockNavigate, replace: mockReplace } as never}
      route={{ key: "MyRoute", name: "MyRoute", params: undefined } as never}
    />,
  );
}

describe("MyRouteScreen RTL", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockAsyncStorageGetItem.mockResolvedValue("true");
    mockAsyncStorageSetItem.mockResolvedValue(undefined);
    mockGetMyCourierProfile.mockResolvedValue({
      id: "courier-1",
      availability_status: "OFFLINE",
      push_token: null,
      fcm_token: null,
    });
    mockGetNextStop.mockResolvedValue(null);
    mockGetOfflineQueue.mockResolvedValue([]);
    mockSyncOfflineQueue.mockResolvedValue({
      processed: 0,
      deferred: 0,
      skipped: 0,
      conflicts: 0,
      conflictItems: [],
      remaining: 0,
    });
    mockStartCourierLocationTracking.mockResolvedValue(undefined);
    mockStopCourierLocationTracking.mockResolvedValue(undefined);
  });

  it("shows empty state when there is no active route", async () => {
    mockGetActiveRoute.mockResolvedValue(null);
    const { getByText, unmount } = renderScreen();

    await waitFor(() => {
      expect(getByText("No active route")).toBeTruthy();
    });
    unmount();
  });

  it("navigates to loading checklist when starting a planned route", async () => {
    mockGetActiveRoute.mockResolvedValue({
      id: "route-1",
      status: "PLANNED",
      stops: [],
    } as never);
    const { getByText, unmount } = renderScreen();

    await waitFor(() => {
      expect(getByText("Start Route")).toBeTruthy();
    });

    fireEvent.press(getByText("Start Route"));
    expect(mockNavigate).toHaveBeenCalledWith("LoadingChecklist", { routeId: "route-1" });
    unmount();
  });

  it("shows NEXT indicator and sequence for the actual next stop", async () => {
    mockGetActiveRoute.mockResolvedValue({
      id: "route-2",
      status: "IN_PROGRESS",
      stops: [
        {
          id: "stop-1",
          sequence_number: 1,
          status: "PENDING",
          stop_type: "PICKUP",
          address: "First Stop Address",
        },
        {
          id: "stop-2",
          sequence_number: 2,
          status: "PENDING",
          stop_type: "DROPOFF",
          address: "Second Stop Address",
        },
      ],
    } as never);
    mockGetNextStop.mockResolvedValue({ id: "stop-2" } as never);

    const { getByText, unmount } = renderScreen();

    await waitFor(() => {
      expect(getByText("Second Stop Address")).toBeTruthy();
    });

    expect(getByText("NEXT")).toBeTruthy();
    expect(getByText("2")).toBeTruthy();
    unmount();
  });
});
