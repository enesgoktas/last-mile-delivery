import React from "react";
import { render, waitFor } from "@testing-library/react-native";
import { StopDetailScreen } from "../StopDetailScreen";

const mockGetOrderPackages = jest.fn();

jest.mock("expo-image-picker", () => ({
  requestCameraPermissionsAsync: jest.fn(async () => ({ status: "granted" })),
  requestMediaLibraryPermissionsAsync: jest.fn(async () => ({ status: "granted" })),
  launchCameraAsync: jest.fn(async () => ({ canceled: true })),
  launchImageLibraryAsync: jest.fn(async () => ({ canceled: true })),
  MediaTypeOptions: { Images: "Images" },
}));

jest.mock("../../services/api", () => ({
  arriveStop: jest.fn(),
  completeStop: jest.fn(),
  failStop: jest.fn(),
  getOrderPackages: (...args: unknown[]) => mockGetOrderPackages(...args),
}));

jest.mock("../../services/offlineQueue", () => ({
  enqueueOfflineAction: jest.fn(),
}));

jest.mock("../../services/pod", () => ({
  resolveStopCompletionPayload: jest.fn(async (payload: unknown) => payload),
}));

jest.mock("../../components/BarcodeScannerModal", () => ({
  BarcodeScannerModal: ({ visible }: { visible: boolean }) => {
    const { Text: MockText } = require("react-native");
    return visible ? <MockText>BarcodeScannerModal</MockText> : null;
  },
}));

jest.mock("../../components/SignaturePadModal", () => ({
  SignaturePadModal: ({ visible }: { visible: boolean }) => {
    const { Text: MockText } = require("react-native");
    return visible ? <MockText>SignaturePadModal</MockText> : null;
  },
}));

function makeStop(overrides: Record<string, unknown> = {}) {
  return {
    id: "stop-1",
    sequence_number: 1,
    stop_type: "DELIVERY",
    status: "PENDING",
    address: "123 Main St",
    customer_phone: null,
    actual_arrival: null,
    order_id: null,
    dispatcher_note: null,
    action_policy: null,
    ...overrides,
  };
}

function renderScreen(stopOverrides: Record<string, unknown> = {}) {
  return render(
    <StopDetailScreen
      navigation={{ goBack: jest.fn(), replace: jest.fn(), navigate: jest.fn() } as never}
      route={{
        key: "StopDetail",
        name: "StopDetail",
        params: { routeId: "route-1", stop: makeStop(stopOverrides) },
      } as never}
    />,
  );
}

describe("StopDetailScreen RTL", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockGetOrderPackages.mockResolvedValue([]);
  });

  test("does not render package box when stop has no order_id", async () => {
    const { queryByText } = renderScreen({ order_id: null });

    await waitFor(() => {
      expect(queryByText("Stop Detail")).toBeTruthy();
    });
    expect(queryByText("Order packages")).toBeNull();
    expect(mockGetOrderPackages).not.toHaveBeenCalled();
  });

  test("renders fetched package rows when stop has order_id", async () => {
    mockGetOrderPackages.mockResolvedValueOnce([
      {
        id: "pkg-1",
        order_id: "ord-1",
        label: "Small Box",
        weight_kg: 1.2,
        length_cm: null,
        width_cm: null,
        height_cm: null,
        volume_cm3: 1500,
        status: "CREATED",
      },
    ]);

    const { getByText } = renderScreen({ order_id: "ord-1" });

    await waitFor(() => {
      expect(getByText("Order packages")).toBeTruthy();
      expect(getByText("Small Box")).toBeTruthy();
    });
    expect(mockGetOrderPackages).toHaveBeenCalledWith("ord-1");
  });
});
