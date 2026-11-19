import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../../state/AuthContext";
import { useToast } from "../../state/ToastContext";
import { LoadingSpinner } from "../../components/LoadingSpinner";
import {
  applyOperationsPolicyPreset,
  getOperationsPolicy,
  listOperationsPolicyPresets,
  saveOperationsPolicy,
} from "../../api/client";
import type {
  CustomOrderFieldDefinition,
  EtaVisibility,
  FieldMode,
  FleetUtilization,
  OperationsPolicy,
  OrderType,
  PolicyPreset,
  RolePermissionSet,
  RoutingObjective,
  SafeDropPolicy,
  SelfServiceMode,
  VisibilityMode,
} from "../../types";
import { SETTINGS_INPUT_CLASSNAME } from "./shared";

const ORDER_TYPES: OrderType[] = ["ON_DEMAND", "SCHEDULED", "BULK", "REVERSE"];
const ROLES = ["OWNER", "ADMIN", "DISPATCHER", "COURIER"] as const;
const FIELD_MODE_OPTIONS: FieldMode[] = ["OFF", "OPTIONAL", "REQUIRED"];
const VISIBILITY_OPTIONS: VisibilityMode[] = ["HIDDEN", "OPTIONAL", "REQUIRED"];
const SAFE_DROP_OPTIONS: SafeDropPolicy[] = ["DISALLOWED", "ALLOWED", "SELECTED_CUSTOMERS"];
const SELF_SERVICE_OPTIONS: SelfServiceMode[] = ["OFF", "RESCHEDULE_ONLY", "RESCHEDULE_AND_INSTRUCTIONS"];
const ETA_OPTIONS: EtaVisibility[] = ["NONE", "ETA_RANGE", "LIVE_MAP"];
const ROUTING_OBJECTIVE_OPTIONS: Array<{
  value: RoutingObjective;
  label: string;
  description: string;
}> = [
  {
    value: "FUEL_AWARE",
    label: "Fuel-aware",
    description: "Minimize realized operating cost while preserving hard delivery commitments.",
  },
  {
    value: "SERVICE_FIRST",
    label: "Service-first",
    description: "Prioritize delivery windows and lateness penalties; fuel is still reported.",
  },
];
const FLEET_UTILIZATION_OPTIONS: Array<{
  value: FleetUtilization;
  label: string;
  description: string;
}> = [
  {
    value: "FULL_FLEET",
    label: "Full Fleet",
    description: "Distribute orders across all available couriers so everyone is working.",
  },
  {
    value: "EFFICIENT",
    label: "Efficient",
    description: "Use the fewest couriers needed. Remaining couriers stay idle.",
  },
];
const STATUS_KEYS = [
  "DRAFT",
  "PENDING",
  "ASSIGNED",
  "PICKED_UP",
  "IN_TRANSIT",
  "DELIVERED",
  "FAILED",
  "RESCHEDULED",
  "CANCELLED",
] as const;

function clonePolicy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function FieldGroup({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      {children}
      {hint ? <p className="text-xs text-gray-400 mt-1">{hint}</p> : null}
    </div>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="bg-white rounded-xl border border-gray-200 p-6 space-y-5">
      <div>
        <h2 className="text-base font-semibold text-gray-900">{title}</h2>
        <p className="text-sm text-gray-500 mt-1">{description}</p>
      </div>
      {children}
    </section>
  );
}

function PermissionToggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <input
      type="checkbox"
      checked={checked}
      onChange={(event) => onChange(event.target.checked)}
      className="rounded border-gray-300"
    />
  );
}

function PodRequirementEditor({
  title,
  value,
  onChange,
}: {
  title: string;
  value: OperationsPolicy["proof_of_delivery"]["defaults"];
  onChange: (next: OperationsPolicy["proof_of_delivery"]["defaults"]) => void;
}) {
  const update = <K extends keyof OperationsPolicy["proof_of_delivery"]["defaults"]>(
    key: K,
    nextValue: OperationsPolicy["proof_of_delivery"]["defaults"][K],
  ) => onChange({ ...value, [key]: nextValue });

  return (
    <div className="rounded-xl border border-gray-200 p-4 space-y-3">
      <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
      <div className="grid grid-cols-2 gap-4">
        <label className="flex items-center gap-2 text-sm text-gray-700">
          <input type="checkbox" checked={value.require_photo} onChange={(e) => update("require_photo", e.target.checked)} />
          Require photo
        </label>
        <FieldGroup label="Photo count">
          <input
            type="number"
            min={0}
            max={5}
            value={value.photo_count}
            onChange={(e) => update("photo_count", Number(e.target.value))}
            className={SETTINGS_INPUT_CLASSNAME}
          />
        </FieldGroup>
        <label className="flex items-center gap-2 text-sm text-gray-700">
          <input type="checkbox" checked={value.require_signature} onChange={(e) => update("require_signature", e.target.checked)} />
          Require signature
        </label>
        <label className="flex items-center gap-2 text-sm text-gray-700">
          <input type="checkbox" checked={value.require_otp} onChange={(e) => update("require_otp", e.target.checked)} />
          Require OTP
        </label>
        <FieldGroup label="Receiver name">
          <select
            value={value.receiver_name_mode}
            onChange={(e) => update("receiver_name_mode", e.target.value as FieldMode)}
            className={SETTINGS_INPUT_CLASSNAME}
          >
            {FIELD_MODE_OPTIONS.map((mode) => <option key={mode} value={mode}>{mode}</option>)}
          </select>
        </FieldGroup>
        <FieldGroup label="Proof notes">
          <select
            value={value.notes_mode}
            onChange={(e) => update("notes_mode", e.target.value as FieldMode)}
            className={SETTINGS_INPUT_CLASSNAME}
          >
            {FIELD_MODE_OPTIONS.map((mode) => <option key={mode} value={mode}>{mode}</option>)}
          </select>
        </FieldGroup>
      </div>
    </div>
  );
}

export function OperationsPolicyTab() {
  const { token } = useAuth();
  const { addToast } = useToast();
  const [policy, setPolicy] = useState<OperationsPolicy | null>(null);
  const [presets, setPresets] = useState<PolicyPreset[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const [policyResponse, presetResponse] = await Promise.all([
        getOperationsPolicy(token!),
        listOperationsPolicyPresets(token!),
      ]);
      setPolicy(policyResponse);
      setPresets(presetResponse);
    } catch (error) {
      addToast(error instanceof Error ? error.message : "Failed to load operations settings", "error");
    } finally {
      setLoading(false);
    }
  }, [token, addToast]);

  useEffect(() => {
    void load();
  }, [load]);

  const updatePolicy = useCallback((mutator: (draft: OperationsPolicy) => void) => {
    setPolicy((prev) => {
      if (!prev) return prev;
      const next = clonePolicy(prev);
      mutator(next);
      return next;
    });
  }, []);

  const applyRoutingObjective = useCallback((draft: OperationsPolicy, objective: RoutingObjective) => {
    draft.optimization.routing_objective = objective;
    draft.optimization.economic_batch_optimizer_enabled = true;
    draft.optimization.shadow_mode_enabled = false;
    draft.optimization.internal_override_enabled = false;
    if (objective === "SERVICE_FIRST") {
      draft.optimization.preset_key = "SERVICE_FIRST";
      draft.optimization.fuel_priority_multiplier = 0;
      return;
    }
    draft.optimization.preset_key = "COST_SAVER";
    draft.optimization.fuel_priority_multiplier = 1;
  }, []);

  async function handleSave() {
    if (!policy) return;
    setSaving(true);
    try {
      const saved = await saveOperationsPolicy(token!, policy);
      setPolicy(saved);
      addToast("Operations policy saved", "success");
    } catch (error) {
      addToast(error instanceof Error ? error.message : "Failed to save operations policy", "error");
    } finally {
      setSaving(false);
    }
  }

  async function handleApplyPreset(presetKey: string) {
    setSaving(true);
    try {
      const updated = await applyOperationsPolicyPreset(token!, presetKey);
      setPolicy(updated);
      addToast("Preset applied", "success");
    } catch (error) {
      addToast(error instanceof Error ? error.message : "Failed to apply preset", "error");
    } finally {
      setSaving(false);
    }
  }

  const eventKeys = useMemo(
    () => Object.keys(policy?.notifications.event_channels ?? {}).sort(),
    [policy],
  );
  if (loading || !policy) {
    return <LoadingSpinner label="Loading operations settings…" />;
  }

  return (
    <div className="max-w-6xl space-y-6">
      <Section
        title="Industry Presets"
        description="Start from a process template for your business, then fine-tune the details below."
      >
        <div className="grid md:grid-cols-2 gap-4">
          {presets.map((preset) => (
            <div key={preset.key} className="rounded-xl border border-gray-200 p-4 flex flex-col gap-3">
              <div>
                <h3 className="text-sm font-semibold text-gray-900">{preset.label}</h3>
                <p className="text-sm text-gray-500 mt-1">{preset.description}</p>
              </div>
              <button
                type="button"
                disabled={saving}
                onClick={() => void handleApplyPreset(preset.key)}
                className="self-start px-3 py-2 text-sm font-medium text-brand-700 bg-brand-50 rounded-lg hover:bg-brand-100 disabled:opacity-50"
              >
                Apply preset
              </button>
            </div>
          ))}
        </div>
      </Section>

      <Section
        title="Dispatch"
        description="Choose how orders are assigned and whether routes require manual approval."
      >
        <div className="grid md:grid-cols-3 gap-4">
          <FieldGroup label="Dispatch mode">
            <select
              value={policy.dispatch.dispatch_mode}
              onChange={(e) => updatePolicy((draft) => { draft.dispatch.dispatch_mode = e.target.value as OperationsPolicy["dispatch"]["dispatch_mode"]; })}
              className={SETTINGS_INPUT_CLASSNAME}
            >
              <option value="AUTOMATIC">Automatic</option>
              <option value="MANUAL">Manual</option>
              <option value="HYBRID">Hybrid</option>
            </select>
          </FieldGroup>
          <FieldGroup label="Assignment strategy">
            <select
              value={policy.dispatch.assignment_strategy}
              onChange={(e) => updatePolicy((draft) => { draft.dispatch.assignment_strategy = e.target.value as OperationsPolicy["dispatch"]["assignment_strategy"]; })}
              className={SETTINGS_INPUT_CLASSNAME}
            >
              <option value="NEAREST_COURIER">Nearest courier</option>
              <option value="LEAST_BUSY">Least busy courier</option>
              <option value="ZONE_BASED">Zone-based</option>
              <option value="PREFERRED_COURIER">Preferred courier</option>
            </select>
          </FieldGroup>
          <FieldGroup label="Route approval">
            <select
              value={policy.dispatch.route_approval.mode}
              onChange={(e) => updatePolicy((draft) => { draft.dispatch.route_approval.mode = e.target.value as OperationsPolicy["dispatch"]["route_approval"]["mode"]; })}
              className={SETTINGS_INPUT_CLASSNAME}
            >
              <option value="NONE">No approval</option>
              <option value="LARGE_ROUTES_ONLY">Large routes only</option>
              <option value="ALL_ROUTES">All routes</option>
            </select>
          </FieldGroup>
        </div>
        <FieldGroup label="Large-route threshold (stops)" hint="Used when route approval is set to large routes only.">
          <input
            type="number"
            min={2}
            max={200}
            value={policy.dispatch.route_approval.large_route_stop_threshold}
            onChange={(e) => updatePolicy((draft) => { draft.dispatch.route_approval.large_route_stop_threshold = Number(e.target.value); })}
            className={SETTINGS_INPUT_CLASSNAME}
          />
        </FieldGroup>
      </Section>

      <Section
        title="Batch Optimization"
        description="Choose the two public planning knobs used by daily batch routing."
      >
        <FieldGroup
          label="Routing objective"
          hint="Fuel-aware controls the optimizer objective; realized fuel and electric cost is still reported for both choices."
        >
          <div className="grid md:grid-cols-2 gap-3">
            {ROUTING_OBJECTIVE_OPTIONS.map((option) => {
              const active = policy.optimization.routing_objective === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => updatePolicy((draft) => {
                    applyRoutingObjective(draft, option.value);
                  })}
                  className={[
                    "text-left rounded-xl border px-4 py-3 transition-colors duration-100",
                    active
                      ? "border-brand-400 bg-brand-50/60"
                      : "border-gray-200 bg-white hover:border-gray-300",
                  ].join(" ")}
                >
                  <p className={`text-sm font-semibold ${active ? "text-brand-700" : "text-gray-900"}`}>
                    {option.label}
                  </p>
                  <p className="mt-0.5 text-xs text-gray-500">{option.description}</p>
                </button>
              );
            })}
          </div>
        </FieldGroup>
        <FieldGroup
          label="Fleet utilization"
          hint="Controls how the optimizer distributes orders across your courier fleet."
        >
          <div className="grid md:grid-cols-2 gap-3">
            {FLEET_UTILIZATION_OPTIONS.map((opt) => {
              const active = policy.optimization.fleet_utilization === opt.value;
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => updatePolicy((draft) => {
                    draft.optimization.fleet_utilization = opt.value;
                  })}
                  className={[
                    "text-left rounded-xl border px-4 py-3 transition-colors duration-100",
                    active
                      ? "border-brand-400 bg-brand-50/60"
                      : "border-gray-200 bg-white hover:border-gray-300",
                  ].join(" ")}
                >
                  <p className={`text-sm font-semibold ${active ? "text-brand-700" : "text-gray-900"}`}>
                    {opt.label}
                  </p>
                  <p className="mt-0.5 text-xs text-gray-500">{opt.description}</p>
                </button>
              );
            })}
          </div>
        </FieldGroup>
      </Section>

      <Section
        title="Delivery Workflow"
        description="Control what drivers must do at the stop and how customers see statuses."
      >
        <div className="grid md:grid-cols-2 gap-4">
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={policy.delivery_workflow.require_pickup_step}
              onChange={(e) => updatePolicy((draft) => { draft.delivery_workflow.require_pickup_step = e.target.checked; })}
            />
            Require pickup step
          </label>
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={policy.delivery_workflow.require_arrival_scan}
              onChange={(e) => updatePolicy((draft) => { draft.delivery_workflow.require_arrival_scan = e.target.checked; })}
            />
            Require arrival confirmation before completion
          </label>
          <FieldGroup label="Pre-arrival call">
            <select
              value={policy.delivery_workflow.pre_arrival_call}
              onChange={(e) => updatePolicy((draft) => { draft.delivery_workflow.pre_arrival_call = e.target.value as OperationsPolicy["delivery_workflow"]["pre_arrival_call"]; })}
              className={SETTINGS_INPUT_CLASSNAME}
            >
              <option value="OFF">Off</option>
              <option value="SUGGESTED">Suggested</option>
              <option value="REQUIRED">Required</option>
            </select>
          </FieldGroup>
          <FieldGroup label="Wait time (minutes)">
            <input
              type="number"
              min={0}
              max={180}
              value={policy.delivery_workflow.wait_time_minutes}
              onChange={(e) => updatePolicy((draft) => { draft.delivery_workflow.wait_time_minutes = Number(e.target.value); })}
              className={SETTINGS_INPUT_CLASSNAME}
            />
          </FieldGroup>
          <FieldGroup label="Safe-drop policy">
            <select
              value={policy.delivery_workflow.safe_drop_policy}
              onChange={(e) => updatePolicy((draft) => {
                const nextValue: OperationsPolicy["delivery_workflow"]["safe_drop_policy"] =
                  e.target.value as OperationsPolicy["delivery_workflow"]["safe_drop_policy"];
                draft.delivery_workflow.safe_drop_policy = nextValue;
              })}
              className={SETTINGS_INPUT_CLASSNAME}
            >
              {SAFE_DROP_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          </FieldGroup>
        </div>
        <div className="grid md:grid-cols-3 gap-4">
          {STATUS_KEYS.map((statusKey) => (
            <FieldGroup key={statusKey} label={`${statusKey} label`}>
              <input
                type="text"
                value={policy.delivery_workflow.customer_status_labels[statusKey] ?? ""}
                onChange={(e) => updatePolicy((draft) => { draft.delivery_workflow.customer_status_labels[statusKey] = e.target.value; })}
                className={SETTINGS_INPUT_CLASSNAME}
              />
            </FieldGroup>
          ))}
        </div>
      </Section>

      <Section
        title="Proof of Delivery"
        description="Define the default proof requirements and override them per order type."
      >
        <PodRequirementEditor
          title="Default proof requirements"
          value={policy.proof_of_delivery.defaults}
          onChange={(next) => updatePolicy((draft) => { draft.proof_of_delivery.defaults = next; })}
        />
        <div className="grid xl:grid-cols-2 gap-4">
          {ORDER_TYPES.map((orderType) => (
            <PodRequirementEditor
              key={orderType}
              title={`${orderType.replace("_", " ")} override`}
              value={policy.proof_of_delivery.by_order_type[orderType] ?? clonePolicy(policy.proof_of_delivery.defaults)}
              onChange={(next) => updatePolicy((draft) => { draft.proof_of_delivery.by_order_type[orderType] = next; })}
            />
          ))}
        </div>
      </Section>

      <Section
        title="Failed Delivery Handling"
        description="Choose allowed failure reasons, retry behavior, and what customers can do after a failed attempt."
      >
        <div className="grid md:grid-cols-3 gap-4">
          <FieldGroup label="Post-failure action">
            <select
              value={policy.failure_handling.post_failure_action}
              onChange={(e) => updatePolicy((draft) => { draft.failure_handling.post_failure_action = e.target.value as OperationsPolicy["failure_handling"]["post_failure_action"]; })}
              className={SETTINGS_INPUT_CLASSNAME}
            >
              <option value="DISPATCH_REVIEW">Dispatch review</option>
              <option value="AUTOMATIC_RETRY">Automatic retry</option>
              <option value="RETURN_TO_DEPOT">Return to depot</option>
              <option value="CUSTOMER_RESCHEDULE">Customer reschedule</option>
            </select>
          </FieldGroup>
          <FieldGroup label="Retry timing">
            <select
              value={policy.failure_handling.retry_timing}
              onChange={(e) => updatePolicy((draft) => { draft.failure_handling.retry_timing = e.target.value as OperationsPolicy["failure_handling"]["retry_timing"]; })}
              className={SETTINGS_INPUT_CLASSNAME}
            >
              <option value="SAME_DAY">Same day</option>
              <option value="NEXT_DAY">Next day</option>
              <option value="MANUAL_ONLY">Manual only</option>
            </select>
          </FieldGroup>
          <FieldGroup label="Customer self-service">
            <select
              value={policy.failure_handling.self_service_mode}
              onChange={(e) => updatePolicy((draft) => {
                const nextValue: OperationsPolicy["failure_handling"]["self_service_mode"] =
                  e.target.value as OperationsPolicy["failure_handling"]["self_service_mode"];
                draft.failure_handling.self_service_mode = nextValue;
              })}
              className={SETTINGS_INPUT_CLASSNAME}
            >
              {SELF_SERVICE_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          </FieldGroup>
          <FieldGroup label="Max attempts">
            <input
              type="number"
              min={1}
              max={10}
              value={policy.failure_handling.max_attempts}
              onChange={(e) => updatePolicy((draft) => { draft.failure_handling.max_attempts = Number(e.target.value); })}
              className={SETTINGS_INPUT_CLASSNAME}
            />
          </FieldGroup>
          <FieldGroup label="Escalation window (hours)">
            <input
              type="number"
              min={1}
              max={168}
              value={policy.failure_handling.escalation_hours}
              onChange={(e) => updatePolicy((draft) => { draft.failure_handling.escalation_hours = Number(e.target.value); })}
              className={SETTINGS_INPUT_CLASSNAME}
            />
          </FieldGroup>
          <label className="flex items-center gap-2 text-sm text-gray-700 mt-7">
            <input
              type="checkbox"
              checked={policy.failure_handling.require_notes_for_other}
              onChange={(e) => updatePolicy((draft) => { draft.failure_handling.require_notes_for_other = e.target.checked; })}
            />
            Require note when reason is OTHER
          </label>
        </div>
        <FieldGroup label="Custom failure reasons" hint="Comma-separated labels shown to drivers as workspace-specific failure options.">
          <textarea
            value={policy.failure_handling.custom_reasons.join(", ")}
            onChange={(e) => updatePolicy((draft) => {
              draft.failure_handling.custom_reasons = e.target.value
                .split(",")
                .map((value) => value.trim())
                .filter(Boolean);
            })}
            className={`${SETTINGS_INPUT_CLASSNAME} min-h-[72px]`}
          />
        </FieldGroup>
      </Section>

      <Section
        title="Customer Communication"
        description="Control ETA visibility, quiet hours, branding, and which channels fire for each delivery event."
      >
        <div className="grid md:grid-cols-3 gap-4">
          <FieldGroup label="Brand name">
            <input
              type="text"
              value={policy.notifications.branding_name}
              onChange={(e) => updatePolicy((draft) => { draft.notifications.branding_name = e.target.value; })}
              className={SETTINGS_INPUT_CLASSNAME}
            />
          </FieldGroup>
          <FieldGroup label="Language">
            <input
              type="text"
              value={policy.notifications.language}
              onChange={(e) => updatePolicy((draft) => { draft.notifications.language = e.target.value; })}
              className={SETTINGS_INPUT_CLASSNAME}
            />
          </FieldGroup>
          <FieldGroup label="ETA visibility">
            <select
              value={policy.notifications.eta_visibility}
              onChange={(e) => updatePolicy((draft) => {
                const nextValue: OperationsPolicy["notifications"]["eta_visibility"] =
                  e.target.value as OperationsPolicy["notifications"]["eta_visibility"];
                draft.notifications.eta_visibility = nextValue;
              })}
              className={SETTINGS_INPUT_CLASSNAME}
            >
              {ETA_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          </FieldGroup>
        </div>
        <div className="grid md:grid-cols-4 gap-4 items-end">
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={policy.notifications.quiet_hours_enabled}
              onChange={(e) => updatePolicy((draft) => { draft.notifications.quiet_hours_enabled = e.target.checked; })}
            />
            Enable quiet hours
          </label>
          <FieldGroup label="Quiet hours start">
            <input
              type="time"
              value={policy.notifications.quiet_hours_start}
              onChange={(e) => updatePolicy((draft) => { draft.notifications.quiet_hours_start = e.target.value; })}
              className={SETTINGS_INPUT_CLASSNAME}
            />
          </FieldGroup>
          <FieldGroup label="Quiet hours end">
            <input
              type="time"
              value={policy.notifications.quiet_hours_end}
              onChange={(e) => updatePolicy((draft) => { draft.notifications.quiet_hours_end = e.target.value; })}
              className={SETTINGS_INPUT_CLASSNAME}
            />
          </FieldGroup>
          <FieldGroup label="Timezone">
            <input
              type="text"
              value={policy.notifications.timezone}
              onChange={(e) => updatePolicy((draft) => { draft.notifications.timezone = e.target.value; })}
              className={SETTINGS_INPUT_CLASSNAME}
            />
          </FieldGroup>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b border-gray-100">
                <th className="py-2">Event</th>
                <th className="py-2">Email</th>
                <th className="py-2">SMS</th>
                <th className="py-2">Push</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {eventKeys.map((eventKey) => {
                const prefs = policy.notifications.event_channels[eventKey];
                return (
                  <tr key={eventKey}>
                    <td className="py-2 font-mono text-xs text-gray-600">{eventKey}</td>
                    <td className="py-2">
                      <PermissionToggle checked={prefs.email} onChange={(value) => updatePolicy((draft) => { draft.notifications.event_channels[eventKey].email = value; })} />
                    </td>
                    <td className="py-2">
                      <PermissionToggle checked={prefs.sms} onChange={(value) => updatePolicy((draft) => { draft.notifications.event_channels[eventKey].sms = value; })} />
                    </td>
                    <td className="py-2">
                      <PermissionToggle checked={prefs.push} onChange={(value) => updatePolicy((draft) => { draft.notifications.event_channels[eventKey].push = value; })} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Section>

      <Section
        title="Order Form"
        description="Control standard field visibility and add tenant-specific fields for different order types."
      >
        <div className="grid md:grid-cols-3 gap-4">
          {Object.entries(policy.order_form.standard_fields).map(([fieldName, visibility]) => (
            <FieldGroup key={fieldName} label={fieldName}>
              <select
                value={visibility}
                onChange={(e) => updatePolicy((draft) => {
                  draft.order_form.standard_fields[fieldName as keyof OperationsPolicy["order_form"]["standard_fields"]] = e.target.value as VisibilityMode;
                })}
                className={SETTINGS_INPUT_CLASSNAME}
              >
                {VISIBILITY_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
              </select>
            </FieldGroup>
          ))}
        </div>

        <div className="space-y-4">
          {policy.order_form.custom_fields.map((field, index) => (
            <div key={`${field.key}-${index}`} className="rounded-xl border border-gray-200 p-4 grid md:grid-cols-6 gap-4">
              <FieldGroup label="Key">
                <input
                  type="text"
                  value={field.key}
                  onChange={(e) => updatePolicy((draft) => { draft.order_form.custom_fields[index].key = e.target.value; })}
                  className={SETTINGS_INPUT_CLASSNAME}
                />
              </FieldGroup>
              <FieldGroup label="Label">
                <input
                  type="text"
                  value={field.label}
                  onChange={(e) => updatePolicy((draft) => { draft.order_form.custom_fields[index].label = e.target.value; })}
                  className={SETTINGS_INPUT_CLASSNAME}
                />
              </FieldGroup>
              <FieldGroup label="Type">
                <select
                  value={field.type}
                  onChange={(e) => updatePolicy((draft) => { draft.order_form.custom_fields[index].type = e.target.value as CustomOrderFieldDefinition["type"]; })}
                  className={SETTINGS_INPUT_CLASSNAME}
                >
                  <option value="TEXT">Text</option>
                  <option value="NUMBER">Number</option>
                  <option value="DATE">Date</option>
                  <option value="SELECT">Select</option>
                  <option value="BOOLEAN">Boolean</option>
                </select>
              </FieldGroup>
              <FieldGroup label="Required">
                <label className="flex items-center gap-2 text-sm text-gray-700 mt-2">
                  <input
                    type="checkbox"
                    checked={field.required}
                    onChange={(e) => updatePolicy((draft) => { draft.order_form.custom_fields[index].required = e.target.checked; })}
                  />
                  Required
                </label>
              </FieldGroup>
              <FieldGroup label="Applies to">
                <input
                  type="text"
                  value={field.applies_to_order_types.join(", ")}
                  onChange={(e) => updatePolicy((draft) => {
                    // applies_to_order_types' contract type is the narrower
                    // delivery-only OrderType union (no RIDE). Cast through
                    // unknown so the freeform textarea still works for both.
                    draft.order_form.custom_fields[index].applies_to_order_types = (e.target.value
                      .split(",")
                      .map((value) => value.trim())
                      .filter(Boolean) as unknown) as (typeof draft.order_form.custom_fields)[number]["applies_to_order_types"];
                  })}
                  className={SETTINGS_INPUT_CLASSNAME}
                  placeholder="ON_DEMAND, BULK"
                />
              </FieldGroup>
              <FieldGroup label="Options">
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={field.options.join(", ")}
                    onChange={(e) => updatePolicy((draft) => {
                      draft.order_form.custom_fields[index].options = e.target.value
                        .split(",")
                        .map((value) => value.trim())
                        .filter(Boolean);
                    })}
                    className={SETTINGS_INPUT_CLASSNAME}
                    placeholder="Only for select fields"
                  />
                  <button
                    type="button"
                    onClick={() => updatePolicy((draft) => { draft.order_form.custom_fields.splice(index, 1); })}
                    className="px-3 py-2 text-sm text-red-600 border border-red-200 rounded-lg hover:bg-red-50"
                  >
                    Remove
                  </button>
                </div>
              </FieldGroup>
            </div>
          ))}
          <button
            type="button"
            onClick={() => updatePolicy((draft) => {
              draft.order_form.custom_fields.push({
                key: `custom_${draft.order_form.custom_fields.length + 1}`,
                label: "New Field",
                type: "TEXT",
                required: false,
                options: [],
                applies_to_order_types: [],
              });
            })}
            className="px-4 py-2 text-sm font-medium text-brand-700 bg-brand-50 rounded-lg hover:bg-brand-100"
          >
            Add custom field
          </button>
        </div>
      </Section>

      <Section
        title="Team Permissions"
        description="Restrict what each role can do within this tenant without changing global role definitions."
      >
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b border-gray-100">
                <th className="py-2">Role</th>
                <th className="py-2">Cancel orders</th>
                <th className="py-2">Edit orders</th>
                <th className="py-2">Reassign orders</th>
                <th className="py-2">Override POD</th>
                <th className="py-2">Manage settings</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {ROLES.map((role) => {
                const perms = policy.team_permissions[role] as RolePermissionSet;
                return (
                  <tr key={role}>
                    <td className="py-2 font-medium text-gray-700">{role}</td>
                    <td className="py-2"><PermissionToggle checked={perms.cancel_order} onChange={(value) => updatePolicy((draft) => { draft.team_permissions[role].cancel_order = value; })} /></td>
                    <td className="py-2"><PermissionToggle checked={perms.edit_order} onChange={(value) => updatePolicy((draft) => { draft.team_permissions[role].edit_order = value; })} /></td>
                    <td className="py-2"><PermissionToggle checked={perms.reassign_order} onChange={(value) => updatePolicy((draft) => { draft.team_permissions[role].reassign_order = value; })} /></td>
                    <td className="py-2"><PermissionToggle checked={perms.override_pod} onChange={(value) => updatePolicy((draft) => { draft.team_permissions[role].override_pod = value; })} /></td>
                    <td className="py-2"><PermissionToggle checked={perms.manage_settings} onChange={(value) => updatePolicy((draft) => { draft.team_permissions[role].manage_settings = value; })} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Section>

      <div className="flex justify-end">
        <button
          type="button"
          disabled={saving}
          onClick={() => void handleSave()}
          className="px-5 py-2.5 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700 disabled:opacity-50"
        >
          {saving ? "Saving..." : "Save operations settings"}
        </button>
      </div>
    </div>
  );
}
