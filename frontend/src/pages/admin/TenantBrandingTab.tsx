import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { Save } from "lucide-react";
import { useAuth } from "../../state/AuthContext";
import { updateTenant } from "../../api/superadmin";
import type { SuperadminTenantDetail } from "../../types";

type Ctx = { tenant: SuperadminTenantDetail | null };

export function TenantBrandingTab() {
  const { tenant } = useOutletContext<Ctx>();
  const { token } = useAuth();
  const [name, setName] = useState("");
  const [logoUrl, setLogoUrl] = useState("");
  const [primaryColor, setPrimaryColor] = useState("");
  const [secondaryColor, setSecondaryColor] = useState("");
  const [active, setActive] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    if (tenant) {
      setName(tenant.name);
      setLogoUrl(tenant.logo_url ?? "");
      setPrimaryColor(tenant.primary_color ?? "");
      setSecondaryColor(tenant.secondary_color ?? "");
      setActive(tenant.is_active);
    }
  }, [tenant]);

  if (!tenant) return <div className="text-sm text-slate-400">Loading…</div>;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !tenant) return;
    setSaving(true);
    setError(null);
    try {
      await updateTenant(token, tenant.id, {
        name: name.trim() || undefined,
        logo_url: logoUrl.trim() || null,
        primary_color: primaryColor.trim() || null,
        secondary_color: secondaryColor.trim() || null,
        is_active: active,
      });
      setSavedAt(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={save} className="space-y-4 max-w-xl">
      <Field label="Display name" value={name} onChange={setName} />
      <Field
        label="Logo URL"
        value={logoUrl}
        onChange={setLogoUrl}
        placeholder="https://…/logo.png"
      />
      <div className="grid grid-cols-2 gap-3">
        <Field
          label="Primary colour"
          value={primaryColor}
          onChange={setPrimaryColor}
          placeholder="#3b82f6"
        />
        <Field
          label="Secondary colour"
          value={secondaryColor}
          onChange={setSecondaryColor}
          placeholder="#1e293b"
        />
      </div>
      <label className="flex items-center gap-2 text-sm text-slate-700">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
        Tenant is active
      </label>

      {error && (
        <div className="px-3 py-2 bg-red-50 border border-red-200 text-red-700 text-sm rounded">
          {error}
        </div>
      )}

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={saving}
          className="inline-flex items-center gap-1.5 h-9 px-3 bg-accent-500 hover:bg-accent-600 disabled:bg-slate-300 text-white text-sm font-medium rounded-md transition-colors"
        >
          <Save size={14} />
          {saving ? "Saving…" : "Save changes"}
        </button>
        {savedAt && <span className="text-xs text-emerald-600">Saved.</span>}
      </div>
    </form>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-700 mb-1.5">{label}</label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full border border-slate-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent-500 focus:ring-1 focus:ring-accent-500/20 placeholder:text-slate-400"
      />
    </div>
  );
}
