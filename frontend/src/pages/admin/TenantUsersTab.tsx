import { useState } from "react";
import { useOutletContext } from "react-router-dom";
import { UserPlus } from "lucide-react";
import { useAuth } from "../../state/AuthContext";
import { inviteTenantUser } from "../../api/superadmin";
import type {
  SuperadminInviteUserPayload,
  SuperadminInviteUserResponse,
  SuperadminTenantDetail,
} from "../../types";

type Ctx = { tenant: SuperadminTenantDetail | null };

export function TenantUsersTab() {
  const { tenant } = useOutletContext<Ctx>();
  const { token } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<SuperadminInviteUserPayload["role"]>("ADMIN");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<SuperadminInviteUserResponse[]>([]);

  if (!tenant) return <div className="text-sm text-slate-400">Loading…</div>;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !tenant) return;
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const u = await inviteTenantUser(token, tenant.id, {
        email: email.trim(),
        password,
        role,
      });
      setCreated((prev) => [u, ...prev]);
      setEmail("");
      setPassword("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to invite user");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm text-slate-500 mb-3">
          Tenant currently has <span className="font-medium text-slate-900">{tenant.user_count}</span>{" "}
          {tenant.user_count === 1 ? "user" : "users"}. Invite a new one below — they'll
          be able to sign in immediately with the temporary password you set.
        </p>
      </div>

      {error && (
        <div className="px-3 py-2 bg-red-50 border border-red-200 text-red-700 text-sm rounded">
          {error}
        </div>
      )}

      <form onSubmit={submit} className="border border-slate-200 rounded-md bg-white p-4 space-y-3">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              placeholder="user@company.com"
              className="w-full border border-slate-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent-500 focus:ring-1 focus:ring-accent-500/20"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">Temporary password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              placeholder="Min 8 characters"
              className="w-full border border-slate-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent-500 focus:ring-1 focus:ring-accent-500/20"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">Role</label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as SuperadminInviteUserPayload["role"])}
              className="w-full border border-slate-200 rounded-md px-3 py-2 text-sm bg-white focus:outline-none focus:border-accent-500 focus:ring-1 focus:ring-accent-500/20"
            >
              <option value="OWNER">Owner</option>
              <option value="ADMIN">Admin</option>
              <option value="DISPATCHER">Dispatcher</option>
              <option value="COMPANY_ADMIN">Company admin (read-only)</option>
              <option value="COURIER">Courier</option>
            </select>
          </div>
        </div>
        <button
          type="submit"
          disabled={submitting}
          className="inline-flex items-center gap-1.5 h-9 px-3 bg-accent-500 hover:bg-accent-600 disabled:bg-slate-300 text-white text-sm font-medium rounded-md transition-colors"
        >
          <UserPlus size={14} />
          {submitting ? "Inviting…" : "Invite user"}
        </button>
      </form>

      {created.length > 0 && (
        <div>
          <h3 className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 mb-2">
            Created in this session
          </h3>
          <div className="border border-slate-200 rounded-md overflow-hidden bg-white">
            {created.map((u) => (
              <div
                key={u.id}
                className="flex items-center justify-between px-4 py-2.5 border-b border-slate-100 last:border-b-0"
              >
                <div className="text-sm text-slate-800">{u.email}</div>
                <span className="text-[10px] px-2 py-0.5 rounded font-medium bg-slate-100 text-slate-700">
                  {u.role}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
