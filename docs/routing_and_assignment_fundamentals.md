# Routing and Assignment Fundamentals

This is the plain-English source of truth for how routing, chaining, courier assignment, weights, and success metrics work in this codebase.

The most important distinction is this:

- **Route finding** means finding a road path and travel time between two points.
- **Route planning** means deciding which orders go to which couriers, and in what stop order.

Those are different problems, and the system uses different algorithms for them.

## One-Page Answer

| Question | Direct answer |
| --- | --- |
| What algorithm finds the road route between two locations? | OSRM, short for Open Source Routing Machine. It is a self-hosted road-routing engine. Our containers run OSRM with `--algorithm mld`, meaning Multi-Level Dijkstra. We do not implement Dijkstra or A* ourselves in the application code. |
| What does "OR-Tools" mean? | Google Optimization Tools, also known as Google's Operations Research Tools. Operations research means math-based optimization for choosing the best plan under constraints. In plain language: it is a solver library, not an AI model and not a routing SaaS. |
| What algorithm creates scheduled or bulk routes? | The Google OR-Tools vehicle-routing solver, called `RoutingModel` in code. We feed it orders, couriers, capacities, time windows, and OSRM travel times, and it searches for a good feasible plan. |
| What algorithm chains stops together? | In batch mode, the OR-Tools vehicle-routing solver decides the stop sequence while enforcing pickup-before-delivery, time windows, capacity, breaks, max distance, max stops, and vehicle eligibility. Afterward, a route-local 2-opt pass can reverse stop subsequences if that shortens a route without breaking constraints. |
| What algorithm assigns orders to couriers in batch mode? | The same OR-Tools vehicle-routing model assigns orders to courier vehicles and sequences their stops together. It is not a separate "nearest courier" pass. |
| What algorithm assigns one urgent on-demand order? | A deterministic cheapest-insertion heuristic. It tries every feasible pickup and delivery insertion position in eligible couriers' active routes, also considers starting a new route, then chooses the lowest weighted detour. |
| What happens if OR-Tools cannot find a batch solution? | A greedy nearest-feasible fallback runs. It sorts vehicles by capacity and repeatedly appends the nearest order that still fits the route constraints. |
| How are large batches handled? | The system first uses grid-based geographic clustering, solves each cluster separately, then merges the cluster solutions. This is a scalability heuristic, not k-means. |
| How are multi-trip courier days handled? | One physical courier can be expanded into trip-specific virtual vehicles, then stitched back into one courier route with depot return and reload stops between trips. |
| Are OSRM and OR-Tools the "core" of our product? | No. They are open-source engines we use. OSRM gives road travel times and map geometry. OR-Tools searches through assignment/sequence options. Our product-specific value is the dispatch workflow, policies, constraints, cost model, eligibility rules, route approval, mobile execution flow, tracking, analytics, and integrations around those engines. |

The short demo answer is:

> Road paths come from self-hosted OSRM, the Open Source Routing Machine, using Multi-Level Dijkstra. Scheduled and bulk dispatch use Google's OR-Tools vehicle-routing solver to choose couriers and stop order under real constraints. On-demand dispatch uses cheapest insertion. If the main solver fails, the fallback is greedy nearest-feasible insertion.

## Route Finding vs Route Planning

### Route finding: road path and travel time

Road-network routing lives in `backend/app/algorithms/osrm_client.py`.

OSRM means **Open Source Routing Machine**. It is the map-road engine. Given coordinates, it answers questions like:

- "What road path connects these two points?"
- "How long does that road path take?"
- "What are the travel times between every pair of these 120 locations?"
- "Which road segment is closest to this GPS coordinate?"

That is the same category of problem as "give me driving directions." It is not the same category as "which courier should get which orders?"

The application asks OSRM for:

- a route from point A to point B
- GeoJSON geometry for drawing route lines on maps
- a full distance and duration table for many points
- nearest-road snapping

OSRM is prepared from Istanbul OpenStreetMap data and served in two profiles:

- `osrm-car`, used for cars, vans, and motorcycles
- `osrm-truck`, used for trucks and heavy vehicles

The OSRM containers run the `ghcr.io/project-osrm/osrm-backend:v5.27.1` Docker image. Both containers start `osrm-routed` with `--algorithm mld`, which means Multi-Level Dijkstra. In practical terms, OSRM pre-processes the road graph so route and table queries are fast. Our application code does not manually run Dijkstra, A*, Bellman-Ford, or a custom shortest-path algorithm.

The prepared data is built by `backend/scripts/prepare_osrm_data.sh`:

- download an Istanbul OpenStreetMap extract from BBBike
- build the car graph with OSRM's bundled `car.lua` profile
- build the truck graph with our custom `backend/osrm_profiles/truck.lua` profile
- run OSRM's extract, partition, and customize steps so `osrm-routed` can answer fast route and matrix requests

If OSRM is unavailable, planning fails loudly. Current planning matrices and route geometry do **not** fall back to straight-line or haversine routing.

OSRM is not:

- our courier assignment algorithm
- our dispatch policy engine
- a hosted third-party API call to Google Maps, Mapbox, or OSRM's public demo server
- a reason our own application code has to be open sourced

### Route planning: which courier and which stop order

Route planning lives mainly in:

- `backend/app/services/dispatch_service.py`
- `backend/app/algorithms/vrp_solver.py`
- `backend/app/algorithms/insertion_heuristic.py`

This is the harder operational question: "given these orders, couriers, working hours, vehicle capacities, and delivery windows, what should each courier do?"

That is not a shortest-path problem. A shortest-path algorithm answers "how do I get from A to B?" It does not answer "which courier should handle these 80 pickups and deliveries while respecting capacity, time windows, breaks, and priorities?"

For that planning problem, the system uses vehicle-routing algorithms and heuristics.

### What OR-Tools means in this app

OR-Tools is an open-source Google optimization library. The name is confusing because different places expand it slightly differently:

- Google calls the project **Google Optimization Tools** in its GitHub README.
- The reference docs call it **Google Operations Research Tools**.
- In delivery-planning conversations, the useful translation is: "a library that searches for a good plan when there are many choices and many constraints."

Operations research is the field behind this kind of planning. It is not "operations" as in DevOps, and it is not a generic "tool." It is math and algorithms for decisions like:

- which courier should take each order
- what sequence those stops should be in
- how to respect capacity, time windows, shifts, and vehicle rules
- which imperfect tradeoff is least bad when everything cannot be perfect

In our code, we specifically use OR-Tools' vehicle-routing solver. We do not use every solver in OR-Tools, and we do not let OR-Tools know anything about our product by itself. We translate our business rules into a model that OR-Tools can solve.

## How Orders Are Chained

### Batch mode: OR-Tools vehicle routing

Scheduled, bulk, pickup-and-delivery, return, and date-scoped planning runs go through the batch dispatch flow.

The core model is:

- Google OR-Tools vehicle-routing solver, named `RoutingModel` in code
- pickup-and-delivery routing
- vehicle capacity
- time windows
- courier working hours

The common name for this class of problem is a **capacitated vehicle routing problem with time windows**. If someone uses the acronym `CVRPTW`, this is what they mean:

- **capacitated**: vehicles have maximum weight, volume, package-count, stop-count, or distance limits
- **vehicle routing**: the solver assigns stops to vehicles and orders stops on each route
- **time windows**: pickups, deliveries, shifts, and overtime windows constrain arrival times

The solver uses virtual nodes:

- one virtual depot per vehicle
- one pickup node per order
- one delivery node per order

Virtual nodes matter because two orders can share the same physical address, but the solver still needs to treat them as separate pieces of work.

### Hard chaining rules

For every pickup-and-delivery order, the solver enforces:

- pickup and delivery stay on the same courier route
- pickup happens before delivery
- vehicle load increases at pickup and decreases at delivery
- delivery must fit the allowed time window
- the route must fit the courier's shift and hard constraints

The route can also be constrained by:

- weight in grams
- volume in cubic centimeters
- package count
- max distance
- max stops
- required breaks
- vehicle tags
- fragile and temperature-sensitive eligibility
- motorcycle restrictions for fragile orders

### Large batch decomposition

When a batch is large, the system can split it before solving.

The clustering algorithm is grid-based geographic hashing:

- delivery locations are put into grid cells
- nearby small cells are merged
- each cluster gets nearby vehicles based on distance to the cluster center
- tiny clusters are merged into a nearby viable cluster

This is done to keep matrix sizes and solve time manageable. It is not k-means and it is not a machine-learning clustering model.

### Multi-trip chaining

A courier can do more than one depot cycle in a day. To model that, the system can expand one courier into multiple virtual vehicles:

- trip 1 represents the courier's first route segment
- trip 2 represents the same courier after returning to reload
- later trips continue the same pattern

After solving, these virtual trips are stitched back into one physical courier route. The stitched route includes depot return and depot reload stops between trips.

This avoids pretending a courier has infinite vehicle capacity while still allowing multiple depot loads in one day.

### Warm start

The warm-start cache stores the previous successful assignment:

- which order went to which courier
- the order sequence for each courier

If the next planning run overlaps enough with the previous one, the old solution is used as a starting suggestion for OR-Tools. It does not force the old plan to remain unchanged; it simply gives the solver a better starting point.

### 2-opt route improvement

After a successful OR-Tools run, the system can run a 2-opt improvement pass.

2-opt is a local-search technique. It tries reversing subsections of a single route and keeps the reversal only when:

- distance improves
- pickup-before-delivery remains valid
- timing still works

It does not move orders between couriers. It polishes the order of stops inside an already planned route.

## How Couriers Are Chosen

### Batch courier assignment

In batch mode, courier choice and stop order are solved together by OR-Tools.

The model receives:

- eligible orders
- eligible couriers
- depot and stop coordinates
- OSRM distance matrices
- OSRM duration matrices adjusted by traffic profiles when available
- vehicle capacity and working-hour constraints
- operations-policy optimization settings when the economic optimizer is enabled

The solver tries to minimize an objective while satisfying hard constraints.

In legacy mode, the main arc cost is distance.

In economic mode, the objective can include:

- distance cost
- fuel cost
- labor cost
- fixed route cost
- lateness penalty
- overtime penalty
- unserved-order penalty

So in economic mode, the selected courier is not always simply the geographically closest courier. A courier may be preferred because the whole plan is cheaper, less late, less overtime-heavy, or more feasible under the constraints.

### Who does what: OSRM vs OR-Tools vs our code

| Component | What it decides | What it does not decide |
| --- | --- | --- |
| OSRM | Road travel time, road distance, map geometry, nearest-road snapping | Which courier gets an order, whether a courier is eligible, whether a route is operationally acceptable |
| OR-Tools vehicle-routing solver | Batch courier assignment and stop sequence after our code supplies constraints, costs, and travel-time matrices | Product workflow, tenant policy, manual approvals, mobile execution behavior, customer tracking, business meaning of priorities |
| Our application code | Which orders/couriers are eligible, what constraints and penalties mean, how planning is approved, how routes are executed and monitored | Low-level shortest-road-path calculation inside OSRM, generic search machinery inside OR-Tools |

### Fleet-spread behavior

The operations policy can choose `FULL_FLEET` fleet utilization.

When enabled, the solver adds a delivery-count balancing term. That term penalizes putting too many deliveries on one courier while other available couriers are underused.

In plain terms: `FULL_FLEET` nudges the solver to spread work across couriers. `EFFICIENT` allows more consolidation when that produces the better objective.

### On-demand courier assignment

On-demand dispatch uses `backend/app/algorithms/insertion_heuristic.py`, not the full OR-Tools batch solver.

The algorithm is cheapest insertion:

1. Filter out couriers who cannot legally or physically take the order.
2. For each eligible courier with an active route, try possible pickup and delivery insertion positions.
3. Reject candidate insertions that break capacity, route order, shift, or time-window constraints.
4. Also evaluate starting a new route for eligible couriers.
5. Choose the candidate with the lowest weighted detour.

The "detour" is the extra route time caused by inserting the new order.

### On-demand assignment strategies

The dispatch policy can alter the candidate set before cheapest insertion runs:

- `NEAREST_COURIER`: use the normal cheapest-insertion behavior.
- `LEAST_BUSY`: first restrict candidates to couriers with the fewest current stops.
- `PREFERRED_COURIER`: if the order has a preferred courier and that courier is eligible, restrict candidates to that courier.
- Zone filtering: if a courier has a zone polygon, the pickup must be inside it.

`ZONE_BASED` exists as a policy enum, but the current code does not implement it as a separate assignment algorithm branch comparable to OR-Tools or cheapest insertion.

### Manual assignment

Manual assignment is not optimization. A dispatcher explicitly chooses the courier.

The backend still checks hard constraints:

- courier is online
- order window has not already passed
- weight fits the courier
- fragile and temperature-sensitive restrictions are respected
- pickup is inside the courier zone when a zone exists
- basic time feasibility check passes

If the courier has no active route, the system creates a planned route with pickup and delivery stops. If the courier already has a planned or active route, the system appends the pickup and delivery stops, then retimes the route using OSRM and traffic adjustments.

## Weight and Priority System

"Weight" means two different things in the system, so this section separates them.

### Package weight is a hard capacity rule

Order package weight is stored in kilograms at the product level and converted to grams for the solver.

The solver uses integer grams to avoid floating-point rounding problems.

This weight is not a preference. It is a hard vehicle-capacity constraint:

- if an order would make a courier exceed capacity, that assignment is infeasible
- pickup adds the order weight to the current load
- delivery subtracts the order weight from the current load
- the planned route must never exceed the courier's capacity

Volume and package count work the same way when configured.

### Order priority changes penalties, not physical feasibility

Order priority does not let the system break hard constraints.

An urgent order still cannot go on an overloaded vehicle, outside a courier's working window, or onto a courier who cannot carry it.

Priority affects tradeoffs only after feasibility is considered.

### Batch priority weights

In economic batch optimization, priority changes how expensive it is to be late or leave an order unserved.

Default priority multipliers are:

| Priority | Multiplier |
| --- | ---: |
| `LOW` | `0.75` |
| `NORMAL` | `1.0` |
| `HIGH` | `1.5` |
| `URGENT` | `2.0` |

If an urgent order is late or unassigned, the solver sees a larger penalty than it would for a normal order. That makes urgent work more protected when the solver must choose between imperfect options.

In legacy batch mode, the objective is mostly distance-based, and priority is not the main batch tradeoff signal.

### On-demand priority weights

For on-demand insertion, priority changes the weighted detour score:

| Priority | Detour weight |
| --- | ---: |
| `URGENT` | `0.5` |
| `HIGH` | `0.75` |
| `NORMAL` | `1.0` |
| `LOW` | `1.25` |

Lower is better. This means an urgent order's detour is discounted, making the system more willing to assign it even when it causes more route disruption.

### Courier and vehicle cost weights

Economic optimization can also weight couriers by operating cost.

Vehicle classes have preset costs for:

- fixed route cost
- distance cost per kilometer
- labor cost per minute
- fuel cost per kilometer
- overtime cost per minute

The optimization presets are:

- `COST_SAVER`
- `BALANCED`
- `SERVICE_FIRST`

Those presets change the relative pain of lateness, overtime, unserved orders, and vehicle operating cost.

When a courier has a specific vehicle spec, fuel cost can be derived from that vehicle's fuel efficiency and configured fuel prices. Otherwise, the system uses vehicle-class defaults.

## Open-Source Dependency Note

No, using OSRM does not mean we are "open sourcing the core of the project."

The current stack uses open-source building blocks:

- OSRM is an open-source C++ road-routing engine. The OSRM repository is BSD-2-Clause licensed.
- OR-Tools is an open-source Google optimization library. The OR-Tools repository is Apache-2.0 licensed.
- OpenStreetMap data is open map data under the Open Database License, usually abbreviated ODbL.

Those facts do **not** automatically force our application code, dispatch workflow, UI, policies, mobile app, tracking app, or backend orchestration to be open source.

The practical compliance items are different:

- keep the required license notices for bundled open-source software
- credit OpenStreetMap when using OpenStreetMap-derived data or maps
- be careful before distributing modified OSRM binaries, modified profiles, or derived map databases
- get legal review before making commercial licensing claims

This is not legal advice; it is the engineering-level reading of the dependencies we use.

## Success Metrics

There are two kinds of success:

- **Correctness**: the route is valid and executable.
- **Quality**: the route is better for service, cost, time, and operations.

### Correctness metrics

A routing result is incorrect if it violates hard constraints.

The validator checks for:

- duplicate order assignment
- missing pickup or delivery
- pickup after delivery
- capacity exceeded
- package-count exceeded
- delivery or pickup outside allowed windows
- route outside shift hours
- max stops exceeded
- max distance exceeded

A good correctness target is:

- zero error-level validation violations on committed routes
- zero duplicate order assignments
- zero missing pickup or delivery pairs

### Assignment metrics

These measure whether the planner found coverage:

- assignment rate: assigned orders divided by total eligible orders
- unassigned order count
- unassigned ratio
- routes created
- active vehicle count
- average stops per route

The system already returns several of these from batch dispatch under `quality_metrics`.

### Route quality metrics

These measure plan efficiency:

- total route distance
- total route duration
- average route distance
- average route duration
- generalized operating cost in economic mode
- fuel cost and estimated fuel consumption when vehicle specs are available
- overtime minutes
- late minutes

The right target depends on the business. A food-delivery workspace may accept more distance to reduce lateness. A scheduled parcel workspace may prefer lower cost and fewer active routes.

### Service metrics

These measure customer and operations outcomes:

- on-time delivery rate
- completion rate
- failed delivery rate
- first-attempt success rate
- SLA alert count
- critical active alert count
- customer ETA accuracy

Existing launch metrics already call out completion rate, failed delivery rate, ETA error, optimization runtime, and unassigned ratio.

### Runtime and reliability metrics

These measure whether optimization is operationally dependable:

- batch dispatch latency
- on-demand dispatch latency
- solver status: `OPTIMAL`, `FEASIBLE`, `GREEDY_FALLBACK`, `NOT_FOUND`, or `ERROR`
- OSRM table latency and error count
- OSRM route latency and error count
- matrix cache hit count
- optimization run failure rate

An operationally healthy system should not only produce good routes; it should produce them quickly enough for dispatchers to trust them during live operations.

### How to tell if routing works

Routing is working correctly when:

- every assigned order has exactly one pickup and one delivery
- every pickup occurs before its delivery
- every courier route respects capacity, time, shift, and vehicle rules
- the road times come from OSRM matrices or route calls
- unassigned orders have explainable constraint reasons

Routing is working well when:

- unassigned ratio is low
- on-time rate is high
- ETA error is shrinking over time
- total distance, duration, overtime, and generalized cost are reasonable for the chosen preset
- dispatcher overrides are rare and explainable

## Demo-Safe Wording and Honest Limits

### Say this

- "Road paths and matrices come from self-hosted OSRM, the Open Source Routing Machine, running Multi-Level Dijkstra over prepared road graphs."
- "Scheduled and bulk planning use Google's OR-Tools vehicle-routing solver. OR-Tools is an optimization library, and here it solves courier assignment and stop sequencing under constraints."
- "On-demand dispatch uses cheapest insertion because it is fast and deterministic for one new order."
- "Priority does not override hard constraints; it changes how strongly the optimizer avoids lateness or unserved urgent work."
- "The economic optimizer can trade distance, fuel, labor, lateness, overtime, and unserved-order penalties."
- "Large batches may be geographically decomposed first so each solver problem stays manageable."
- "OSRM and OR-Tools are open-source engines inside the system, not the whole product."

### Do not say this

- "We use AI to decide routes."
- "We wrote our own Dijkstra or A* route planner."
- "OR-Tools is our algorithm." Say what it means: Google's vehicle-routing solver, fed by our constraints and costs.
- "The closest courier always gets the order."
- "Urgent orders ignore constraints."
- "Zone-based assignment is a fully separate algorithm."
- "If OSRM is down, planning safely falls back to straight-line routing."
- "Because we use OSRM, our whole product has to be open source."

### Current limits

- OSRM is a hard dependency for planning matrices and road geometry.
- Batch planning has a 1000-order fetch cap per tenant for memory protection.
- Large-batch clustering improves scalability but can reduce global optimality because clusters are solved separately.
- `ZONE_BASED` is defined as a policy value, but current dispatch logic does not implement a full zone-based optimizer branch.
- Learned traffic behavior is a lightweight multiplier model, not a deep learning model.

## Source Trail

Use these files when checking or updating this document:

- `backend/docker-compose.yml`: OSRM car/truck containers and `--algorithm mld`
- `backend/scripts/prepare_osrm_data.sh`: OSRM graph preparation and profiles
- `backend/app/algorithms/osrm_client.py`: OSRM route, table, nearest, retry, and cache behavior
- `backend/app/services/vehicle_routing_profile.py`: vehicle-to-OSRM-profile mapping
- `backend/app/services/dispatch_service.py`: batch and on-demand orchestration
- `backend/app/algorithms/vrp_solver.py`: OR-Tools batch solver model
- `backend/app/algorithms/insertion_heuristic.py`: on-demand cheapest insertion
- `backend/app/algorithms/greedy_fallback.py`: fallback assignment
- `backend/app/algorithms/geo_clustering.py`: large-batch decomposition
- `backend/app/algorithms/two_opt.py`: post-solve route improvement
- `backend/app/algorithms/solution_validator.py`: correctness checks
- `backend/app/schemas/operations_policy.py`: optimization presets and priority multipliers

External references:

- [OSRM backend repository](https://github.com/Project-OSRM/osrm-backend): OSRM services, MLD/CH preprocessing, Docker usage, and BSD-2-Clause license
- [OSRM API documentation](https://project-osrm.org/docs/v26.4.0/): Route, Table, Nearest, and other services
- [Google OR-Tools overview](https://developers.google.com/optimization/introduction): what OR-Tools is and what kinds of optimization problems it solves
- [Google OR-Tools pickup-and-delivery routing guide](https://developers.google.com/optimization/routing/pickup_delivery): vehicle-routing model with pickup/delivery pairs
- [OpenStreetMap copyright and license page](https://www.openstreetmap.org/copyright/): ODbL data license and attribution requirements
