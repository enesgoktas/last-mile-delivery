import {
  buildFailureReasonChoices,
  failureNoteRequired,
  FAILURE_REASONS,
  getWaitTimeMinutes,
  requiresArrivalScan,
  requiresField,
  requiresPreArrivalCall,
  requiresReceiverNameForSafeDrop,
  sanitizeCompletionPayloadForStop,
  shouldQueueOfflineOnError,
  supportsPodMedia,
  suggestsPreArrivalCall,
  toErrorMessage,
} from "../stopDetailHelpers";

class MockApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

describe("stopDetailHelpers", () => {
  test("exposes expected failure reasons", () => {
    expect(FAILURE_REASONS).toEqual([
      "NOT_HOME",
      "WRONG_ADDRESS",
      "REFUSED",
      "DAMAGED",
      "ACCESS_DENIED",
      "OTHER",
    ]);
  });

  test("queues offline for non-API errors", () => {
    expect(shouldQueueOfflineOnError(new Error("network timeout"))).toBe(true);
    expect(shouldQueueOfflineOnError(new Error("Network request failed"))).toBe(true);
    expect(shouldQueueOfflineOnError(new Error("Cannot reach API at http://localhost:8000"))).toBe(true);
  });

  test("does not queue offline for non-network unknown errors", () => {
    expect(shouldQueueOfflineOnError(new Error("validation failed"))).toBe(false);
    expect(shouldQueueOfflineOnError("unexpected string error")).toBe(false);
  });

  test("builds configured custom failure reasons", () => {
    expect(
      buildFailureReasonChoices({
        require_arrival_scan: true,
        pre_arrival_call: "SUGGESTED",
        wait_time_minutes: 10,
        safe_drop_policy: "ALLOWED",
        proof_of_delivery: {
          require_photo: true,
          photo_count: 1,
          require_signature: false,
          require_otp: false,
          receiver_name_mode: "OPTIONAL",
          notes_mode: "OPTIONAL",
        },
        failure_handling: {
          allowed_reasons: [
            { value: "NOT_HOME", label: "Customer not home" },
            { value: "OTHER", label: "Gate locked", custom_reason_label: "Gate locked" },
          ],
          require_notes_for_other: true,
          post_failure_action: "CUSTOMER_RESCHEDULE",
          self_service_mode: "RESCHEDULE_ONLY",
        },
      }),
    ).toEqual([
      { key: "NOT_HOME", value: "NOT_HOME", label: "Customer not home", customReasonLabel: undefined },
      { key: "custom:Gate locked", value: "OTHER", label: "Gate locked", customReasonLabel: "Gate locked" },
    ]);
  });

  test("marks only required field modes as mandatory", () => {
    expect(requiresField("REQUIRED")).toBe(true);
    expect(requiresField("OPTIONAL")).toBe(false);
    expect(requiresField("OFF")).toBe(false);
  });

  test("requires failure note only for uncategorized OTHER reasons", () => {
    expect(
      failureNoteRequired(
        { key: "OTHER", value: "OTHER", label: "Other" },
        {
          require_arrival_scan: true,
          pre_arrival_call: "SUGGESTED",
          wait_time_minutes: 10,
          safe_drop_policy: "ALLOWED",
          proof_of_delivery: {
            require_photo: true,
            photo_count: 1,
            require_signature: false,
            require_otp: false,
            receiver_name_mode: "OPTIONAL",
            notes_mode: "OPTIONAL",
          },
          failure_handling: {
            allowed_reasons: [],
            require_notes_for_other: true,
            post_failure_action: "CUSTOMER_RESCHEDULE",
            self_service_mode: "RESCHEDULE_ONLY",
          },
        },
      ),
    ).toBe(true);
    expect(
      failureNoteRequired(
        { key: "custom:Gate locked", value: "OTHER", label: "Gate locked", customReasonLabel: "Gate locked" },
        {
          require_arrival_scan: true,
          pre_arrival_call: "SUGGESTED",
          wait_time_minutes: 10,
          safe_drop_policy: "ALLOWED",
          proof_of_delivery: {
            require_photo: true,
            photo_count: 1,
            require_signature: false,
            require_otp: false,
            receiver_name_mode: "OPTIONAL",
            notes_mode: "OPTIONAL",
          },
          failure_handling: {
            allowed_reasons: [],
            require_notes_for_other: true,
            post_failure_action: "CUSTOMER_RESCHEDULE",
            self_service_mode: "RESCHEDULE_ONLY",
          },
        },
      ),
    ).toBe(false);
  });

  test("queues offline for retryable API errors", () => {
    expect(shouldQueueOfflineOnError(new MockApiError("backend unreachable", 0))).toBe(true);
    expect(shouldQueueOfflineOnError(new MockApiError("server error", 500))).toBe(true);
    expect(shouldQueueOfflineOnError(new MockApiError("too many requests", 429))).toBe(true);
    expect(shouldQueueOfflineOnError(new MockApiError("request timeout", 408))).toBe(true);
  });

  test("does not queue offline for non-retryable API errors", () => {
    expect(shouldQueueOfflineOnError(new MockApiError("invalid transition", 400))).toBe(false);
    expect(shouldQueueOfflineOnError(new MockApiError("forbidden", 403))).toBe(false);
  });

  test("formats error messages safely", () => {
    expect(toErrorMessage(new Error("boom"))).toBe("boom");
    expect(toErrorMessage("unknown")).toBe("Unknown error");
  });

  // ── Policy enforcement helpers ──────────────────────────────────────────

  const fullPolicy = {
    require_arrival_scan: true,
    pre_arrival_call: "REQUIRED" as const,
    wait_time_minutes: 5,
    safe_drop_policy: "DISALLOWED" as const,
    proof_of_delivery: {
      require_photo: true,
      photo_count: 1,
      require_signature: false,
      require_otp: false,
      receiver_name_mode: "OPTIONAL" as const,
      notes_mode: "OPTIONAL" as const,
    },
    failure_handling: {
      allowed_reasons: [],
      require_notes_for_other: true,
      post_failure_action: "CUSTOMER_RESCHEDULE" as const,
      self_service_mode: "RESCHEDULE_ONLY" as const,
    },
  };

  test("requiresArrivalScan returns true when policy is set", () => {
    expect(requiresArrivalScan(fullPolicy)).toBe(true);
    expect(requiresArrivalScan({ ...fullPolicy, require_arrival_scan: false })).toBe(false);
    expect(requiresArrivalScan(null)).toBe(false);
    expect(requiresArrivalScan(undefined)).toBe(false);
  });

  test("requiresPreArrivalCall returns true only for REQUIRED", () => {
    expect(requiresPreArrivalCall(fullPolicy)).toBe(true);
    expect(requiresPreArrivalCall({ ...fullPolicy, pre_arrival_call: "SUGGESTED" })).toBe(false);
    expect(requiresPreArrivalCall({ ...fullPolicy, pre_arrival_call: "OFF" })).toBe(false);
    expect(requiresPreArrivalCall(null)).toBe(false);
  });

  test("suggestsPreArrivalCall returns true for SUGGESTED and REQUIRED", () => {
    expect(suggestsPreArrivalCall(fullPolicy)).toBe(true);
    expect(suggestsPreArrivalCall({ ...fullPolicy, pre_arrival_call: "SUGGESTED" })).toBe(true);
    expect(suggestsPreArrivalCall({ ...fullPolicy, pre_arrival_call: "OFF" })).toBe(false);
    expect(suggestsPreArrivalCall(null)).toBe(false);
  });

  test("getWaitTimeMinutes returns policy value or 0 when absent", () => {
    expect(getWaitTimeMinutes(fullPolicy)).toBe(5);
    expect(getWaitTimeMinutes({ ...fullPolicy, wait_time_minutes: 0 })).toBe(0);
    expect(getWaitTimeMinutes(null)).toBe(0);
    expect(getWaitTimeMinutes(undefined)).toBe(0);
  });

  test("requiresReceiverNameForSafeDrop returns true only for DISALLOWED", () => {
    expect(requiresReceiverNameForSafeDrop(fullPolicy)).toBe(true);
    expect(requiresReceiverNameForSafeDrop({ ...fullPolicy, safe_drop_policy: "ALLOWED" })).toBe(false);
    expect(requiresReceiverNameForSafeDrop({ ...fullPolicy, safe_drop_policy: "SELECTED_CUSTOMERS" })).toBe(false);
    expect(requiresReceiverNameForSafeDrop(null)).toBe(false);
  });

  test("supports pod media only for delivery stops", () => {
    expect(supportsPodMedia("DELIVERY")).toBe(true);
    expect(supportsPodMedia("PICKUP")).toBe(false);
    expect(supportsPodMedia("DEPOT_RETURN")).toBe(false);
    expect(supportsPodMedia("DEPOT_RELOAD")).toBe(false);
  });

  test("sanitizes completion payload for non-delivery stops", () => {
    const payload = sanitizeCompletionPayloadForStop("PICKUP", {
      notes: "left at desk",
      receiver_name: "Ali",
      otp_code: "1234",
      photo_local_uris: ["file:///photo.jpg"],
      signature_local_uri: "file:///signature.png",
    });
    expect(payload).toEqual({});
  });
});
