/**
 * Onboarding checklist (Phase 8.9)
 *
 * Shown as a dismissible sidebar card on first login.
 * Tracks progress through: depot → courier → order → optimization.
 * State is persisted to localStorage so it survives page refreshes.
 *
 * Usage: mount <OnboardingChecklist /> once in the layout. It renders only
 * when the user hasn't dismissed it and there are incomplete steps.
 */
import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { isAdminLikeRole } from "../config/roles";
import { useAuth } from "../state/AuthContext";

const STORAGE_KEY = "lmd_onboarding_v1";

export interface OnboardingState {
  dismissed: boolean;
  steps: {
    depot: boolean;
    courier: boolean;
    order: boolean;
    optimization: boolean;
  };
}

const DEFAULT_STATE: OnboardingState = {
  dismissed: false,
  steps: { depot: false, courier: false, order: false, optimization: false },
};

function loadState(): OnboardingState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as OnboardingState;
  } catch {
    /* ignore */
  }
  return DEFAULT_STATE;
}

function saveState(s: OnboardingState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    /* ignore */
  }
}

export function useOnboardingState() {
  const [state, setState] = useState<OnboardingState>(loadState);

  function update(patch: Partial<OnboardingState["steps"]>) {
    setState((prev) => {
      const next = { ...prev, steps: { ...prev.steps, ...patch } };
      saveState(next);
      return next;
    });
  }

  function dismiss() {
    setState((prev) => {
      const next = { ...prev, dismissed: true };
      saveState(next);
      return next;
    });
  }

  function reset() {
    saveState(DEFAULT_STATE);
    setState(DEFAULT_STATE);
  }

  return { state, update, dismiss, reset };
}

const STEPS: {
  key: keyof OnboardingState["steps"];
  label: string;
  description: string;
  to: string;
  cta: string;
  adminOnly?: boolean;
}[] = [
  {
    key: "depot",
    label: "Create your first depot",
    description: "A depot is the starting point for all your routes.",
    to: "/settings?tab=Depots",
    cta: "Go to Settings → Depots",
    adminOnly: true,
  },
  {
    key: "courier",
    label: "Add your first courier",
    description: "Assign a driver and vehicle to start dispatching.",
    to: "/couriers",
    cta: "Go to Couriers",
  },
  {
    key: "order",
    label: "Create your first order",
    description: "Add a delivery order to be included in the next route.",
    to: "/orders",
    cta: "Go to Orders",
  },
  {
    key: "optimization",
    label: "Run your first optimization",
    description: "Let the system build an optimal route for your couriers.",
    to: "/",
    cta: "Go to Live Ops",
  },
];

interface Props {
  onStepComplete?: (key: keyof OnboardingState["steps"]) => void;
}

export function OnboardingChecklist({ onStepComplete }: Props) {
  const { role } = useAuth();
  const { state, update, dismiss } = useOnboardingState();
  const [isExpanded, setIsExpanded] = useState(true);
  const visibleSteps = STEPS.filter((step) => !step.adminOnly || isAdminLikeRole(role));

  const allDone = visibleSteps.every((step) => state.steps[step.key]);

  // Auto-dismiss after all steps complete (short delay for satisfaction UX)
  useEffect(() => {
    if (allDone && !state.dismissed) {
      const t = setTimeout(dismiss, 3000);
      return () => clearTimeout(t);
    }
  }, [allDone, state.dismissed, dismiss]);

  if (state.dismissed) return null;

  const completedCount = visibleSteps.filter((step) => state.steps[step.key]).length;
  const totalCount = visibleSteps.length;
  const progressPct = Math.round((completedCount / totalCount) * 100);

  return (
    <div className="fixed bottom-4 right-4 w-72 bg-white rounded-xl shadow-lg border border-gray-200 overflow-hidden z-30">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-brand-600 text-white">
        <div>
          <p className="text-sm font-semibold">Getting started</p>
          <p className="text-xs opacity-80">
            {completedCount} / {totalCount} steps complete
          </p>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setIsExpanded((v) => !v)}
            className="p-1 rounded hover:bg-white/20 transition-colors"
            aria-label={isExpanded ? "Collapse" : "Expand"}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d={isExpanded ? "M5 15l7-7 7 7" : "M19 9l-7 7-7-7"} />
            </svg>
          </button>
          <button
            onClick={dismiss}
            className="p-1 rounded hover:bg-white/20 transition-colors"
            aria-label="Dismiss"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      {/* Progress bar */}
      <div className="h-1 bg-gray-100">
        <div
          className="h-1 bg-brand-500 transition-all duration-500"
          style={{ width: `${progressPct}%` }}
        />
      </div>

      {/* Steps */}
      {isExpanded && (
        <div className="p-3 space-y-2">
          {allDone ? (
            <div className="text-center py-3">
              <div className="text-2xl mb-1">🎉</div>
              <p className="text-sm font-semibold text-gray-800">You're all set!</p>
              <p className="text-xs text-gray-500 mt-0.5">Closing in a moment…</p>
            </div>
          ) : (
            visibleSteps.map((step) => {
              const done = state.steps[step.key];
              return (
                <div
                  key={step.key}
                  className={`flex items-start gap-2.5 p-2.5 rounded-lg ${
                    done ? "opacity-50" : "bg-gray-50"
                  }`}
                >
                  {/* Checkbox */}
                  <button
                    onClick={() => {
                      update({ [step.key]: !done });
                      if (!done) onStepComplete?.(step.key);
                    }}
                    className={`flex-shrink-0 mt-0.5 w-5 h-5 rounded-full border-2 flex items-center justify-center transition-colors ${
                      done
                        ? "border-brand-500 bg-brand-500 text-white"
                        : "border-gray-300 hover:border-brand-400"
                    }`}
                    aria-label={done ? `Unmark ${step.label}` : `Mark ${step.label} as done`}
                  >
                    {done && (
                      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                  </button>
                  {/* Content */}
                  <div className="min-w-0">
                    <p className={`text-xs font-medium ${done ? "line-through text-gray-400" : "text-gray-800"}`}>
                      {step.label}
                    </p>
                    {!done && (
                      <>
                        <p className="text-xs text-gray-500 mt-0.5">{step.description}</p>
                        <Link
                          to={step.to}
                          className="text-xs text-brand-600 hover:underline mt-1 inline-block"
                        >
                          {step.cta} →
                        </Link>
                      </>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
