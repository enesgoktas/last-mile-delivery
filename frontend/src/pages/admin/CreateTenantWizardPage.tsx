import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight, Check } from "lucide-react";
import { useAuth } from "../../state/AuthContext";
import { createTenant } from "../../api/superadmin";
import type { FeatureFlags, SuperadminTenantCreatePayload } from "../../types";

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

const STEPS = ["Company", "Modes", "Rides", "EV fleet", "Initial admin", "Confirm"] as const;
type Step = (typeof STEPS)[number];

interface WizardState {
  // Company info
  name: string;
  slug: string;
  primaryColor: string;
  // Modes
  deliveriesEnabled: boolean;
  ridesEnabled: boolean;
  // Ride policy
  ridePickupBufferEnabled: boolean;
  rideStrictLatenessEnabled: boolean;
  rideAirportFeaturesEnabled: boolean;
  rideNoShowHandlingEnabled: boolean;
  // EV fleet
  evBatteryAwarenessEnabled: boolean;
  evLowBatteryAlertsEnabled: boolean;
  // Initial admin
  adminEmail: string;
  adminPassword: string;
  adminConfirm: string;
}

const INITIAL: WizardState = {
  name: "",
  slug: "",
  primaryColor: "",
  deliveriesEnabled: true,
  ridesEnabled: false,
  ridePickupBufferEnabled: false,
  rideStrictLatenessEnabled: false,
  rideAirportFeaturesEnabled: false,
  rideNoShowHandlingEnabled: false,
  evBatteryAwarenessEnabled: false,
  evLowBatteryAlertsEnabled: false,
  adminEmail: "",
  adminPassword: "",
  adminConfirm: "",
};

function deriveFlags(s: WizardState): FeatureFlags {
  return {
    deliveries_enabled: s.deliveriesEnabled,
    rides_enabled: s.ridesEnabled,
    ride_pickup_buffer_enabled: s.ridesEnabled && s.ridePickupBufferEnabled,
    ride_strict_lateness_enabled: s.ridesEnabled && s.rideStrictLatenessEnabled,
    ride_airport_features_enabled: s.ridesEnabled && s.rideAirportFeaturesEnabled,
    ride_no_show_handling_enabled: s.ridesEnabled && s.rideNoShowHandlingEnabled,
    ev_battery_awareness_enabled: s.evBatteryAwarenessEnabled,
    ev_low_battery_alerts_enabled:
      s.evBatteryAwarenessEnabled && s.evLowBatteryAlertsEnabled,
    ev_auto_charging_enabled: false,
    multi_depot_vehicles_enabled: false,
    multi_trip_enabled: true,
    traffic_aware_routing_enabled: true,
  };
}

export function CreateTenantWizardPage() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [stepIdx, setStepIdx] = useState(0);
  const [state, setState] = useState<WizardState>(INITIAL);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const step = STEPS[stepIdx];
  const visibleSteps = STEPS.filter((s) => s !== "Rides" || state.ridesEnabled);
  const visibleIdx = visibleSteps.indexOf(step);

  function update<K extends keyof WizardState>(key: K, value: WizardState[K]) {
    setState((prev) => ({ ...prev, [key]: value }));
  }

  function validateCurrent(): string | null {
    if (step === "Company") {
      if (!state.name.trim()) return "Company name is required.";
      if (!SLUG_RE.test(state.slug)) {
        return "Slug must be lowercase alphanumeric, hyphen-separated (no spaces).";
      }
    }
    if (step === "Modes") {
      if (!state.deliveriesEnabled && !state.ridesEnabled) {
        return "Pick at least one service mode.";
      }
    }
    if (step === "Initial admin") {
      if (!state.adminEmail.trim()) return "Admin email required.";
      if (state.adminPassword.length < 8) return "Password must be at least 8 characters.";
      if (state.adminPassword !== state.adminConfirm) return "Passwords don't match.";
    }
    return null;
  }

  function next() {
    const err = validateCurrent();
    if (err) {
      setError(err);
      return;
    }
    setError(null);
    // Skip the Rides step if rides are disabled
    let nextIdx = stepIdx + 1;
    while (
      nextIdx < STEPS.length &&
      STEPS[nextIdx] === "Rides" &&
      !state.ridesEnabled
    ) {
      nextIdx += 1;
    }
    setStepIdx(nextIdx);
  }

  function back() {
    setError(null);
    let prevIdx = stepIdx - 1;
    while (prevIdx >= 0 && STEPS[prevIdx] === "Rides" && !state.ridesEnabled) {
      prevIdx -= 1;
    }
    setStepIdx(Math.max(0, prevIdx));
  }

  async function submit() {
    if (!token) return;
    setError(null);
    setSubmitting(true);
    try {
      const payload: SuperadminTenantCreatePayload = {
        name: state.name.trim(),
        slug: state.slug.trim(),
        primary_color: state.primaryColor.trim() || null,
        feature_flags: deriveFlags(state),
        initial_user: {
          email: state.adminEmail.trim(),
          password: state.adminPassword,
          role: "OWNER",
        },
      };
      const created = await createTenant(token, payload);
      navigate(`/admin/tenants/${created.id}`, { replace: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create tenant.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="px-8 py-8 max-w-3xl mx-auto w-full">
      <Link
        to="/admin/tenants"
        className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700 mb-3"
      >
        <ArrowLeft size={12} />
        Back to tenants
      </Link>
      <h1 className="text-xl font-semibold text-slate-900 mb-1">New tenant</h1>
      <p className="text-sm text-slate-500 mb-6">
        Onboarding questionnaire. Each answer maps to a feature flag or setting on
        the new tenant.
      </p>

      <Stepper steps={visibleSteps} activeIdx={visibleIdx} />

      {error && (
        <div className="mb-4 px-3 py-2 bg-red-50 border border-red-200 text-red-700 text-sm rounded">
          {error}
        </div>
      )}

      <div className="mt-6">
        {step === "Company" && <CompanyStep state={state} update={update} />}
        {step === "Modes" && <ModesStep state={state} update={update} />}
        {step === "Rides" && <RidesStep state={state} update={update} />}
        {step === "EV fleet" && <EvStep state={state} update={update} />}
        {step === "Initial admin" && <InitialAdminStep state={state} update={update} />}
        {step === "Confirm" && <ConfirmStep state={state} />}
      </div>

      <div className="mt-8 flex items-center justify-between">
        <button
          onClick={back}
          disabled={stepIdx === 0 || submitting}
          className="inline-flex items-center gap-1.5 h-9 px-3 text-sm font-medium text-slate-600 hover:text-slate-900 disabled:text-slate-300"
        >
          <ArrowLeft size={14} />
          Back
        </button>
        {step === "Confirm" ? (
          <button
            onClick={submit}
            disabled={submitting}
            className="inline-flex items-center gap-1.5 h-9 px-4 bg-accent-500 hover:bg-accent-600 disabled:bg-slate-300 text-white text-sm font-medium rounded-md transition-colors"
          >
            <Check size={14} />
            {submitting ? "Creating…" : "Create tenant"}
          </button>
        ) : (
          <button
            onClick={next}
            disabled={submitting}
            className="inline-flex items-center gap-1.5 h-9 px-4 bg-accent-500 hover:bg-accent-600 text-white text-sm font-medium rounded-md transition-colors"
          >
            Next
            <ArrowRight size={14} />
          </button>
        )}
      </div>
    </div>
  );
}

function Stepper({ steps, activeIdx }: { steps: readonly Step[]; activeIdx: number }) {
  return (
    <div className="flex items-center gap-2">
      {steps.map((s, i) => {
        const active = i === activeIdx;
        const done = i < activeIdx;
        return (
          <div key={s} className="flex items-center gap-2">
            <div
              className={[
                "w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-semibold",
                active
                  ? "bg-accent-500 text-white"
                  : done
                    ? "bg-emerald-100 text-emerald-700"
                    : "bg-slate-100 text-slate-400",
              ].join(" ")}
            >
              {done ? <Check size={12} /> : i + 1}
            </div>
            <span
              className={[
                "text-xs",
                active ? "text-slate-900 font-semibold" : "text-slate-500",
              ].join(" ")}
            >
              {s}
            </span>
            {i < steps.length - 1 && <div className="w-4 h-px bg-slate-200" />}
          </div>
        );
      })}
    </div>
  );
}

// ── Step components ───────────────────────────────────────────────────

type StepProps = {
  state: WizardState;
  update: <K extends keyof WizardState>(key: K, value: WizardState[K]) => void;
};

function CompanyStep({ state, update }: StepProps) {
  return (
    <div className="space-y-4">
      <Field
        label="Company name"
        value={state.name}
        onChange={(v) => update("name", v)}
        placeholder="Acme Logistics"
      />
      <Field
        label="Slug"
        value={state.slug}
        onChange={(v) => update("slug", v.toLowerCase())}
        placeholder="acme-logistics"
        hint="Lowercase letters, numbers, hyphens. Used in tenant URLs and login."
      />
      <Field
        label="Primary brand colour (optional)"
        value={state.primaryColor}
        onChange={(v) => update("primaryColor", v)}
        placeholder="#3b82f6"
      />
    </div>
  );
}

function ModesStep({ state, update }: StepProps) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-600 mb-2">What does this company do?</p>
      <Toggle
        label="Package delivery"
        description="Standard last-mile / same-day deliveries. Dispatcher sees the Orders page."
        checked={state.deliveriesEnabled}
        onChange={(v) => update("deliveriesEnabled", v)}
      />
      <Toggle
        label="Passenger transport"
        description="Executive transport / ride-hailing. Driver-arrives-before-passenger flow with a dedicated Rides page."
        checked={state.ridesEnabled}
        onChange={(v) => update("ridesEnabled", v)}
      />
    </div>
  );
}

function RidesStep({ state, update }: StepProps) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-600 mb-2">Ride policy</p>
      <Toggle
        label="Driver must arrive before passenger ready time"
        description="Hard constraint — orders that can't satisfy the buffer go unassigned with an alert."
        checked={state.ridePickupBufferEnabled}
        onChange={(v) => update("ridePickupBufferEnabled", v)}
      />
      <Toggle
        label="Strict pickup lateness (zero soft-grace)"
        description="Default 5 minutes of grace becomes 0 for ride pickups."
        checked={state.rideStrictLatenessEnabled}
        onChange={(v) => update("rideStrictLatenessEnabled", v)}
      />
      <Toggle
        label="Airport pickup features"
        description="Adds flight info field on rides + airport punctuality reporting."
        checked={state.rideAirportFeaturesEnabled}
        onChange={(v) => update("rideAirportFeaturesEnabled", v)}
      />
      <Toggle
        label="Passenger no-show handling"
        description="Driver app shows a wait timer and no-show button on ride pickups."
        checked={state.rideNoShowHandlingEnabled}
        onChange={(v) => update("rideNoShowHandlingEnabled", v)}
      />
    </div>
  );
}

function EvStep({ state, update }: StepProps) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-600 mb-2">EV fleet awareness</p>
      <Toggle
        label="Battery awareness"
        description="Vehicle's current charge constrains route distance — half-charged cars get fewer / shorter routes."
        checked={state.evBatteryAwarenessEnabled}
        onChange={(v) => update("evBatteryAwarenessEnabled", v)}
      />
      <Toggle
        label="Low-battery alerts in dispatcher dashboard"
        description="Surface vehicles below the low-charge threshold."
        checked={state.evLowBatteryAlertsEnabled}
        onChange={(v) => update("evLowBatteryAlertsEnabled", v)}
        disabled={!state.evBatteryAwarenessEnabled}
      />
      <p className="text-xs text-slate-400 italic mt-1">
        Auto-insert charging stops mid-route is a Phase 2 feature and not yet available.
      </p>
    </div>
  );
}

function InitialAdminStep({ state, update }: StepProps) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600 mb-2">
        First OWNER user for this tenant. They can invite others after signing in.
      </p>
      <Field
        label="Email"
        type="email"
        value={state.adminEmail}
        onChange={(v) => update("adminEmail", v)}
        placeholder="admin@acme.com"
      />
      <Field
        label="Temporary password"
        type="password"
        value={state.adminPassword}
        onChange={(v) => update("adminPassword", v)}
        placeholder="Minimum 8 characters"
      />
      <Field
        label="Confirm password"
        type="password"
        value={state.adminConfirm}
        onChange={(v) => update("adminConfirm", v)}
        placeholder=""
      />
    </div>
  );
}

function ConfirmStep({ state }: { state: WizardState }) {
  const flags = deriveFlags(state);
  const enabled = (Object.keys(flags) as Array<keyof FeatureFlags>).filter(
    (k) => flags[k],
  );
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">Review and create.</p>
      <Section title="Company">
        <KV label="Name" value={state.name} />
        <KV label="Slug" value={state.slug} />
        {state.primaryColor && <KV label="Primary colour" value={state.primaryColor} />}
      </Section>
      <Section title="Enabled features">
        <div className="flex flex-wrap gap-1.5">
          {enabled.map((f) => (
            <span
              key={f}
              className="text-[10px] px-2 py-0.5 rounded bg-accent-50 text-accent-700 font-medium"
            >
              {f}
            </span>
          ))}
        </div>
      </Section>
      <Section title="Initial admin">
        <KV label="Email" value={state.adminEmail} />
        <KV label="Role" value="OWNER" />
      </Section>
    </div>
  );
}

// ── Reusable atoms ────────────────────────────────────────────────────

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  hint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  hint?: string;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-700 mb-1.5">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full border border-slate-200 rounded-md px-3 py-2 text-sm text-slate-900 bg-white focus:outline-none focus:border-accent-500 focus:ring-1 focus:ring-accent-500/20 placeholder:text-slate-400"
      />
      {hint && <p className="text-[11px] text-slate-400 mt-1">{hint}</p>}
    </div>
  );
}

function Toggle({
  label,
  description,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label
      className={[
        "flex items-start gap-3 px-3 py-2.5 border border-slate-200 rounded-md cursor-pointer transition-colors",
        disabled
          ? "opacity-50 cursor-not-allowed"
          : checked
            ? "border-accent-300 bg-accent-50/50"
            : "hover:border-slate-300",
      ].join(" ")}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5"
      />
      <div className="flex-1">
        <div className="text-sm font-medium text-slate-900">{label}</div>
        {description && (
          <div className="text-xs text-slate-500 mt-0.5">{description}</div>
        )}
      </div>
    </label>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border border-slate-200 rounded-md bg-white">
      <div className="px-4 py-2 border-b border-slate-200 text-[10px] font-semibold uppercase tracking-widest text-slate-400">
        {title}
      </div>
      <div className="px-4 py-3 space-y-1.5">{children}</div>
    </div>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-2 text-sm">
      <span className="text-slate-400 text-xs w-28 shrink-0">{label}</span>
      <span className="text-slate-800">{value}</span>
    </div>
  );
}
