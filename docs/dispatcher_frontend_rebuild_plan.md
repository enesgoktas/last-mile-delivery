# Dispatcher Frontend Rebuild Plan

Date: 2026-03-31
Status: Proposed
Owner: Frontend rebuild

## Why We Should Rebuild

The current dispatcher UI is solving too many adjacent problems in too many places.

- Order intake lives on the Orders page.
- Automatic optimization and approval live on Live Ops.
- Manual assignment is split between order details, the exception center, and the route editor.
- Failed orders and alerts are promoted to separate destinations even though they are really dispatch exceptions.
- The highest-value action, "I entered orders, now assign them," is not the obvious primary path.

This is not a backend capability problem. The backend already supports the core dispatch behaviors we need:

- New ON_DEMAND orders can auto-enqueue insertion dispatch on creation.
- Pending SCHEDULED and BULK orders can run through batch optimization.
- Optimization preview, approval, and rejection already exist.
- Manual assignment and preferred-courier pinning already exist.

The rebuild should make those capabilities feel like one coherent product instead of several disconnected tools.

## Ground Truth From The Existing Code

Frontend complexity is concentrated in a few oversized surfaces:

- `frontend/src/pages/OrdersPage.tsx` is 1320 lines.
- `frontend/src/pages/LiveOpsPage.tsx` is 952 lines.
- `frontend/src/components/ExceptionCenter.tsx` is 332 lines.
- `frontend/src/components/RouteEditorPanel.tsx` is 392 lines.

Today the primary dispatcher workflow is spread across many routes:

- `Live Ops`
- `Orders`
- `Couriers`
- `Failed`
- `Analytics`
- `Alerts`
- `Audit`
- `Settings`

That is too much navigation for a product whose core job is dispatching work.

## Product Principles

1. One primary workflow: create order -> auto-assign -> review result.
2. Manual dispatch remains available, but always secondary.
3. Exceptions stay inside dispatch, not in separate top-level pages.
4. The map supports dispatch; it should not dominate the product.
5. Settings are admin-only and visually separate from daily operations.
6. Reuse one set of action patterns everywhere. No repeated "same action, different UI."
7. Every critical action must show a clear result state: assigned, queued, needs review, blocked, failed.

## Recommended Information Architecture

Primary navigation:

- Dispatch
- Orders
- Couriers
- Settings

Secondary / deferred:

- Analytics
- Audit

Removed as standalone primary nav items:

- Failed Orders
- SLA Alerts

Both become sections inside Dispatch.

## Core User Flows

### Flow 1: Single on-demand order

1. Dispatcher clicks `New Order`.
2. Fills minimal intake form.
3. Clicks `Create & Auto-Assign`.
4. UI creates the order.
5. If workspace dispatch mode is `AUTOMATIC` or `HYBRID`, the UI immediately shows `Assigning order...` and polls until the order becomes assigned or returns a blocking reason.
6. Result appears inline:
   - Assigned to courier
   - Still pending with reason
   - Needs manual review

### Flow 2: Several pending orders

1. Dispatcher adds several orders or imports CSV.
2. Dispatcher lands back on Dispatch with those orders highlighted in the pending queue.
3. Dispatcher clicks `Auto-Assign Pending Orders`.
4. UI triggers optimization.
5. If route approval is not required, results commit and the queue refreshes.
6. If route approval is required, UI opens a review screen with `Approve Plan` and `Reject Plan`.

### Flow 3: Manual fallback

1. Order is still pending or blocked.
2. Dispatcher opens order details.
3. Dispatcher can:
   - `Assign Manually`
   - `Pin Courier`
   - `Clear Pin`
   - `Reschedule`
   - `Cancel Order`

### Flow 4: Route correction

1. Dispatcher opens a route from Dispatch or Couriers.
2. Dispatcher can:
   - `Reorder Stops`
   - `Add Dispatcher Note`
   - `Add Pending Order`

This remains secondary. It should never be the first thing users must do after entering orders.

## Screens From Scratch

## 1. Dispatch

This becomes the home screen and the operational center.

### Goal

Make "assign pending work" the most obvious thing on the screen.

### Layout

- Top action bar
- Pending queue
- Draft review banner when applicable
- Active routes summary
- Exceptions section
- Optional map toggle or right-side map panel

### Top-level buttons

- `New Order`
- `Import Orders`
- `Auto-Assign Pending Orders`
- `Review Draft Plan` (only when a pending approval run exists)
- `Refresh`

### Sections

#### A. Pending Queue

Shows all dispatchable work in one list, regardless of how it was created.

Columns:

- Order ref
- Delivery address
- Type
- Priority
- Time window
- Status
- Preferred courier
- Assignment result / blocker

Row actions:

- `Details`
- `Assign Manually`
- `Pin Courier`
- `Clear Pin`
- `Retry Auto-Assign`

#### B. Draft Review Banner

Visible only when optimization created a draft run.

Shows:

- routes created
- orders newly assigned
- orders still unassigned
- violations or attention items

Buttons:

- `Review Draft Plan`
- `Approve Plan`
- `Reject Plan`

#### C. Active Routes

Compact route cards, not a giant control surface.

Each card shows:

- courier
- route status
- stops complete / total
- distance
- next stop

Buttons:

- `Open Route`

#### D. Exceptions

Merged content from the current exception center, failed-orders page, and alerts page.

Subsections:

- Critical alerts
- Failed deliveries
- Still unassigned orders

Buttons:

- `Acknowledge`
- `Open Route`
- `Reschedule`
- `Cancel Order`
- `Assign Manually`

#### E. Map

The map is optional and secondary.

Rules:

- hidden by default on smaller screens
- collapsible on desktop
- never blocks the primary dispatch queue

Map buttons:

- `Show Map`
- `Hide Map`

## 2. Order Intake

This can be a dedicated page or a large drawer/modal opened from Dispatch.
It should be optimized for speed.

### Goal

Let a dispatcher enter a valid order without forcing them through operational screens.

### Structure

- Intake mode switch: `Single Order` / `CSV Import`
- Minimal required form first
- Advanced fields collapsed
- Assignment intent visible before submit

### Single order fields shown by default

- Order type
- Priority
- Pickup address
- Delivery address
- Delivery window
- Customer email
- Optional depot when required by policy

Advanced section:

- phone
- reference
- cargo dimensions / weight
- fragile / temperature-sensitive
- notes
- custom fields

### Single order buttons

- `Create & Auto-Assign`
- `Create Only`
- `Cancel`

### Single order result states

- `Order created`
- `Assigning order...`
- `Assigned to courier X`
- `Could not auto-assign`
- `Saved to pending queue`

### CSV import

Steps:

1. upload file
2. validate file
3. review row counts and errors
4. publish valid rows
5. optionally auto-assign immediately

Buttons:

- `Upload CSV`
- `Validate File`
- `Create Valid Orders`
- `Create Valid Orders & Auto-Assign`
- `Download Errors`

## 3. Orders

Orders becomes a clean operational ledger, not a second dispatch console.

### Goal

Find and inspect orders quickly, with manual actions available from one place.

### What stays here

- search
- filtering
- date ranges
- order details
- manual order actions
- status history

### What leaves this page

- primary optimization CTA
- exception handling as a separate workflow
- anything that belongs to daily dispatch execution

### Top-level buttons

- `New Order`
- `Import Orders`

### Row actions

- `Open`

### Detail drawer buttons

- `Assign Manually`
- `Pin Courier`
- `Clear Pin`
- `Retry Auto-Assign`
- `Reschedule`
- `Cancel Order`
- `Close`

## 4. Couriers

Couriers stays as a roster and capacity screen.

### Goal

Make it easy to understand who is available and who can take work.

### Main content

- courier roster
- availability
- vehicle type
- capacity
- current route summary

### Buttons

- `Add Courier`
- `Edit Courier`
- `Open Active Route`

## 5. Settings

Settings should support dispatch, not compete with it.

### Goal

Keep admin configuration separate and calmer.

### Sections

- Dispatch policy
- Depots
- Constraints
- Users
- API / Webhooks

### Most important dispatch settings for MVP

- dispatch mode
- route approval mode
- assignment strategy
- optimization preset

### Buttons

- `Save Changes`
- `Apply Preset`
- `Discard`

## Recommended Visual Hierarchy

- Dispatch CTA uses the strongest visual emphasis everywhere.
- Manual actions use secondary or ghost styles.
- Dangerous actions like cancel and reject stay destructive.
- Pending and blocked states should be obvious without opening a drawer.
- Keep route IDs and courier IDs readable, but never let raw IDs dominate the design.

## What We Should Explicitly Remove

- Separate top-level `Failed Orders` page
- Separate top-level `SLA Alerts` page
- Giant left-panel / map-first Live Ops layout
- Repeated assignment controls in multiple unrelated contexts
- Needing to bounce from Orders -> Live Ops -> Overlay -> Route editor to finish one dispatch task

## Frontend Architecture For The Rebuild

Recommended feature structure:

- `features/dispatch`
- `features/orders`
- `features/couriers`
- `features/settings`
- `components/ui`

Recommended rules:

- One shared order form, not multiple order-creation variants.
- One shared order action menu / action hook.
- One shared optimization status model.
- One shared polling / invalidation model.

Strong recommendation:

- Introduce TanStack Query for server-state fetching, polling, cache invalidation, and mutation state.

Reason:

- The current app repeats polling, refetch, and loading/error plumbing in several places.
- This is exactly the kind of repetition we should delete instead of re-wrapping again.

If we do not add TanStack Query, we should still replace repeated custom polling patterns with a single feature-level data layer.

## Small Backend Addition I Recommend

Most of the rebuild can use existing APIs, but one backend addition would make the frontend much cleaner:

- Add a single `dispatch pending work` endpoint that orchestrates the correct behavior for all pending orders.

Why:

- Current batch optimization only targets pending `SCHEDULED` and `BULK` orders.
- Current ON_DEMAND auto-dispatch happens on order creation, one order at a time.
- A single frontend button labeled `Auto-Assign Pending Orders` is much easier to reason about if the backend owns the mixed-order orchestration.

Without this endpoint, the frontend can still be rebuilt, but the button semantics will be harder to explain.

## Implementation Phases

### Phase 1: Architecture and shell

- Build new app shell and reduced navigation.
- Create shared layout, action bar, drawer, table, and state primitives.
- Add feature-level data layer and mutation hooks.

### Phase 2: Order intake + dispatch MVP

- Build Order Intake.
- Build Dispatch page.
- Wire create order, import, auto-assign, draft review, manual assignment.
- Make this the primary operational workflow.

### Phase 3: Orders and Couriers

- Rebuild Orders page as a clean ledger.
- Rebuild Couriers page around availability and route visibility.
- Reuse shared details drawers and action components.

### Phase 4: Settings

- Rebuild dispatch-related settings first.
- Defer rarely used admin tabs until the operational rebuild is stable.

### Phase 5: Analytics and cleanup

- Reintroduce analytics only after the operational product feels correct.
- Remove legacy pages and dead components.

## Testing Strategy

This rebuild must ship with stronger coverage than the current frontend.

### Component and hook tests

- order intake validation
- create order success and failure states
- create-and-auto-assign flow
- pending queue filters and rendering
- optimization draft review states
- manual assignment
- preferred courier pin / clear
- reschedule and cancel actions
- courier roster states

### Integration tests

- ON_DEMAND create -> auto-assign success
- ON_DEMAND create -> auto-assign blocked
- CSV import -> publish -> auto-assign
- batch optimize -> draft review -> approve
- batch optimize -> draft review -> reject
- failed order -> reschedule from Dispatch
- unassigned order -> manual assignment from Dispatch

### End-to-end tests

- dispatcher logs in and creates a new order
- dispatcher creates an order and sees assignment outcome
- dispatcher imports orders and auto-assigns them
- dispatcher reviews a draft plan and approves it
- dispatcher manually assigns a blocked order

## Success Criteria

We should consider the rebuild successful when:

- A dispatcher can create an order and start assignment from one obvious place.
- A dispatcher never needs to visit more than two screens for the common flow.
- Exceptions are visible inside Dispatch without extra navigation.
- Manual assignment is available but not required for normal operation.
- The codebase has shared primitives instead of repeated flow logic.

## Recommended Build Order

1. New app shell and reduced nav
2. Dispatch page
3. Order intake
4. Shared order details drawer
5. Couriers page
6. Settings
7. Delete legacy screens

## Final Recommendation

Yes, we should treat this as a real frontend rebuild, not a patch series.

The right move is not "clean up Live Ops a bit." The right move is:

- reduce the product to four primary destinations
- put dispatch at the center
- make `Create & Auto-Assign` and `Auto-Assign Pending Orders` the dominant actions
- demote manual routing tools to fallback controls
- merge exception workflows back into Dispatch

That gives us a frontend shaped around the actual job the user is trying to do.
