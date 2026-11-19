/**
 * Screen-level logic tests for StopDetailScreen.
 *
 * These cover the helper and policy rules that determine what the screen shows
 * and which actions are available. They complement stopDetailHelpers.test.ts
 * with more complex policy interactions and edge cases.
 */

import {
  buildFailureReasonChoices,
  failureNoteRequired,
  getWaitTimeMinutes,
  requiresArrivalScan,
  requiresField,
  requiresPreArrivalCall,
  requiresReceiverNameForSafeDrop,
  shouldQueueOfflineOnError,
  suggestsPreArrivalCall,
  toErrorMessage,
} from "../stopDetailHelpers";
import type { StopActionPolicy } from "../../types";

// ── policy fixture helpers ────────────────────────────────────────────────────

function makePolicy(overrides: Partial<StopActionPolicy> = {}): StopActionPolicy {
  return {
    require_arrival_scan: false,
    pre_arrival_call: "OFF",
    wait_time_minutes: 5,
    safe_drop_policy: "ALLOWED",
    proof_of_delivery: {
      require_photo: false,
      photo_count: 1,
      require_signature: false,
      require_otp: false,
      receiver_name_mode: "OFF",
      notes_mode: "OFF",
    },
    failure_handling: {
      allowed_reasons: [],
      require_notes_for_other: true,
      post_failure_action: "DISPATCH_REVIEW",
      self_service_mode: "OFF",
    },
    ...overrides,
  };
}

// ── requiresArrivalScan ───────────────────────────────────────────────────────

describe("StopDetailScreen — requiresArrivalScan", () => {
  test("returns true when policy mandates arrival scan", () => {
    expect(requiresArrivalScan(makePolicy({ require_arrival_scan: true }))).toBe(true);
  });

  test("returns false when policy does not require scan", () => {
    expect(requiresArrivalScan(makePolicy({ require_arrival_scan: false }))).toBe(false);
  });

  test("returns false when policy is null or undefined", () => {
    expect(requiresArrivalScan(null)).toBe(false);
    expect(requiresArrivalScan(undefined)).toBe(false);
  });
});

// ── requiresPreArrivalCall / suggestsPreArrivalCall ───────────────────────────

describe("StopDetailScreen — pre-arrival call policy", () => {
  test("requiresPreArrivalCall is true only for REQUIRED mode", () => {
    expect(requiresPreArrivalCall(makePolicy({ pre_arrival_call: "REQUIRED" }))).toBe(true);
    expect(requiresPreArrivalCall(makePolicy({ pre_arrival_call: "SUGGESTED" }))).toBe(false);
    expect(requiresPreArrivalCall(makePolicy({ pre_arrival_call: "OFF" }))).toBe(false);
  });

  test("suggestsPreArrivalCall is true for both SUGGESTED and REQUIRED", () => {
    expect(suggestsPreArrivalCall(makePolicy({ pre_arrival_call: "SUGGESTED" }))).toBe(true);
    expect(suggestsPreArrivalCall(makePolicy({ pre_arrival_call: "REQUIRED" }))).toBe(true);
    expect(suggestsPreArrivalCall(makePolicy({ pre_arrival_call: "OFF" }))).toBe(false);
  });

  test("both return false when policy is missing", () => {
    expect(requiresPreArrivalCall(null)).toBe(false);
    expect(suggestsPreArrivalCall(null)).toBe(false);
  });
});

// ── getWaitTimeMinutes ────────────────────────────────────────────────────────

describe("StopDetailScreen — getWaitTimeMinutes", () => {
  test("returns policy-configured wait time", () => {
    expect(getWaitTimeMinutes(makePolicy({ wait_time_minutes: 15 }))).toBe(15);
  });

  test("returns 0 when policy is null", () => {
    expect(getWaitTimeMinutes(null)).toBe(0);
    expect(getWaitTimeMinutes(undefined)).toBe(0);
  });
});

// ── requiresReceiverNameForSafeDrop ───────────────────────────────────────────

describe("StopDetailScreen — requiresReceiverNameForSafeDrop", () => {
  test("returns true when safe drop is DISALLOWED (driver must confirm someone received)", () => {
    expect(requiresReceiverNameForSafeDrop(makePolicy({ safe_drop_policy: "DISALLOWED" }))).toBe(true);
  });

  test("returns false when safe drop is ALLOWED or SELECTED_CUSTOMERS", () => {
    expect(requiresReceiverNameForSafeDrop(makePolicy({ safe_drop_policy: "ALLOWED" }))).toBe(false);
    expect(requiresReceiverNameForSafeDrop(makePolicy({ safe_drop_policy: "SELECTED_CUSTOMERS" }))).toBe(false);
  });
});

// ── requiresField ────────────────────────────────────────────────────────────

describe("StopDetailScreen — requiresField", () => {
  test("returns true only for REQUIRED mode", () => {
    expect(requiresField("REQUIRED")).toBe(true);
    expect(requiresField("OPTIONAL")).toBe(false);
    expect(requiresField("OFF")).toBe(false);
    expect(requiresField(undefined)).toBe(false);
  });
});

// ── buildFailureReasonChoices ─────────────────────────────────────────────────

describe("StopDetailScreen — buildFailureReasonChoices", () => {
  test("returns default reasons when no policy is configured", () => {
    const choices = buildFailureReasonChoices(null);
    const keys = choices.map((c) => c.key);
    expect(keys).toContain("NOT_HOME");
    expect(keys).toContain("WRONG_ADDRESS");
    expect(keys).toContain("OTHER");
  });

  test("returns policy-configured reasons when provided", () => {
    const policy = makePolicy({
      failure_handling: {
        allowed_reasons: [
          { value: "NOT_HOME", label: "Nobody home", custom_reason_label: null },
          { value: "ACCESS_DENIED", label: "Cannot access", custom_reason_label: null },
        ],
        require_notes_for_other: false,
        post_failure_action: "DISPATCH_REVIEW",
        self_service_mode: "OFF",
      },
    });
    const choices = buildFailureReasonChoices(policy);
    expect(choices).toHaveLength(2);
    expect(choices[0].label).toBe("Nobody home");
    expect(choices[1].value).toBe("ACCESS_DENIED");
  });

  test("prefixes custom reason keys with 'custom:'", () => {
    const policy = makePolicy({
      failure_handling: {
        allowed_reasons: [
          { value: "OTHER", label: "Wrong size", custom_reason_label: "WRONG_SIZE" },
        ],
        require_notes_for_other: false,
        post_failure_action: "DISPATCH_REVIEW",
        self_service_mode: "OFF",
      },
    });
    const choices = buildFailureReasonChoices(policy);
    expect(choices[0].key).toBe("custom:WRONG_SIZE");
    expect(choices[0].customReasonLabel).toBe("WRONG_SIZE");
  });
});

// ── failureNoteRequired ───────────────────────────────────────────────────────

describe("StopDetailScreen — failureNoteRequired", () => {
  const otherChoice = {
    key: "OTHER",
    value: "OTHER",
    label: "Other",
    customReasonLabel: undefined,
  };

  test("requires note when choice is OTHER with no custom reason and require_notes_for_other is true", () => {
    const policy = makePolicy({
      failure_handling: {
        allowed_reasons: [],
        require_notes_for_other: true,
        post_failure_action: "DISPATCH_REVIEW",
        self_service_mode: "OFF",
      },
    });
    expect(failureNoteRequired(otherChoice, policy)).toBe(true);
  });

  test("does not require note when require_notes_for_other is false", () => {
    const policy = makePolicy({
      failure_handling: {
        allowed_reasons: [],
        require_notes_for_other: false,
        post_failure_action: "DISPATCH_REVIEW",
        self_service_mode: "OFF",
      },
    });
    expect(failureNoteRequired(otherChoice, policy)).toBe(false);
  });

  test("does not require note when choice has a custom reason label", () => {
    const customChoice = { key: "custom:WRONG_SIZE", value: "OTHER", label: "Wrong size", customReasonLabel: "WRONG_SIZE" };
    const policy = makePolicy({
      failure_handling: {
        allowed_reasons: [],
        require_notes_for_other: true,
        post_failure_action: "DISPATCH_REVIEW",
        self_service_mode: "OFF",
      },
    });
    expect(failureNoteRequired(customChoice, policy)).toBe(false);
  });

  test("does not require note when non-OTHER reason is selected", () => {
    const notHomeChoice = { key: "NOT_HOME", value: "NOT_HOME", label: "Not home", customReasonLabel: undefined };
    expect(failureNoteRequired(notHomeChoice, null)).toBe(false);
  });

  test("returns false when no choice is provided", () => {
    expect(failureNoteRequired(undefined, makePolicy())).toBe(false);
  });

  test("defaults require_notes_for_other to true when policy is null", () => {
    expect(failureNoteRequired(otherChoice, null)).toBe(true);
  });
});

// ── toErrorMessage ────────────────────────────────────────────────────────────

describe("StopDetailScreen — toErrorMessage", () => {
  test("extracts message from Error instances", () => {
    expect(toErrorMessage(new Error("Arrival failed"))).toBe("Arrival failed");
  });

  test("returns 'Unknown error' for non-Error values", () => {
    expect(toErrorMessage("string error")).toBe("Unknown error");
    expect(toErrorMessage(null)).toBe("Unknown error");
    expect(toErrorMessage({ code: 500 })).toBe("Unknown error");
  });
});
