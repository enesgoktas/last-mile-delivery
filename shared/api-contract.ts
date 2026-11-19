import type {
  components,
  operations,
  paths,
} from "./api-schema.generated";

type Schemas = components["schemas"];

export type ApiPaths = paths;
export type ApiOperations = operations;
export type ApiSchemas = Schemas;

export type VehicleType = Schemas["VehicleType"];
export type AvailabilityStatus = Schemas["AvailabilityStatus"];
export type PushProvider = Schemas["PushProvider"];
export type RouteStatus = Schemas["RouteStatus"];
export type StopType = Schemas["StopType"];
export type StopStatus = Schemas["StopStatus"];

export type FieldMode = Schemas["StopProofPolicyResponse"]["notes_mode"];
export type PreArrivalCallMode = Schemas["StopActionPolicyResponse-Output"]["pre_arrival_call"];
export type SafeDropPolicy = Schemas["StopActionPolicyResponse-Output"]["safe_drop_policy"];
export type FailureAction = Schemas["StopFailurePolicyResponse"]["post_failure_action"];
export type SelfServiceMode = Schemas["StopFailurePolicyResponse"]["self_service_mode"];
export type EtaVisibility = Schemas["TrackingResponse"]["eta_visibility"];

export type FailureReasonOption = Schemas["FailureReasonOptionResponse"];
export type StopProofPolicy = Schemas["StopProofPolicyResponse"];
export type StopFailurePolicy = Schemas["StopFailurePolicyResponse"];
export type StopActionPolicy = Schemas["StopActionPolicyResponse-Output"];

export type CourierProfileContract = Schemas["CourierResponse"];
export type PreviewChangedOrderContract = Schemas["PreviewChangedOrder"];
export type PreviewExplainabilityChangeType = Schemas["PreviewExplainabilityChangeType"];
export type PreviewExplainabilityItemContract = Schemas["PreviewExplainabilityItem"];
export type PreviewExplainabilityTone = Schemas["PreviewExplainabilityTone"];
export type PreviewOrderSummaryContract = Schemas["PreviewOrderSummary"];
export type PreviewRouteChangeType = Schemas["PreviewRouteChangeType"];
export type PreviewRouteContract = Schemas["PreviewRouteResponse-Output"];
export type PreviewRouteExplainabilityContract = Schemas["PreviewRouteExplainability-Output"];
export type PreviewStopContract = Schemas["PreviewStopResponse-Output"];
export type PreviewViolationContract = Schemas["PreviewViolation"];
export type RouteStopContract = Schemas["RouteStopResponse"];
export type RouteContract = Schemas["RouteResponse"];
export type RunPreviewContract = Schemas["RunPreviewResponse"];
export type RunPreviewExplainabilityContract = Schemas["RunPreviewExplainability-Output"];
export type TrackingResponseContract = Schemas["TrackingResponse"];
export type TokenResponseContract = Schemas["TokenResponse"];
export type LoginRequestContract = Schemas["LoginRequest"];
export type OrderCreateContract = Schemas["OrderCreate"];
export type OrderRescheduleContract = Schemas["OrderReschedule"];
export type TrackingRescheduleRequestContract = Schemas["TrackingRescheduleRequest"];
export type PushTokenUpdateContract = Schemas["PushTokenUpdate"];
export type AnalyticsSummaryContract = Schemas["AnalyticsSummaryResponse"];
export type OperationsPolicyContract = Schemas["OperationsPolicy-Output"];
export type OperationsPolicyInputContract = Schemas["OperationsPolicy-Input"];
export type PolicyPresetContract = Schemas["PolicyPreset-Output"];
export type TenantBrandingContract = Schemas["TenantBranding"];

export type FuelType = Schemas["FuelType"];
export type VehicleSpecContract = Schemas["VehicleSpecResponse"];
export type EPAMenuEntryContract = Schemas["EPAMenuEntry"];
export type EPASearchResultContract = Schemas["EPASearchResult"];
