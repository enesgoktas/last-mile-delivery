import { useEffect, useMemo, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { Save } from "lucide-react";
import { useAuth } from "../../state/AuthContext";
import { getFeatureFlagsSchema, updateFeatureFlags } from "../../api/superadmin";
import type {
  FeatureFlagSchemaEntry,
  FeatureFlags,
  SuperadminTenantDetail,
} from "../../types";

type Ctx = { tenant: SuperadminTenantDetail | null };

export function TenantSettingsTab() {
  const { tenant } = useOutletContext<Ctx>();
  const { token } = useAuth();
  const [schema, setSchema] = useState<FeatureFlagSchemaEntry[] | null>(null);
  const [flags, setFlags] = useState<FeatureFlags | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    getFeatureFlagsSchema(token)
      .then((s) => {
        if (!cancelled) setSchema(s);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load schema");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  useEffect(() => {
    if (tenant) {
      setFlags({ ...tenant.feature_flags });
      setDirty(false);
    }
  }, [tenant]);

  const grouped = useMemo(() => {
    if (!schema) return null;
    const map = new Map<string, FeatureFlagSchemaEntry[]>();
    for (const entry of schema) {
      const cat = entry.category || "Other";
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(entry);
    }
    return Array.from(map.entries());
  }, [schema]);

  function isFlagDisabled(entry: FeatureFlagSchemaEntry): boolean {
    if (!flags) return true;
    return entry.depends_on.some((dep) => !flags[dep]);
  }

  function toggle(name: keyof FeatureFlags, value: boolean) {
    if (!flags) return;
    const next: FeatureFlags = { ...flags, [name]: value };
    // If we just turned off a feature, also turn off any flag that depends on it.
    if (!value && schema) {
      for (const entry of schema) {
        if (entry.depends_on.includes(name)) {
          next[entry.name] = false;
        }
      }
    }
    setFlags(next);
    setDirty(true);
  }

  async function save() {
    if (!token || !tenant || !flags) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateFeatureFlags(token, tenant.id, flags);
      setFlags(updated);
      setDirty(false);
      setSavedAt(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  if (!tenant || !flags) {
    return <div className="text-sm text-slate-400">Loading…</div>;
  }
  if (!schema || !grouped) {
    return <div className="text-sm text-slate-400">Loading schema…</div>;
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <p className="text-sm text-slate-500">
          Adding a flag in the backend automatically adds a row here — UI is fully
          schema-driven.
        </p>
        <div className="flex items-center gap-3">
          {savedAt && !dirty && (
            <span className="text-xs text-emerald-600">Saved.</span>
          )}
          <button
            onClick={save}
            disabled={!dirty || saving}
            className="inline-flex items-center gap-1.5 h-9 px-3 bg-accent-500 hover:bg-accent-600 disabled:bg-slate-300 disabled:cursor-not-allowed text-white text-sm font-medium rounded-md transition-colors"
          >
            <Save size={14} />
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 px-3 py-2 bg-red-50 border border-red-200 text-red-700 text-sm rounded">
          {error}
        </div>
      )}

      <div className="space-y-6">
        {grouped.map(([category, entries]) => (
          <div key={category}>
            <h3 className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 mb-2">
              {category}
            </h3>
            <div className="space-y-2">
              {entries.map((entry) => {
                const disabled = isFlagDisabled(entry);
                const checked = flags[entry.name];
                return (
                  <label
                    key={entry.name}
                    className={[
                      "flex items-start gap-3 px-3 py-2.5 border border-slate-200 rounded-md transition-colors",
                      disabled
                        ? "opacity-50 cursor-not-allowed"
                        : checked
                          ? "border-accent-300 bg-accent-50/40 cursor-pointer"
                          : "hover:border-slate-300 cursor-pointer",
                    ].join(" ")}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={disabled}
                      onChange={(e) => toggle(entry.name, e.target.checked)}
                      className="mt-0.5"
                    />
                    <div className="flex-1">
                      <div className="text-sm font-medium text-slate-900">
                        {entry.label}
                      </div>
                      <div className="text-xs text-slate-500 mt-0.5">
                        {entry.description}
                      </div>
                      {entry.depends_on.length > 0 && (
                        <div className="text-[11px] text-slate-400 mt-1">
                          Requires: {entry.depends_on.join(", ")}
                        </div>
                      )}
                    </div>
                  </label>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
