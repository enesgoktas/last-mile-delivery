import type {
  AnalyticsSummaryContract,
  CourierProfileContract,
  EPAMenuEntryContract,
  EPASearchResultContract,
  EtaVisibility,
  FailureAction,
  FailureReasonOption,
  FieldMode,
  FuelType,
  OperationsPolicyContract,
  PolicyPresetContract,
  PreviewChangedOrderContract,
  PreviewExplainabilityChangeType,
  PreviewExplainabilityItemContract,
  PreviewExplainabilityTone,
  PreviewRouteChangeType,
  PreviewRouteContract,
  PreviewRouteExplainabilityContract,
  PreArrivalCallMode,
  PushProvider,
  RouteContract,
  RouteStatus,
  RouteStopContract,
  RunPreviewContract,
  RunPreviewExplainabilityContract,
  SafeDropPolicy,
  SelfServiceMode,
  StopActionPolicy,
  StopFailurePolicy,
  StopProofPolicy,
  StopStatus,
  StopType,
  VehicleSpecContract,
  VehicleType,
  AvailabilityStatus,
} from "../../shared/api-contract";

export type UserRole =
  | "SUPERADMIN"
  | "OWNER"
  | "ADMIN"
  | "DISPATCHER"
  | "COURIER"
  | "COMPANY_ADMIN";

// ── Superadmin control plane ──

export interface FeatureFlags {
  deliveries_enabled: boolean;
  rides_enabled: boolean;
  ride_pickup_buffer_enabled: boolean;
  ride_strict_lateness_enabled: boolean;
  ride_airport_features_enabled: boolean;
  ride_no_show_handling_enabled: boolean;
  ev_battery_awareness_enabled: boolean;
  ev_low_battery_alerts_enabled: boolean;
  ev_auto_charging_enabled: boolean;
  multi_depot_vehicles_enabled: boolean;
  multi_trip_enabled: boolean;
  traffic_aware_routing_enabled: boolean;
}

export interface FeatureFlagSchemaEntry {
  name: keyof FeatureFlags;
  label: string;
  description: string;
  category: string;
  default: boolean;
  depends_on: Array<keyof FeatureFlags>;
}

export interface SuperadminTenantSummary {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
  user_count: number;
  feature_flags: FeatureFlags;
}

export interface SuperadminTenantDetail extends SuperadminTenantSummary {
  logo_url: string | null;
  primary_color: string | null;
  secondary_color: string | null;
  operations_policy: Record<string, unknown>;
}

export interface SuperadminTenantCreatePayload {
  name: string;
  slug: string;
  logo_url?: string | null;
  primary_color?: string | null;
  secondary_color?: string | null;
  feature_flags: FeatureFlags;
  operations_policy?: Record<string, unknown> | null;
  initial_user: {
    email: string;
    password: string;
    role: "OWNER" | "ADMIN";
  };
}

export interface SuperadminTenantUpdatePayload {
  name?: string;
  slug?: string;
  is_active?: boolean;
  logo_url?: string | null;
  primary_color?: string | null;
  secondary_color?: string | null;
}

export interface SuperadminInviteUserPayload {
  email: string;
  password: string;
  role: "OWNER" | "ADMIN" | "DISPATCHER" | "COURIER" | "COMPANY_ADMIN";
}

export interface SuperadminInviteUserResponse {
  id: string;
  email: string;
  role: UserRole;
}

// ── Phase 2 ride analytics ──

export interface RideStatusBreakdown {
  pending: number;
  assigned: number;
  in_transit: number;
  delivered: number;
  cancelled: number;
  failed: number;
}

export interface PassengerRideSummary {
  passenger_name: string;
  ride_count: number;
  completed_count: number;
  cancelled_count: number;
}

export interface RideAnalyticsResponse {
  total_rides: number;
  completed_rides: number;
  cancelled_rides: number;
  upcoming_rides: number;
  by_status: RideStatusBreakdown;
  top_passengers: PassengerRideSummary[];
  airport_pickups: number;
}

export interface CourierUtilizationRow {
  courier_id: string;
  route_count: number;
  total_distance_km: number;
  total_hours: number;
  order_count: number;
}

export interface FleetUtilizationResponse {
  rows: CourierUtilizationRow[];
  total_route_count: number;
  total_distance_km: number;
  total_hours: number;
}

export interface TerminalPunctualityRow {
  terminal: string;
  total: number;
  on_time: number;
  on_time_rate: number;
}

export interface AirportPunctualityResponse {
  rows: TerminalPunctualityRow[];
  overall_total: number;
  overall_on_time: number;
  overall_on_time_rate: number;
}

export interface VehicleTag {
  id: string;
  name: string;
  color: string;
}

export interface VehicleTagCreate {
  name: string;
  color?: string;
}

export type OrderStatus =
  | "DRAFT"
  | "PENDING"
  | "ASSIGNED"
  | "PICKED_UP"
  | "IN_TRANSIT"
  | "DELIVERED"
  | "FAILED"
  | "RESCHEDULED"
  | "CANCELLED"
  | "EXPIRED";

export type OrderType = "ON_DEMAND" | "SCHEDULED" | "BULK" | "REVERSE" | "PICKUP_AND_DELIVERY" | "RETURN" | "RIDE";

export type OrderPriority = "LOW" | "NORMAL" | "HIGH" | "URGENT";

export type FailedOrderCustomerResponseStatus = "PENDING" | "RESPONDED" | "ESCALATED";

export type VisibilityMode = "HIDDEN" | "OPTIONAL" | "REQUIRED";
export type DispatchMode = "AUTOMATIC" | "MANUAL" | "HYBRID";
export type AssignmentStrategy = "NEAREST_COURIER" | "LEAST_BUSY" | "ZONE_BASED" | "PREFERRED_COURIER";
export type RouteApprovalMode = "NONE" | "LARGE_ROUTES_ONLY" | "ALL_ROUTES";
export type RetryTiming = "SAME_DAY" | "NEXT_DAY" | "MANUAL_ONLY";
export type CustomFieldType = "TEXT" | "NUMBER" | "DATE" | "SELECT" | "BOOLEAN";
export type OptimizationPresetKey = "COST_SAVER" | "BALANCED" | "SERVICE_FIRST";
export type FleetUtilization = "EFFICIENT" | "FULL_FLEET";
export type RoutingObjective = "FUEL_AWARE" | "SERVICE_FIRST";

export type {
  AvailabilityStatus,
  EPAMenuEntryContract as EPAMenuEntry,
  EPASearchResultContract as EPASearchResult,
  EtaVisibility,
  FailureAction,
  FailureReasonOption,
  FieldMode,
  FuelType,
  PreviewExplainabilityChangeType,
  PreviewExplainabilityTone,
  PreviewRouteChangeType,
  PreArrivalCallMode,
  PushProvider,
  RouteStatus,
  SafeDropPolicy,
  SelfServiceMode,
  StopActionPolicy,
  StopFailurePolicy,
  StopProofPolicy,
  StopStatus,
  StopType,
  VehicleType,
};

export type VehicleSpec = VehicleSpecContract;

type DeepRequired<T> =
  T extends readonly (infer U)[] ? DeepRequired<U>[] :
  T extends object ? { [K in keyof T]-?: DeepRequired<NonNullable<T[K]>> } :
  T;

export type OperationsPolicy = DeepRequired<OperationsPolicyContract> & {
  optimization: DeepRequired<OperationsPolicyContract>["optimization"] & {
    routing_objective: RoutingObjective;
  };
};
export type NotificationChannelPreferences = OperationsPolicy["notifications"]["event_channels"][string];
export type CustomOrderFieldDefinition = OperationsPolicy["order_form"]["custom_fields"][number];
// SUPERADMIN is a platform-level role and is not present in tenant
// team_permissions — narrow to the per-tenant roles.
export type TenantRole = Exclude<UserRole, "SUPERADMIN">;
export type RolePermissionSet = OperationsPolicy["team_permissions"][TenantRole];
export type PolicyPreset = Omit<PolicyPresetContract, "policy"> & {
  policy: OperationsPolicy;
};

// Lifecycle counts. Field is optional in the generated contract because the
// backend started returning it after the OpenAPI schema was last regenerated;
// callers should treat it as best-effort.
export interface OrderStatusBreakdown {
  DRAFT: number;
  PENDING: number;
  ASSIGNED: number;
  PICKED_UP: number;
  IN_TRANSIT: number;
  DELIVERED: number;
  FAILED: number;
  RESCHEDULED: number;
  CANCELLED: number;
  EXPIRED: number;
}

export type AnalyticsSummary = AnalyticsSummaryContract & {
  status_breakdown?: OrderStatusBreakdown;
};

// ── Optimization savings (Insights → "Optimization gains" panel) ─────
// Aggregated baseline-vs-selected scenario deltas. The backend always
// returns the totals (zero-filled when there are no contributing
// comparisons), so callers can render KPIs unconditionally; check
// `comparisons_count > 0` before showing per-day charts.
export interface OptimizationSavingsTotals {
  baseline_distance_km: number;
  optimized_distance_km: number;
  distance_saved_km: number;
  distance_saved_pct: number;
  baseline_duration_min: number;
  optimized_duration_min: number;
  duration_saved_min: number;
  duration_saved_pct: number;
  baseline_on_time_rate: number;
  optimized_on_time_rate: number;
  on_time_rate_delta: number;
  late_minutes_saved: number;
  energy_cost_saved_try: number;
  fuel_saved_liters: number;
  co2_saved_kg: number;
  routes_saved: number;
}

export interface OptimizationSavingsDayRow {
  date: string;
  selected_scenario_key: string;
  baseline_distance_km: number;
  optimized_distance_km: number;
  baseline_duration_min: number;
  optimized_duration_min: number;
  baseline_on_time_rate: number;
  optimized_on_time_rate: number;
  baseline_energy_cost_try: number;
  optimized_energy_cost_try: number;
  baseline_co2_kg: number;
  optimized_co2_kg: number;
}

export interface OptimizationSavingsSolverMixRow {
  scenario_key: string;
  count: number;
}

export interface OptimizationSavingsResponse {
  comparisons_count: number;
  totals: OptimizationSavingsTotals;
  per_day: OptimizationSavingsDayRow[];
  solver_mix: OptimizationSavingsSolverMixRow[];
}

export interface CostSavingsTotals {
  total_fuel_cost_try: number;
  total_fuel_liters: number;
  total_co2_kg: number;
  total_distance_km: number;
  total_routes: number;
  total_deliveries: number;
  cost_per_delivery_try: number | null;
  cost_per_km_try: number | null;
  fuel_cost_saved_try: number | null;
  co2_saved_kg: number | null;
  distance_saved_km: number | null;
}

export interface CostSavingsDayRow {
  date: string;
  total_fuel_cost_try: number;
  total_fuel_liters: number;
  total_co2_kg: number;
  total_distance_km: number;
  routes: number;
  fuel_cost_saved_try: number | null;
  co2_saved_kg: number | null;
}

export interface CostSavingsResponse {
  routes_included: number;
  totals: CostSavingsTotals;
  per_day: CostSavingsDayRow[];
}

export interface OrderForensicsHeader {
  id: string;
  external_ref: string | null;
  status: string;
  priority: string;
  scheduled_for_date: string | null;
  earliest_delivery: string;
  latest_delivery: string;
  assigned_courier_id: string | null;
  route_id: string | null;
  attempt_count: number;
  failed_at: string | null;
  failure_cause_category: string | null;
  failure_cause_subcategory: string | null;
}

export interface OrderForensicsStatusHistoryRow {
  from_status: string | null;
  to_status: string;
  actor_user_id: string | null;
  reason: string | null;
  created_at: string;
}

export interface OrderForensicsStopRow {
  id: string;
  sequence_number: number;
  stop_type: string;
  status: string;
  lat: number;
  lon: number;
  planned_arrival: string;
  planned_departure: string | null;
  actual_arrival: string | null;
  failure_reason: string | null;
  delay_cause: string | null;
}

export interface OrderForensicsStopHistoryRow {
  stop_id: string;
  from_status: string | null;
  to_status: string;
  reason: string | null;
  lat: number | null;
  lon: number | null;
  created_at: string;
}

export interface OrderForensicsDispatchDecisionRow {
  optimization_run_id: string | null;
  kind: string;
  chosen_courier_id: string | null;
  chosen_score: number | null;
  alternatives: Array<Record<string, unknown>> | null;
  decision_reason: string | null;
  created_at: string;
}

export interface OrderForensicsETAPredictionRow {
  stop_id: string;
  predicted_by: string;
  predicted_at: string;
  predicted_arrival: string;
  actual_arrival: string | null;
  prediction_horizon_s: number | null;
  error_s: number | null;
}

export interface OrderForensicsResponse {
  order: OrderForensicsHeader;
  status_history: OrderForensicsStatusHistoryRow[];
  stops: OrderForensicsStopRow[];
  stop_history: OrderForensicsStopHistoryRow[];
  dispatch_decisions: OrderForensicsDispatchDecisionRow[];
  eta_predictions: OrderForensicsETAPredictionRow[];
}

export interface ETAAccuracyStats {
  predictions: number;
  on_time_rate: number | null;
  mean_abs_error_s: number | null;
  p95_abs_error_s: number | null;
  mean_error_signed_s: number | null;
  on_time_window_s: number;
}

export interface FailureCauseRow {
  category: string;
  count: number;
  share: number;
}

export interface FailureCausesStats {
  total_failures: number;
  by_category: FailureCauseRow[];
}

export interface PlanStabilityRow {
  created_at: string;
  stability_pct: number | null;
  orders_evaluated: number;
}

export interface PlanStabilityStats {
  samples: number;
  avg_stability_pct: number | null;
  recent: PlanStabilityRow[];
}

export interface SolverQualityResponse {
  eta_accuracy: ETAAccuracyStats;
  failure_causes: FailureCausesStats;
  plan_stability: PlanStabilityStats;
}

// `CourierProfileContract` (auto-generated from the OpenAPI schema) already
// includes the multi-depot fields `additional_depot_ids` + `end_depot_id`
// after Phase 1. We only intersect-add `vehicle_tags` because that field
// is currently absent from the auto-generated contract.
export type Courier = CourierProfileContract & { vehicle_tags?: VehicleTag[] };

export interface CourierCreate {
  user_id: string;
  vehicle_type: VehicleType;
  vehicle_spec_id?: string;
  max_weight_kg: number;
  max_volume_cm3?: number;
  max_packages?: number;
  depot_lat: number;
  depot_lon: number;
  depot_id?: string;
  working_hours_start?: string;
  working_hours_end?: string;
  // Phase 1
  max_passengers?: number;
  max_battery_range_km?: number;
  current_battery_pct?: number;
  battery_low_threshold_pct?: number;
  // Multi-depot
  additional_depot_ids?: string[];
  end_depot_id?: string;
}

export interface CourierUpdate {
  vehicle_type?: VehicleType;
  vehicle_spec_id?: string | null;
  max_weight_kg?: number;
  max_volume_cm3?: number;
  max_packages?: number;
  depot_lat?: number;
  depot_lon?: number;
  depot_id?: string;
  working_hours_start?: string;
  working_hours_end?: string;
  // Phase 1
  max_passengers?: number;
  max_battery_range_km?: number;
  current_battery_pct?: number;
  battery_low_threshold_pct?: number;
  // Multi-depot
  additional_depot_ids?: string[];
  end_depot_id?: string | null;
}

export interface Order {
  id: string;
  status: OrderStatus;
  type: OrderType;
  priority: OrderPriority;
  pickup_address: string;
  pickup_lat?: number;
  pickup_lon?: number;
  delivery_address: string;
  delivery_lat?: number;
  delivery_lon?: number;
  depot_id?: string | null;
  weight_kg?: number;
  is_fragile?: boolean;
  is_temp_sensitive?: boolean;
  required_vehicle_tags?: VehicleTag[];
  earliest_pickup?: string | null;
  latest_pickup?: string | null;
  return_of_order_id?: string | null;
  earliest_delivery: string;
  latest_delivery: string;
  scheduled_for_date?: string | null;
  attempt_count: number;
  max_attempts: number;
  failed_at?: string | null;
  customer_response_status?: FailedOrderCustomerResponseStatus | null;
  customer_response_due_at?: string | null;
  escalated_at?: string | null;
  customer_email: string;
  customer_phone?: string | null;
  delivery_otp_code?: string | null;
  assigned_courier_id?: string | null;
  preferred_courier_id?: string | null;
  route_id?: string | null;
  external_ref?: string | null;
  custom_fields?: Record<string, unknown> | null;
  notes?: string | null;
  created_at: string;
  updated_at?: string;
  // Phase 1: ride-mode fields (only meaningful when type === "RIDE")
  passenger_count?: number;
  passenger_name?: string | null;
  passenger_phone?: string | null;
  flight_info?: Record<string, unknown> | null;
  special_requirements?: string | null;
  pickup_buffer_minutes_override?: number | null;
  service_time_pickup_seconds_override?: number | null;
  // Shared across siblings of a split ride. NULL for standalone orders.
  ride_group_id?: string | null;
}

export interface ExceptionCenterCounts {
  pending_approval_runs: number;
  critical_alerts: number;
  failed_orders: number;
  unassigned_orders: number;
  total_open_items: number;
}

export interface ExceptionCenterSummary {
  counts: ExceptionCenterCounts;
  pending_run: OptimizationRun | null;
  critical_alerts: SLAAlert[];
  failed_orders: Order[];
  unassigned_orders: Order[];
}

export interface OrderCreate {
  type: OrderType;
  priority: OrderPriority;
  pickup_address: string;
  pickup_lat: number;
  pickup_lon: number;
  delivery_address: string;
  delivery_lat: number;
  delivery_lon: number;
  depot_id?: string;
  weight_kg?: number;
  length_cm?: number;
  width_cm?: number;
  height_cm?: number;
  volume_cm3?: number;
  is_fragile?: boolean;
  is_temp_sensitive?: boolean;
  required_vehicle_tag_ids?: string[];
  earliest_pickup?: string;
  latest_pickup?: string;
  earliest_delivery: string;
  latest_delivery: string;
  customer_email: string;
  customer_phone?: string;
  external_ref?: string;
  custom_fields?: Record<string, unknown>;
  notes?: string;
  // Phase 1: ride-mode fields
  passenger_count?: number;
  passenger_name?: string;
  passenger_phone?: string;
  flight_info?: Record<string, unknown> | null;
  special_requirements?: string;
  pickup_buffer_minutes_override?: number;
  service_time_pickup_seconds_override?: number;
  // Splitting a large RIDE across multiple vehicles
  allow_split?: boolean;
  passengers_per_vehicle?: number;
}

export interface OrderReturnCreate {
  earliest_pickup: string;
  latest_pickup: string;
  reason_code?: string;
}

// ── Recurring Orders ──

export type RecurrenceCadence = "DAILY" | "WEEKLY" | "BIWEEKLY" | "MONTHLY" | "CUSTOM";

export interface RecurringOrderTemplate {
  id: string;
  tenant_id: string;
  name: string;
  description?: string | null;
  is_active: boolean;
  cadence: RecurrenceCadence;
  interval_days: number;
  day_of_week_mask: number;
  day_of_month?: number | null;
  start_date: string;
  end_date?: string | null;
  max_occurrences?: number | null;
  occurrences_generated: number;
  delivery_window_start: string;
  delivery_window_end: string;
  pickup_window_start?: string | null;
  pickup_window_end?: string | null;
  lead_time_days: number;
  order_type: OrderType;
  priority: OrderPriority;
  pickup_address: string;
  pickup_lat: number;
  pickup_lon: number;
  delivery_address: string;
  delivery_lat: number;
  delivery_lon: number;
  depot_id?: string | null;
  weight_kg?: number | null;
  is_fragile: boolean;
  is_temp_sensitive: boolean;
  required_vehicle_tags?: VehicleTag[];
  length_cm?: number | null;
  width_cm?: number | null;
  height_cm?: number | null;
  volume_cm3?: number | null;
  customer_email: string;
  customer_phone?: string | null;
  external_ref_prefix?: string | null;
  custom_fields?: Record<string, unknown> | null;
  notes?: string | null;
  // Ride-specific fields (only meaningful when order_type === "RIDE")
  passenger_count?: number | null;
  passenger_name?: string | null;
  passenger_phone?: string | null;
  flight_info?: Record<string, unknown> | null;
  special_requirements?: string | null;
  pickup_buffer_minutes_override?: number | null;
  allow_split?: boolean;
  passengers_per_vehicle?: number | null;
  last_generated_for?: string | null;
  next_generation_at?: string | null;
  last_run_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface RecurringOrderTemplateCreate {
  name: string;
  description?: string;
  is_active?: boolean;
  cadence: RecurrenceCadence;
  interval_days?: number;
  day_of_week_mask?: number;
  day_of_month?: number | null;
  start_date: string;
  end_date?: string | null;
  max_occurrences?: number | null;
  delivery_window_start: string;
  delivery_window_end: string;
  pickup_window_start?: string | null;
  pickup_window_end?: string | null;
  lead_time_days?: number;
  order_type: OrderType;
  priority?: OrderPriority;
  pickup_address: string;
  pickup_lat: number;
  pickup_lon: number;
  delivery_address: string;
  delivery_lat: number;
  delivery_lon: number;
  depot_id?: string | null;
  weight_kg?: number | null;
  is_fragile?: boolean;
  is_temp_sensitive?: boolean;
  required_vehicle_tag_ids?: string[];
  length_cm?: number | null;
  width_cm?: number | null;
  height_cm?: number | null;
  volume_cm3?: number | null;
  customer_email: string;
  customer_phone?: string | null;
  external_ref_prefix?: string | null;
  custom_fields?: Record<string, unknown> | null;
  notes?: string | null;
  // Ride-specific fields (only meaningful when order_type === "RIDE")
  passenger_count?: number | null;
  passenger_name?: string | null;
  passenger_phone?: string | null;
  flight_info?: Record<string, unknown> | null;
  special_requirements?: string | null;
  pickup_buffer_minutes_override?: number | null;
  allow_split?: boolean;
  passengers_per_vehicle?: number | null;
}

export type RecurringOrderTemplateUpdate = Partial<RecurringOrderTemplateCreate>;

export interface RecurringOrderRunResponse {
  templates_evaluated: number;
  occurrences_created: number;
  occurrences_skipped_duplicate: number;
  occurrences_skipped_capped: number;
}

export interface RecurringOrderPreviewResponse {
  template_id: string;
  occurrence_dates: string[];
}

// ── Date-range dispatch ──

export interface AssignRangeResponse {
  task_id: string;
  message: string;
  span_days: number;
}

export interface RouteScenarioSummary {
  route_count: number;
  assigned_count: number;
  unassigned_count: number;
  vehicles_used: number;
  distance_km: number;
  duration_minutes: number;
  on_time_rate: number;
  late_minutes: number;
  realized_energy_cost_try: number;
  fuel_liters: number;
  electric_kwh: number;
  co2_kg?: number | null;
  solver_status: string;
  objective_breakdown?: Record<string, unknown> | null;
  alns_telemetry?: Record<string, unknown> | null;
  energy_price_sources: Array<{
    fuel_type: string;
    unit: string;
    price_try: number;
    effective_date: string;
    source_name: string;
    source_url: string;
    freshness_status: string;
    province_code?: string | null;
    province_name?: string | null;
  }>;
  energy_price_freshness_status: string;
  warnings: string[];
}

export interface RouteScenario {
  id: string;
  comparison_id: string;
  key: string;
  label: string;
  routing_objective: RoutingObjective;
  fleet_utilization: FleetUtilization;
  solver_kind: string;
  status: string;
  optimization_run_id?: string | null;
  summary?: RouteScenarioSummary | null;
  error_message?: string | null;
  created_at: string;
  completed_at?: string | null;
}

export interface RouteScenarioComparison {
  id: string;
  target_date: string;
  status: string;
  task_id?: string | null;
  selected_scenario_id?: string | null;
  current_policy_snapshot?: Record<string, unknown> | null;
  error_message?: string | null;
  created_at: string;
  completed_at?: string | null;
  applied_at?: string | null;
  scenarios: RouteScenario[];
}

export interface RouteScenarioCreateResponse {
  comparison: RouteScenarioComparison;
  task_id: string;
  message: string;
}

export interface RouteScenarioSelectResponse {
  comparison: RouteScenarioComparison;
  approved_routes: number;
  deleted_draft_routes: number;
}

export interface Depot {
  id: string;
  name: string;
  address: string;
  province_code?: string | null;
  province_name?: string | null;
  lat: number;
  lon: number;
  operating_hours_start: string;
  operating_hours_end: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface DepotCreate {
  name: string;
  address: string;
  province_code?: string | null;
  province_name?: string | null;
  lat: number;
  lon: number;
  operating_hours_start: string;
  operating_hours_end: string;
  is_active?: boolean;
}

export interface DepotUpdate {
  name?: string;
  address?: string;
  province_code?: string | null;
  province_name?: string | null;
  lat?: number;
  lon?: number;
  operating_hours_start?: string;
  operating_hours_end?: string;
  is_active?: boolean;
}

export type PackageStatus = "AT_DEPOT" | "LOADED" | "IN_TRANSIT" | "DELIVERED" | "RETURNED_TO_DEPOT" | "LOST";

export type OrderLegKind = "FIRST_MILE" | "LINEHAUL" | "LAST_MILE";
export type OrderLegStatus = "PENDING" | "PLANNED" | "IN_TRANSIT" | "COMPLETED" | "CANCELLED";

export interface OrderLeg {
  id: string;
  order_id: string;
  leg_kind: OrderLegKind;
  sequence_number: number;
  from_depot_id: string | null;
  to_depot_id: string | null;
  status: OrderLegStatus;
  planned_pickup_at: string | null;
  planned_delivery_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface OrderPackage {
  id: string;
  order_id: string;
  label: string;
  weight_kg: number;
  length_cm?: number | null;
  width_cm?: number | null;
  height_cm?: number | null;
  volume_cm3?: number | null;
  status: PackageStatus;
  created_at: string;
  updated_at: string;
}

export interface InventoryEvent {
  id: string;
  package_id: string;
  event_type: string;
  courier_id?: string | null;
  depot_id?: string | null;
  notes?: string | null;
  created_at: string;
}

export type Route = RouteContract;

export type RouteStop = RouteStopContract;

export type OptimizationRunPreview = RunPreviewContract;

export type OptimizationRunExplainability = RunPreviewExplainabilityContract;

export type OptimizationPreviewRoute = PreviewRouteContract;

export type OptimizationPreviewRouteExplainability = PreviewRouteExplainabilityContract;

export type OptimizationPreviewChangedOrder = PreviewChangedOrderContract;

export type OptimizationPreviewReason = PreviewExplainabilityItemContract;

export interface CourierLocation {
  courier_id: string;
  vehicle_type: VehicleType;
  availability_status: AvailabilityStatus;
  lat: number;
  lon: number;
  heading?: number | null;
  speed_kmh?: number | null;
  updated_at?: string | null;
  source: string;
}

export interface GeoJSONGeometry {
  type: string;
  coordinates: number[] | number[][] | number[][][];
}

export interface GeoJSONFeature {
  type: "Feature";
  geometry: GeoJSONGeometry;
  properties: Record<string, unknown>;
}

export interface GeoJSONFeatureCollection {
  type: "FeatureCollection";
  features: GeoJSONFeature[];
}

export interface RouteGeometryResponse {
  route_id: string;
  status: RouteStatus;
  source: string;
  generated_at: string;
  geometry: GeoJSONFeatureCollection;
}

export interface OptimizationRun {
  id: string;
  task_id?: string | null;
  run_type: string;
  status: string;
  routes_created?: number | null;
  unassigned_count?: number | null;
  solver_status?: string | null;
  started_at: string;
  finished_at?: string | null;
}

export interface ApiKeyRecord {
  id: string;
  name: string;
  permissions: string[];
  rate_limit_per_min: number;
  is_active: boolean;
  created_at: string;
}

export interface ApiKeyCreateResponse extends ApiKeyRecord {
  api_key: string;
}

export interface WebhookEndpoint {
  id: string;
  client_id: string;
  url: string;
  events: string[];
  is_active: boolean;
  created_at: string;
}

export interface WebhookEndpointCreatedResponse extends WebhookEndpoint {
  secret: string;
}

export interface WebhookDeliveryLog {
  id: string;
  webhook_endpoint_id: string;
  event_type: string;
  attempt_number: number;
  delivery_status: string;
  response_status?: number | null;
  error_message?: string | null;
  sent_at: string;
  next_retry_at?: string | null;
}

export interface OrderStatusHistoryItem {
  id: string;
  order_id: string;
  from_status?: string | null;
  to_status: string;
  actor_user_id?: string | null;
  reason?: string | null;
  created_at: string;
}

export type SLAAlertSeverity = "WARNING" | "CRITICAL";

export type SLAAlertStatus = "ACTIVE" | "ACKNOWLEDGED" | "RESOLVED";

export interface SLAAlert {
  id: string;
  route_id: string;
  stop_id: string | null;
  order_id: string | null;
  courier_id: string;
  severity: SLAAlertSeverity;
  status: SLAAlertStatus;
  alert_type: string;
  message: string;
  estimated_delay_seconds: number;
  created_at: string;
  updated_at: string;
  acknowledged_at: string | null;
}

export interface SLAScanResult {
  routes_scanned: number;
  alerts_created: number;
  alerts_resolved: number;
  reopt_triggered: number;
}

export interface ImportPreview {
  import_id: string;
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
}

export interface AuditLogEntry {
  id: string;
  tenant_id: string | null;
  actor_user_id: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  details: Record<string, unknown> | null;
  ip_address: string | null;
  created_at: string;
}

export interface TenantBranding {
  logo_url: string | null;
  primary_color: string | null;
  secondary_color: string | null;
}

export interface RbacOverride {
  id: string;
  role: string;
  permission: string;
  granted: boolean;
}
