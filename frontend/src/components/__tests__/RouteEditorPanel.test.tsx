import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouteEditorPanel } from "../RouteEditorPanel";
import type { Order, Route } from "../../types";

vi.mock("../StatusBadge", () => ({
  StatusBadge: ({ value }: { value: string }) => <span>{value}</span>,
}));

function buildRoute(): Route {
  return {
    id: "route-1",
    courier_id: "courier-1",
    status: "ACTIVE",
    planned_distance_km: 12.5,
    actual_distance_km: null,
    optimization_run_id: null,
    created_at: "2026-03-01T08:00:00Z",
    started_at: "2026-03-01T08:10:00Z",
    completed_at: null,
    stops: [
      {
        id: "stop-1",
        order_id: "order-1",
        sequence_number: 0,
        stop_type: "PICKUP",
        lat: 0,
        lon: 0,
        address: "123 Pickup St",
        planned_arrival: "2026-03-01T09:00:00Z",
        actual_arrival: null,
        status: "PENDING",
        failure_reason: null,
        failure_reason_detail: null,
        notes: null,
        dispatcher_note: "Call before arrival",
        customer_phone: null,
        action_policy: null,
      },
      {
        id: "stop-2",
        order_id: "order-1",
        sequence_number: 1,
        stop_type: "DELIVERY",
        lat: 0,
        lon: 0,
        address: "789 Delivery Ave",
        planned_arrival: "2026-03-01T09:30:00Z",
        actual_arrival: null,
        status: "PENDING",
        failure_reason: null,
        failure_reason_detail: null,
        notes: null,
        dispatcher_note: null,
        customer_phone: null,
        action_policy: null,
      },
    ],
  };
}

function buildPendingOrder(overrides: Partial<Order> = {}): Order {
  return {
    id: "pending-order-1",
    status: "PENDING",
    type: "ON_DEMAND",
    priority: "URGENT",
    pickup_address: "Pickup",
    delivery_address: "12 New Stop St",
    customer_email: "ops@example.com",
    earliest_delivery: "2026-03-01T10:00:00Z",
    latest_delivery: "2026-03-01T11:00:00Z",
    attempt_count: 0,
    max_attempts: 3,
    created_at: "2026-03-01T08:00:00Z",
    ...overrides,
  };
}

describe("RouteEditorPanel", () => {
  it("saves dispatcher notes for the selected stop", async () => {
    const onSetDispatcherNote = vi.fn().mockResolvedValue(undefined);

    render(
      <RouteEditorPanel
        route={buildRoute()}
        pendingOrders={[]}
        onReorder={vi.fn().mockResolvedValue(undefined)}
        onSetDispatcherNote={onSetDispatcherNote}
        onAssignPendingOrder={vi.fn().mockResolvedValue(undefined)}
        onSetPreferredCourier={vi.fn().mockResolvedValue(undefined)}
        onClose={vi.fn()}
      />,
    );

    const noteField = screen.getByPlaceholderText(/dispatcher note/i);
    fireEvent.change(noteField, { target: { value: "Gate code is 4422" } });
    fireEvent.click(screen.getByRole("button", { name: /save note/i }));

    await waitFor(() =>
      expect(onSetDispatcherNote).toHaveBeenCalledWith("route-1", "stop-1", "Gate code is 4422"),
    );
  });

  it("adds a pending order onto the route courier", async () => {
    const onAssignPendingOrder = vi.fn().mockResolvedValue(undefined);

    render(
      <RouteEditorPanel
        route={buildRoute()}
        pendingOrders={[buildPendingOrder()]}
        onReorder={vi.fn().mockResolvedValue(undefined)}
        onSetDispatcherNote={vi.fn().mockResolvedValue(undefined)}
        onAssignPendingOrder={onAssignPendingOrder}
        onSetPreferredCourier={vi.fn().mockResolvedValue(undefined)}
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));

    await waitFor(() =>
      expect(onAssignPendingOrder).toHaveBeenCalledWith("pending-order-1", "courier-1"),
    );
  });

  it("pins a pending order to the selected route courier", async () => {
    const onSetPreferredCourier = vi.fn().mockResolvedValue(undefined);

    render(
      <RouteEditorPanel
        route={buildRoute()}
        pendingOrders={[buildPendingOrder()]}
        onReorder={vi.fn().mockResolvedValue(undefined)}
        onSetDispatcherNote={vi.fn().mockResolvedValue(undefined)}
        onAssignPendingOrder={vi.fn().mockResolvedValue(undefined)}
        onSetPreferredCourier={onSetPreferredCourier}
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^pin$/i }));

    await waitFor(() =>
      expect(onSetPreferredCourier).toHaveBeenCalledWith("pending-order-1", "courier-1"),
    );
  });
});
