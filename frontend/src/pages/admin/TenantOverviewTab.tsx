import { useOutletContext } from "react-router-dom";
import type { SuperadminTenantDetail } from "../../types";

type Ctx = { tenant: SuperadminTenantDetail | null };

export function TenantOverviewTab() {
  const { tenant } = useOutletContext<Ctx>();
  if (!tenant) {
    return <div className="text-sm text-slate-400">Loading…</div>;
  }
  const flags = tenant.feature_flags;
  const enabled = (Object.keys(flags) as Array<keyof typeof flags>).filter(
    (k) => flags[k],
  );
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      <Section title="Identity">
        <KV label="Name" value={tenant.name} />
        <KV label="Slug" value={tenant.slug} />
        <KV label="Status" value={tenant.is_active ? "Active" : "Inactive"} />
        <KV label="Users" value={String(tenant.user_count)} />
      </Section>
      <Section title="Enabled features">
        {enabled.length === 0 ? (
          <div className="text-xs text-slate-400">No features enabled.</div>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {enabled.map((flag) => (
              <span
                key={flag}
                className="text-[10px] px-2 py-0.5 rounded bg-accent-50 text-accent-700 font-medium"
              >
                {flag}
              </span>
            ))}
          </div>
        )}
      </Section>
    </div>
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
      <span className="text-slate-400 text-xs w-20 shrink-0">{label}</span>
      <span className="text-slate-800">{value}</span>
    </div>
  );
}
