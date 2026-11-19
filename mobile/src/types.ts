import type {
  CourierProfileContract,
  FailureAction,
  FailureReasonOption,
  FieldMode,
  PreArrivalCallMode,
  PushProvider,
  RouteContract,
  RouteStopContract,
  SafeDropPolicy,
  SelfServiceMode,
  StopActionPolicy,
  StopFailurePolicy,
  StopProofPolicy,
  StopStatus,
  StopType,
  VehicleType,
} from "../../shared/api-contract";

export type RootStackParamList = {
  Login: undefined;
  MyRoute: undefined;
  StopDetail: { routeId: string; stop: RouteStop; routeStatus?: RouteContract["status"] };
  LoadingChecklist: { routeId: string };
};

export type CourierProfile = CourierProfileContract;
export type RouteStop = RouteStopContract;
export type CourierRoute = RouteContract;

export type {
  FailureAction,
  FailureReasonOption,
  FieldMode,
  PreArrivalCallMode,
  PushProvider,
  SafeDropPolicy,
  SelfServiceMode,
  StopActionPolicy,
  StopFailurePolicy,
  StopProofPolicy,
  StopStatus,
  StopType,
  VehicleType,
};

export type PackageItem = {
  package_id: string;
  order_id: string;
  label: string;
  weight_kg: number;
  volume_cm3: number | null;
  status: string;
};

export type CourierCurrentLoad = {
  courier_id: string;
  total_weight_kg: number;
  total_volume_cm3: number;
  package_count: number;
  packages: PackageItem[];
};

export type OrderPackage = {
  id: string;
  order_id: string;
  label: string;
  weight_kg: number;
  length_cm: number | null;
  width_cm: number | null;
  height_cm: number | null;
  volume_cm3: number | null;
  status: string;
};
