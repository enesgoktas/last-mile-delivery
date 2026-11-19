import type { FieldMode, StopActionPolicy, StopType } from "../types";
import type { StopCompletionWithLocalPodPayload } from "../services/pod";

export type FailureReason =
  | "NOT_HOME"
  | "WRONG_ADDRESS"
  | "REFUSED"
  | "DAMAGED"
  | "ACCESS_DENIED"
  | "OTHER";

export type FailureReasonChoice = {
  key: string;
  value: string;
  label: string;
  customReasonLabel?: string | null;
};

export const FAILURE_REASONS: FailureReason[] = [
  "NOT_HOME",
  "WRONG_ADDRESS",
  "REFUSED",
  "DAMAGED",
  "ACCESS_DENIED",
  "OTHER",
];

export function buildFailureReasonChoices(
  actionPolicy?: StopActionPolicy | null,
): FailureReasonChoice[] {
  const configured = actionPolicy?.failure_handling.allowed_reasons;
  if (!configured?.length) {
    return FAILURE_REASONS.map((reason) => ({
      key: reason,
      value: reason,
      label: reason.replaceAll("_", " "),
    }));
  }

  return configured.map((reason) => ({
    key: reason.custom_reason_label ? `custom:${reason.custom_reason_label}` : reason.value,
    value: reason.value,
    label: reason.label,
    customReasonLabel: reason.custom_reason_label,
  }));
}

export function requiresField(mode: FieldMode | undefined): boolean {
  return mode === "REQUIRED";
}

export function failureNoteRequired(
  choice: FailureReasonChoice | undefined,
  actionPolicy?: StopActionPolicy | null,
): boolean {
  if (!choice) {
    return false;
  }
  return (
    choice.value === "OTHER" &&
    !choice.customReasonLabel &&
    Boolean(actionPolicy?.failure_handling.require_notes_for_other ?? true)
  );
}

export function requiresArrivalScan(policy?: StopActionPolicy | null): boolean {
  return policy?.require_arrival_scan === true;
}

export function requiresPreArrivalCall(policy?: StopActionPolicy | null): boolean {
  return policy?.pre_arrival_call === "REQUIRED";
}

export function suggestsPreArrivalCall(policy?: StopActionPolicy | null): boolean {
  const mode = policy?.pre_arrival_call;
  return mode === "SUGGESTED" || mode === "REQUIRED";
}

export function getWaitTimeMinutes(policy?: StopActionPolicy | null): number {
  return policy?.wait_time_minutes ?? 0;
}

/** When safe_drop is DISALLOWED the driver must capture receiver name regardless of receiver_name_mode. */
export function requiresReceiverNameForSafeDrop(policy?: StopActionPolicy | null): boolean {
  return policy?.safe_drop_policy === "DISALLOWED";
}

export function shouldQueueOfflineOnError(error: unknown): boolean {
  if (error instanceof Error && typeof (error as { status?: unknown }).status === "number") {
    const status = (error as unknown as { status: number }).status;
    return status === 0 || status >= 500 || status === 429 || status === 408;
  }
  if (!(error instanceof Error)) {
    return false;
  }
  const message = error.message.toLowerCase();
  return (
    message.includes("cannot reach api") ||
    message.includes("network request failed") ||
    message.includes("network error") ||
    message.includes("failed to fetch") ||
    message.includes("timeout")
  );
}

export function toErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Unknown error";
}

export function supportsPodMedia(stopType: StopType): boolean {
  return stopType === "DELIVERY";
}

export function sanitizeCompletionPayloadForStop(
  stopType: StopType,
  payload: StopCompletionWithLocalPodPayload,
): StopCompletionWithLocalPodPayload {
  if (supportsPodMedia(stopType)) {
    return payload;
  }
  return {};
}
