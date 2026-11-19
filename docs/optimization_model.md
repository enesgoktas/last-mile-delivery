# Optimisation model — formal specification

**Last updated:** 2026-05-07

This document is the **formal mathematical specification** of the
last-mile dispatch optimisation problem solved by `rich_alns` (and, for
benchmarking, `ortools_cvrptw`). It is the spec everything else is
"verified against" in the V&V suite.

For the plain-English overview see
[routing_and_assignment_fundamentals.md](routing_and_assignment_fundamentals.md).
For how each element below is exercised by tests, see
[verification_and_validation.md](verification_and_validation.md).

The problem class is **Multi-Trip Pickup-and-Delivery VRP with Time
Windows, heterogeneous fleet, and side constraints (PDPTW-MT)**. It
extends the classical Solomon (1987) VRPTW in eight directions, listed
in §10.

---

## 1. Sets and indices

| Symbol | Meaning |
| --- | --- |
| $N$ | set of nodes (depot + pickup locations + delivery locations) |
| $0 \in N$ | the depot |
| $R$ | set of pickup–delivery requests (orders) |
| $r \in R$ | a single request, with pickup node $p_r$ and delivery node $d_r$ |
| $V$ | set of available vehicles (couriers, possibly multi-trip-virtualised) |
| $v \in V$ | a single vehicle |
| $T_v$ | set of trips for vehicle $v$ (single-trip vehicles have $|T_v| = 1$) |
| $K = \{$weight, volume, package_count$\}$ | capacity dimensions |
| $\Pi = \{$URGENT, HIGH, NORMAL, LOW$\}$ | order priority classes |

## 2. Parameters (input data — `VRPInput`)

### Network
- $\tau_{ij}$ — travel duration from node $i$ to node $j$ (seconds)
- $\delta_{ij}$ — travel distance from node $i$ to node $j$ (metres)

### Requests
For each $r \in R$:
- $p_r, d_r \in N$ — pickup and delivery node indices
- $[\underline{e}^p_r, \overline{e}^p_r]$ — pickup time window (seconds from midnight)
- $[\underline{e}^d_r, \overline{e}^d_r]$ — soft delivery time window
- $\overline{e}^{d,\text{hard}}_r$ — optional hard delivery deadline ($\geq \overline{e}^d_r$)
- $s^p_r, s^d_r$ — service time at pickup, delivery (seconds)
- $q^k_r$ for $k \in K$ — load per capacity dimension
- $\pi_r \in \Pi$ — priority class
- $u_r$ — unserved penalty (TL)
- $\lambda_r$ — lateness penalty per minute past $\overline{e}^d_r$ (TL/min)
- $\mu_r$ — priority penalty multiplier
- $\Phi_r \subseteq \text{Tags}$ — required vehicle tags
- $V_r \subseteq V$ — allowed vehicle subset (or unrestricted)
- $\text{frag}_r, \text{temp}_r \in \{0, 1\}$ — fragility / temperature-sensitivity

### Vehicles
For each $v \in V$:
- $b_v \in N$ — depot node (start = end)
- $[\sigma_v, \overline{\sigma}_v]$ — soft shift window (seconds from midnight)
- $\overline{\sigma}^{\text{hard}}_v$ — hard shift end (optional)
- $C^k_v$ for $k \in K$ — capacity per dimension (0 = unconstrained)
- $D_v$ — max distance per route (0 = unconstrained)
- $S_v$ — max stops per route (0 = unconstrained)
- $\Theta_v \subseteq \text{Tags}$ — vehicle tags
- $\beta_v \in \{0, 1\}$ — break required this shift
- $[\underline{\beta}_v, \overline{\beta}_v], \delta^\beta_v$ — break window and duration
- Cost coefficients (TL):
  - $c^{\text{fix}}_v$ — fixed cost if vehicle is used at all
  - $c^{\text{dist}}_v$ — per-km
  - $c^{\text{labor}}_v$ — per-minute of total shift duration
  - $c^{\text{fuel}}_v$ — per-km
  - $c^{\text{ot}}_v$ — per-minute of overtime past $\overline{\sigma}_v$
- $\eta_v$ — fuel efficiency (km/L), or $\bot$ if unspecified
- $\gamma_v$ — CO₂ emissions (g/km), or $\bot$

## 3. Decision variables

- $x_{r,v,t} \in \{0, 1\}$ — request $r$ assigned to trip $t$ of vehicle $v$
- $y_r \in \{0, 1\}$ — request $r$ left unassigned
- $\sigma_{r,v,t} \in \mathbb{N}$ — order index of $r$ within trip $(v, t)$
- $a^p_r, a^d_r \in \mathbb{R}_{\geq 0}$ — actual arrival times at pickup, delivery (seconds)

Implicit: route ends and starts use $a^d_{\text{depot},\,v,t}$ for shift accounting.

## 4. Objective — lexicographic ordering

The solver minimises the 4-tuple

$$
\mathbf{Z} = \bigl(Z_1,\ Z_2,\ Z_3,\ Z_4\bigr)
$$

in **lexicographic order**: $Z_1$ first, with $Z_2$ as a tiebreaker, etc.

### 4.1 $Z_1$ — Weighted unserved count
$$
Z_1 = \sum_{r \in R} y_r \cdot w(\pi_r)
$$
with priority weights
$$
w(\text{URGENT}) = 10\,000,\ w(\text{HIGH}) = 100,\ w(\text{NORMAL}) = 10,\ w(\text{LOW}) = 1.
$$

### 4.2 $Z_2$ — Lateness + overtime (minutes)
$$
Z_2 = \sum_{r \in R} \mathrm{late}_r + \sum_{v \in V} \mathrm{ot}_v
$$
where $\mathrm{late}_r = \lceil \max(0, a^d_r - \overline{e}^d_r) / 60 \rceil$
(minutes past the soft delivery window) and
$\mathrm{ot}_v = \lceil \max(0, t^{\text{end}}_v - \overline{\sigma}_v) / 60 \rceil$.

### 4.3 $Z_3$ — Generalised economic cost
$$
Z_3 = \sum_{v \in V} \Bigl(c^{\text{fix}}_v \mathbb{1}[v\text{ active}]
   + c^{\text{dist}}_v \cdot \tfrac{D^{\text{tot}}_v}{1000}
   + c^{\text{fuel}}_v \cdot \tfrac{D^{\text{tot}}_v}{1000}
   + c^{\text{labor}}_v \cdot \tfrac{T^{\text{shift}}_v}{60}
   + c^{\text{ot}}_v \cdot \mathrm{ot}_v\Bigr)
$$
$$
\quad + \sum_{r \in R} \mathrm{late}_r \cdot \lambda_r \cdot \mu_r
   + \mathbb{1}[\text{spread\_fleet}] \cdot \rho \cdot \bigl(\max_v n_v - \min_{v \text{ active}} n_v\bigr)
$$
where
- $D^{\text{tot}}_v$ — total distance driven by vehicle $v$ (m)
- $T^{\text{shift}}_v$ — duration from shift start to last route end (s)
- $n_v$ — delivery count on vehicle $v$
- $\rho = 1\,000$ TL/imbalance-unit — fleet-spread coefficient (see [VRP_REWRITE_PLAN.md](../VRP_REWRITE_PLAN.md)).

### 4.4 $Z_4$ — Stability distance
Reserved (currently 0). In future used for solution-similarity to a previous plan in tactical replanning.

## 5. Hard constraints

(C1) **Coverage.** Every request is either assigned exactly once or unassigned:
$$
\sum_{v,t} x_{r,v,t} + y_r = 1 \quad \forall r \in R
$$

(C2) **URGENT served.** $y_r = 0$ if $\pi_r = \text{URGENT}$.

(C3) **Pickup before delivery.** Within each trip $(v, t)$: $\sigma_{p_r,v,t} < \sigma_{d_r,v,t}$ if $r$ is on this trip; $a^p_r + s^p_r + \tau_{p_r d_r} \leq a^d_r$.

(C4) **Pickup-and-delivery on same vehicle.** Both endpoints of $r$ share the same $(v, t)$ if assigned.

(C5) **Multi-dimensional capacity.** At every point in trip $(v, t)$, the cumulative load satisfies $\sum_{r \text{ on board}} q^k_r \leq C^k_v$ for each $k \in K$.

(C6) **Time windows.** $\underline{e}^p_r \leq a^p_r \leq \overline{e}^p_r$ and $\underline{e}^d_r \leq a^d_r$, with $a^d_r \leq \overline{e}^{d,\text{hard}}_r$ (hard) or $\overline{e}^d_r$ if no hard deadline. Soft window $\overline{e}^d_r$ violations are *allowed* but penalised in $Z_2$ + $Z_3$.

(C7) **Hard shift end.** $t^{\text{end}}_v \leq \overline{\sigma}^{\text{hard}}_v$ if specified.

(C8) **Max distance.** $D^{\text{tot}}_v \leq D_v$ (when $D_v > 0$).

(C9) **Max stops.** $|\{r : x_{r,v,t} = 1\}| \leq S_v$ per trip (when $S_v > 0$).

(C10) **Compatibility.** $\Phi_r \subseteq \Theta_v$ must hold; fragile and temperature-sensitive flags must match vehicle capabilities.

(C11) **Allowed vehicles.** $x_{r,v,t} = 0$ if $V_r \neq \emptyset$ and $v \notin V_r$.

(C12) **Mandatory break.** If $\beta_v = 1$, trip $(v, t)$ schedules a break of duration $\delta^\beta_v$ that starts within $[\underline{\beta}_v, \overline{\beta}_v]$.

## 6. Reported metrics (not part of objective)

For evaluation only — written into `EconomicCostBreakdown`:
- $L_v = D^{\text{tot}}_v / (1000 \cdot \eta_v)$ if $\eta_v \neq \bot$, else $\bot$ (litres of fuel)
- $E_v = D^{\text{tot}}_v / 1000 \cdot \gamma_v$ if $\gamma_v \neq \bot$, else $\bot$ (grams of CO₂)

These quantities are computed but do not feed back into $Z_3$.

---

## 7. Modelling assumptions (the spec's load-bearing fictions)

These are the simplifications the model makes about reality. Each is
defensible but limits where the model is valid; the V&V chapter cites
this list when discussing scope and limitations.

| # | Assumption | Why we make it | When it breaks |
| --- | --- | --- | --- |
| MA1 | **Deterministic travel times.** $\tau_{ij}$ is a fixed scalar, not a distribution. | OSRM returns one duration; uncertainty handling would require stochastic VRP techniques outside scope. | Severe traffic disruptions; weather events. |
| MA2 | **No mid-route capacity transfers.** A vehicle cannot hand off cargo to another vehicle. | Drastically reduces problem size; matches the operational reality of small vans and motorbikes. | Cross-docking or hub-and-spoke operations. |
| MA3 | **Single depot per vehicle by default.** $b_v$ is the same node at start and end. The vehicle may visit additional depot nodes mid-route for reloads, and may end at a node different from start, when the tenant has `features.multi_depot_vehicles_enabled` and the courier carries an `additional_depots` set / `end_depot_id` (Phases 1–5, 2026-05). | Matches the default warehouse setup. The opt-in extension covers couriers that serve multiple warehouses or do one-way repositioning shifts. | Cross-tenant multi-depot vehicles, or solver-chosen depots beyond the explicit allowed set. |
| MA4 | **Pickup at depot is the common case.** Most orders use `pickup_is_depot=True`; non-depot pickups are supported but tightly coupled to the depot via paired-PD constraints. | Reflects the "warehouse → customer" delivery model. | True peer-to-peer pickup/delivery (uber-style). |
| MA5 | **Static order set per solve.** Once `solve()` is called, $R$ is fixed; new orders trigger a re-solve (`solve_reactive` for small changes). | Avoids streaming/online VRP machinery. | High-velocity flash-sale arrivals. |
| MA6 | **Symmetric, triangle-inequality-respecting matrix.** $\tau_{ij} = \tau_{ji}$ and $\tau_{ij} \leq \tau_{ik} + \tau_{kj}$ are assumed even though OSRM may produce small asymmetries. | Lets us use Cython delta-eval shortcuts that exploit triangle inequality. | One-way streets at scale (we accept the small error). |
| MA7 | **Service time is point-determined, not vehicle-determined.** $s^d_r$ depends on the order, not which vehicle delivers it. | Matches reality (unloading time depends on parcel, not van). | Specialised loading equipment per vehicle type. |
| MA8 | **Lexicographic priorities are strictly ordered.** No trade-off between $Z_1$ and $Z_3$: a single more-served customer beats *any* economic improvement. | Reflects business policy ("serve first, save money second"). | Pathological tiny-improvement-vs-big-cost scenarios. |
| MA9 | **Costs are linear and additive.** No volume discounts, no economies of scale, no tiered fuel pricing. | Standard VRP modelling assumption; non-linear costs would require non-linear solvers. | Tiered carrier contracts. |
| MA10 | **Vehicles are independent.** No coordinated convoying, no platoon discounts. | Standard. | Trucks-with-trailers operations. |
| MA11 | **Fleet utilisation modes binary.** `FULL_FLEET` adds spread penalty; `EFFICIENT` does not. There is no in-between. | Operational simplicity. | Soft "prefer to spread but accept consolidation" semantics. |
| MA12 | **Best-known solutions are stable benchmarks.** Solomon's BKS values from Vidal et al. 2013 are treated as comparison anchors. | They're standard. | Future research (Helsgaun 2017+, hybrids) tightens BKS by ≤ 0.5%; doesn't change the order of magnitude. |

## 8. Out-of-model concerns (deliberately not in the spec)

- **Driver fatigue / regulatory rest beyond a single break.** Only one break per shift is modelled (C12).
- **Real-time GPS tracking** — handled by tracking_service, not part of the optimisation.
- **Customer no-show / cancellation mid-route** — handled reactively (`solve_reactive`), not in the static model.
- **Pricing and revenue** — orders are non-negotiable inputs.
- **Carrier/contract selection** — assumes a single fleet under direct control.

## 9. Mapping to code

| Spec element | Implementation |
| --- | --- |
| Sets, parameters | [`backend/app/algorithms/vrp_solver.py`](../backend/app/algorithms/vrp_solver.py) — `OrderData`, `VehicleData`, `VRPInput` |
| Decision variables (internal) | [`backend/app/algorithms/rich_vrp/domain/models.py`](../backend/app/algorithms/rich_vrp/domain/models.py) — `Visit`, `Route`, `Solution` |
| Objective $Z_1\!\!-\!\!Z_4$ | [`backend/app/algorithms/rich_vrp/objective/lexicographic.py`](../backend/app/algorithms/rich_vrp/objective/lexicographic.py) — `objective_for_solution` |
| Cost coefficients (4.3) | [`backend/app/algorithms/rich_vrp/objective/cost.py`](../backend/app/algorithms/rich_vrp/objective/cost.py) — `compute_route_cost` |
| Hard constraints C1–C12 | [`backend/app/algorithms/rich_vrp/feasibility/kernel.py`](../backend/app/algorithms/rich_vrp/feasibility/kernel.py) + post-solve in [`solution_validator.py`](../backend/app/algorithms/solution_validator.py) |
| Lexicographic comparison | `LexicographicObjective.__lt__` in objective module |
| Reported metrics (§6) | `EconomicCostBreakdown` in `vrp_solver.py` |

## 10. Why this is "PDPTW-MT" and not classical VRPTW

The classical Solomon VRPTW has only the depot–customer arcs and a
single-tier "minimise distance" objective. Our model extends it in
eight directions; each extension is also why a direct comparison to
Solomon's BKS overstates the gap.

| # | Extension | Solomon (1987) | This model |
| --- | --- | --- | --- |
| 1 | Pickup-and-delivery pairing | every customer is its own pickup at the depot | each request has distinct pickup and delivery nodes (C3) |
| 2 | Multi-trip per vehicle | one route per vehicle | a vehicle can do multiple trips (set $T_v$) with reload at depot |
| 3 | Heterogeneous fleet | identical vehicles | per-vehicle capacities, costs, fuel, tags |
| 4 | Multi-dimensional capacity | weight only | weight + volume + package count (C5, $K$) |
| 5 | Compatibility & tags | none | required tags, fragility, temperature-sensitivity (C10) |
| 6 | Soft-then-hard time windows | hard windows only | soft window with lateness penalty + hard deadline (C6, $Z_2$, $Z_3$) |
| 7 | Lexicographic objective | single distance objective | 4-tier objective $\mathbf{Z}$ (§4) |
| 8 | Fleet utilisation policy | uniform | `FULL_FLEET` vs `EFFICIENT` switch (C11/MA11) |

## 11. Verification status

For the test that exercises each constraint and assumption, see
[verification_and_validation.md](verification_and_validation.md). Current
gating: 46 V&V tests in
`backend/tests/validation_and_verification/` plus the production
`solution_validator` running on every dispatch.

When this document changes, refresh the **Last updated** date at the top
and update the V&V README activity matrix accordingly.
