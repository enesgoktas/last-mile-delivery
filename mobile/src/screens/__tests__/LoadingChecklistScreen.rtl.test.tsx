import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";
import { LoadingChecklistScreen } from "../LoadingChecklistScreen";

const mockGetMyCourierProfile = jest.fn();
const mockGetCourierCurrentLoad = jest.fn();
const mockStartRoute = jest.fn();

jest.mock("../../services/api", () => ({
  getMyCourierProfile: (...args: unknown[]) => mockGetMyCourierProfile(...args),
  getCourierCurrentLoad: (...args: unknown[]) => mockGetCourierCurrentLoad(...args),
  getActiveRoute: jest.fn(),
  startRoute: (...args: unknown[]) => mockStartRoute(...args),
}));

jest.mock("../../components/BarcodeScannerModal", () => ({
  BarcodeScannerModal: ({ visible }: { visible: boolean }) => {
    const { Text } = require("react-native");
    return visible ? <Text>BarcodeScannerModal</Text> : null;
  },
}));

function renderScreen() {
  return render(
    <LoadingChecklistScreen
      navigation={{ goBack: jest.fn(), navigate: jest.fn(), replace: jest.fn() } as never}
      route={{ key: "LoadingChecklist", name: "LoadingChecklist", params: { routeId: "route-1" } } as never}
    />,
  );
}

describe("LoadingChecklistScreen RTL", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockGetMyCourierProfile.mockResolvedValue({ id: "courier-1" });
    mockGetCourierCurrentLoad.mockResolvedValue({ packages: [] });
    mockStartRoute.mockResolvedValue({ id: "route-1", status: "ACTIVE" });
  });

  it("shows load-error state and blocks start when package fetch fails", async () => {
    mockGetMyCourierProfile.mockRejectedValueOnce(new Error("backend down"));
    const { getByText } = renderScreen();

    await waitFor(() => {
      expect(getByText("Could not load package list")).toBeTruthy();
    });
    expect(getByText("Cannot start — package list failed to load")).toBeTruthy();
  });

  it("allows route start when package list is empty", async () => {
    const goBack = jest.fn();
    const { getByText } = render(
      <LoadingChecklistScreen
        navigation={{ goBack, navigate: jest.fn(), replace: jest.fn() } as never}
        route={{ key: "LoadingChecklist", name: "LoadingChecklist", params: { routeId: "route-1" } } as never}
      />,
    );

    await waitFor(() => {
      expect(getByText("No packages to confirm. You can start the route.")).toBeTruthy();
      expect(getByText("Start Route (0/0 confirmed)")).toBeTruthy();
    });

    fireEvent.press(getByText("Start Route (0/0 confirmed)"));
    await waitFor(() => {
      expect(mockStartRoute).toHaveBeenCalledWith("route-1");
      expect(goBack).toHaveBeenCalled();
    });
  });
});
