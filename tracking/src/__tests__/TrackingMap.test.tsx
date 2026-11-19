import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen } from "@testing-library/react";

// vi.hoisted ensures these classes are available inside the vi.mock factory,
// which is hoisted to the top of the file before any `class` declarations run.
const { MockMap, MockMarker, MockPopup, MockNavigationControl } = vi.hoisted(() => {
  type MapEventCallback = (event?: unknown) => void;

  class MockMap {
    static lastInstance: MockMap | null = null;

    private handlers: Record<string, MapEventCallback[]> = {};
    addControl = vi.fn();
    remove = vi.fn();
    fitBounds = vi.fn();
    easeTo = vi.fn();
    on = vi.fn((event: string, cb: MapEventCallback) => {
      if (!this.handlers[event]) this.handlers[event] = [];
      this.handlers[event].push(cb);
    });

    emit(event: string, payload?: unknown) {
      (this.handlers[event] ?? []).forEach((cb) => cb(payload));
    }

    constructor() {
      MockMap.lastInstance = this;
    }
  }

  // Track constructor arguments so tests can assert `new Marker({color: ...})`.
  // Arrow functions can't be used as constructors; we use a class with a static tracker.
  class MockMarker {
    static calls: unknown[][] = [];
    setLngLat = vi.fn().mockReturnThis();
    setPopup = vi.fn().mockReturnThis();
    addTo = vi.fn().mockReturnThis();
    remove = vi.fn();

    constructor(...args: unknown[]) {
      MockMarker.calls.push(args);
    }
  }

  class MockPopup {
    setText = vi.fn().mockReturnThis();
  }

  class MockNavigationControl {}

  return { MockMap, MockMarker, MockPopup, MockNavigationControl };
});

vi.mock("maplibre-gl", () => ({
  default: {
    Map: MockMap,
    Marker: MockMarker,
    Popup: MockPopup,
    NavigationControl: MockNavigationControl,
  },
  Map: MockMap,
  Marker: MockMarker,
  Popup: MockPopup,
  NavigationControl: MockNavigationControl,
}));

vi.mock("maplibre-gl/dist/maplibre-gl.css", () => ({}));

import { TrackingMap } from "../TrackingMap";

function renderMap(
  props: Partial<Parameters<typeof TrackingMap>[0]> & { deliveryLat?: number; deliveryLon?: number } = {},
) {
  return render(
    <TrackingMap
      deliveryLat={props.deliveryLat ?? 41.0}
      deliveryLon={props.deliveryLon ?? 29.0}
      courierLat={props.courierLat}
      courierLon={props.courierLon}
    />,
  );
}

describe("TrackingMap", () => {
  beforeEach(() => {
    MockMap.lastInstance = null;
    MockMarker.calls = [];
    vi.clearAllMocks();
  });

  it("renders the map container div with correct aria attributes", () => {
    renderMap();
    const mapDiv = screen.getByRole("img", { name: /live delivery map/i });
    expect(mapDiv).toBeInTheDocument();
  });

  it("shows loading overlay before map fires the load event", () => {
    renderMap();
    expect(screen.getByText("Loading map…")).toBeInTheDocument();
  });

  it("hides loading overlay after the load event fires", async () => {
    renderMap();
    await act(async () => {
      MockMap.lastInstance?.emit("load");
    });
    expect(screen.queryByText("Loading map…")).not.toBeInTheDocument();
  });

  it("shows error overlay when the map fires an error event", async () => {
    renderMap();
    await act(async () => {
      MockMap.lastInstance?.emit("error", { error: { message: "Tiles failed to load" } });
    });
    expect(screen.getByText(/map could not be loaded/i)).toBeInTheDocument();
  });

  it("uses the generic fallback message when error event has no message", async () => {
    renderMap();
    await act(async () => {
      MockMap.lastInstance?.emit("error", {});
    });
    expect(screen.getByText(/map could not be loaded/i)).toBeInTheDocument();
  });

  it("does not show error overlay before any error fires", () => {
    renderMap();
    expect(screen.queryByText(/map could not be loaded/i)).not.toBeInTheDocument();
  });

  it("initialises the map only once even if re-rendered", async () => {
    const { rerender } = renderMap({ deliveryLat: 41.0, deliveryLon: 29.0 });
    const firstInstance = MockMap.lastInstance;

    rerender(<TrackingMap deliveryLat={42.0} deliveryLon={30.0} />);

    expect(MockMap.lastInstance).toBe(firstInstance);
  });

  it("cleans up the map instance when the component unmounts", async () => {
    const { unmount } = renderMap();
    await act(async () => {
      MockMap.lastInstance?.emit("load");
    });

    const mapInstance = MockMap.lastInstance!;
    unmount();

    expect(mapInstance.remove).toHaveBeenCalled();
  });

  it("adds a NavigationControl to the map on init", () => {
    renderMap();
    expect(MockMap.lastInstance?.addControl).toHaveBeenCalled();
  });

  it("adds a delivery marker when map is ready", async () => {
    renderMap({ deliveryLat: 41.5, deliveryLon: 29.5 });
    await act(async () => {
      MockMap.lastInstance?.emit("load");
    });

    const deliveryMarkerCall = MockMarker.calls.find(
      ([opts]) => (opts as Record<string, unknown>)?.color === "#0b5fff",
    );
    expect(deliveryMarkerCall).toBeDefined();
  });

  it("adds a courier marker when courier coordinates are present", async () => {
    renderMap({ courierLat: 41.3, courierLon: 29.3, deliveryLat: 41.0, deliveryLon: 29.0 });
    await act(async () => {
      MockMap.lastInstance?.emit("load");
    });

    const courierMarkerCall = MockMarker.calls.find(
      ([opts]) => (opts as Record<string, unknown>)?.color === "#f97316",
    );
    expect(courierMarkerCall).toBeDefined();
  });

  it("loading overlay has aria-live='polite'", () => {
    renderMap();
    const overlay = screen.getByText("Loading map…").closest("[aria-live]");
    expect(overlay?.getAttribute("aria-live")).toBe("polite");
  });

  it("error overlay has aria-live='assertive'", async () => {
    renderMap();
    await act(async () => {
      MockMap.lastInstance?.emit("error", {});
    });
    const errEl = screen.getByText(/map could not be loaded/i).closest("[aria-live]");
    expect(errEl?.getAttribute("aria-live")).toBe("assertive");
  });
});
