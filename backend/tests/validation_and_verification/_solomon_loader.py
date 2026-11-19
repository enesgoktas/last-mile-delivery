"""Solomon VRPTW instance parser → VRPInput.

The Solomon (1987) benchmark is the de-facto standard test suite for
Vehicle Routing with Time Windows. The 56 100-customer instances
(R1, R2, C1, C2, RC1, RC2 families) are public-domain and available
from multiple academic sources; see scripts/download_solomon.py.

Format reference:
    <INSTANCE_NAME>

    VEHICLE
    NUMBER     CAPACITY
      <K>        <Q>

    CUSTOMER
    CUST NO.  XCOORD.   YCOORD.    DEMAND   READY TIME   DUE DATE   SERVICE TIME
        0     <x>       <y>          0         0           <H>          0
        1     <x>       <y>         <d>       <r>          <l>         <s>
        ...

Customer 0 is the depot. Vehicles are identical: K vehicles of capacity Q.
By Solomon's convention travel time = Euclidean distance (speed = 1).

For our internal VRPInput we map:
  - location k → 1 (depot, then customers)
  - distance_m[i][j] = round(euclidean(i, j) * 1000)   (1 grid unit = 1 km)
  - duration_s[i][j] = round(euclidean(i, j))           (Solomon speed=1)
  - time_window for customer k → (READY_TIME, DUE_DATE)
  - service_time for customer k → SERVICE_TIME (seconds, integer)
  - K vehicles all sharing the depot, capacity Q grams (Solomon "demand" = grams)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from app.algorithms.vrp_solver import OrderData, VehicleData, VRPInput
from app.models.enums import OrderPriority, VehicleType


@dataclass(frozen=True, slots=True)
class SolomonInstance:
    """Parsed Solomon instance — independent of our internal VRPInput."""
    name: str
    n_vehicles: int
    capacity: int
    depot: tuple[float, float]
    customers: list["SolomonCustomer"]


@dataclass(frozen=True, slots=True)
class SolomonCustomer:
    cust_no: int
    x: float
    y: float
    demand: int
    ready_time: int
    due_date: int
    service_time: int


def parse_solomon_text(text: str) -> SolomonInstance:
    """Parse a Solomon-format string → SolomonInstance.

    Robust to extra blank lines and varying whitespace; uses keyword
    anchors (VEHICLE, CUSTOMER) rather than fixed line numbers.
    """
    lines = [line.rstrip() for line in text.splitlines()]

    # Instance name = first non-empty line
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        raise ValueError("Solomon file is empty")
    name = lines[idx].strip()
    idx += 1

    # Find VEHICLE block
    while idx < len(lines) and "VEHICLE" not in lines[idx].upper():
        idx += 1
    if idx >= len(lines):
        raise ValueError("Missing VEHICLE block in Solomon file")
    idx += 1  # skip VEHICLE line
    # Skip the "NUMBER     CAPACITY" header
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        raise ValueError("Missing vehicle header in Solomon file")
    idx += 1  # skip NUMBER CAPACITY header
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    parts = lines[idx].split()
    if len(parts) < 2:
        raise ValueError(f"Cannot parse vehicle line: {lines[idx]!r}")
    n_vehicles = int(parts[0])
    capacity = int(parts[1])
    idx += 1

    # Find CUSTOMER block
    while idx < len(lines) and "CUSTOMER" not in lines[idx].upper():
        idx += 1
    if idx >= len(lines):
        raise ValueError("Missing CUSTOMER block in Solomon file")
    idx += 1  # skip CUSTOMER line
    # Skip header line "CUST NO.  XCOORD ..."
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        raise ValueError("Missing customer header line")
    idx += 1  # skip customer column header

    customers: list[SolomonCustomer] = []
    for line in lines[idx:]:
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 7:
            continue
        try:
            customers.append(SolomonCustomer(
                cust_no=int(parts[0]),
                x=float(parts[1]),
                y=float(parts[2]),
                demand=int(parts[3]),
                ready_time=int(parts[4]),
                due_date=int(parts[5]),
                service_time=int(parts[6]),
            ))
        except ValueError:
            # Skip malformed lines silently — happens with trailing footers etc.
            continue

    if not customers:
        raise ValueError("Solomon file has no customer rows")
    if customers[0].cust_no != 0:
        raise ValueError(f"First customer must be depot (cust_no=0), got {customers[0].cust_no}")
    if customers[0].demand != 0:
        raise ValueError(f"Depot demand must be 0, got {customers[0].demand}")

    return SolomonInstance(
        name=name,
        n_vehicles=n_vehicles,
        capacity=capacity,
        depot=(customers[0].x, customers[0].y),
        customers=customers[1:],  # exclude depot from customer list
    )


def load_solomon_file(path: Path) -> SolomonInstance:
    return parse_solomon_text(Path(path).read_text())


def to_vrp_input(instance: SolomonInstance) -> VRPInput:
    """Convert a SolomonInstance to our internal VRPInput.

    Conventions:
      * 1 Solomon grid unit = 1 km → distance_m = round(euclidean * 1000)
      * Travel time = distance, but in seconds → duration_s = round(euclidean)
        (Solomon's speed=1 with same units for distance and time)
      * Service time stays in the Solomon unit (seconds)
      * One depot at index 0; customers at indices 1..N
      * Each customer k → one delivery order with depot as pickup
      * Vehicles: K identical vehicles of capacity Q grams

    All economic costs (distance/labor/fuel) are zero-ed out so total cost
    equals total distance — that's what we compare to Solomon's published
    BKS, which is a pure distance objective.
    """
    depot = instance.depot
    locations: list[tuple[float, float]] = [depot]
    locations.extend((c.x, c.y) for c in instance.customers)

    n_locs = len(locations)
    dist_m = [[0] * n_locs for _ in range(n_locs)]
    dur_s = [[0] * n_locs for _ in range(n_locs)]
    for i in range(n_locs):
        xi, yi = locations[i]
        for j in range(n_locs):
            if i == j:
                continue
            xj, yj = locations[j]
            d = math.hypot(xi - xj, yi - yj)
            dist_m[i][j] = int(round(d * 1000))
            dur_s[i][j] = int(round(d))

    horizon = max(c.due_date for c in instance.customers)

    # Solomon's BKS uses a hierarchical objective: minimise vehicles, then
    # distance. Our rich_alns is configured for last-mile dispatch, where
    # the natural objective is "use the fleet you have" — there is no
    # built-in "min vehicles" tier. We approximate Solomon's preference
    # with a fixed_route_cost per active vehicle plus distance_cost_per_km=1.
    # In practice this only partially consolidates routes; the resulting
    # gap-to-BKS is itself a V&V finding and is reported in solomon_results.csv.
    # gap-to-BKS is computed from total_distance_m so fixed_route_cost is
    # purely a search-shaping device, not a metric component.
    vehicles = [
        VehicleData(
            courier_id=f"veh-{v:02d}",
            vehicle_type=VehicleType.CAR_VAN,
            physical_courier_id=f"veh-{v:02d}",
            depot_index=0,
            start_time_s=0,
            end_time_s=horizon,
            hard_end_time_s=horizon,
            capacity_grams=instance.capacity,
            capacity_volume_cm3=0,
            max_packages=0,
            fixed_route_cost=500,
            distance_cost_per_km=1,
            labor_cost_per_minute=0,
            fuel_cost_per_km=0,
            overtime_penalty_per_minute=0,
            overtime_cost_per_minute=0,
            trip_index=0,
        )
        for v in range(instance.n_vehicles)
    ]

    orders = [
        OrderData(
            order_id=f"ord-{c.cust_no:03d}",
            pickup_index=0,
            delivery_index=k + 1,
            pickup_is_depot=True,
            weight_grams=c.demand,
            volume_cm3=0,
            package_count=1,
            earliest_time_s=c.ready_time,
            latest_time_s=c.due_date,
            hard_latest_time_s=c.due_date,
            service_time_s=c.service_time,
            priority=OrderPriority.NORMAL,
            unserved_penalty=10_000,
            lateness_penalty_per_minute=100,
            priority_penalty_multiplier=1.0,
        )
        for k, c in enumerate(instance.customers)
    ]

    return VRPInput(
        orders=orders,
        vehicles=vehicles,
        locations=locations,
        duration_matrix=dur_s,
        distance_matrix=dist_m,
    )


def list_vendored_instances(data_dir: Path) -> list[Path]:
    """Return sorted list of `.txt` files in the Solomon data directory."""
    if not data_dir.is_dir():
        return []
    return sorted(p for p in data_dir.iterdir() if p.is_file() and p.suffix.lower() == ".txt")
