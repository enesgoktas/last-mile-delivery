import { useCallback, useMemo, useState, type FormEvent } from "react";
import { inviteUser, listUsers, updateUser } from "../../api/client";
import { LoadingSpinner } from "../../components/LoadingSpinner";
import { Modal } from "../../components/Modal";
import { StatusBadge } from "../../components/StatusBadge";
import { usePolling } from "../../hooks/usePolling";
import { useAuth } from "../../state/AuthContext";
import { useToast } from "../../state/ToastContext";
import { SETTINGS_INPUT_CLASSNAME } from "./shared";

type UserRecord = {
  id: string;
  tenant_id: string;
  email: string;
  role: string;
  is_active: boolean;
  created_at: string;
};

const PAGE_SIZE = 10;

export function UsersTab() {
  const { token } = useAuth();
  const { addToast } = useToast();
  const fetchUsers = useCallback(() => listUsers(token!), [token]);
  const { data: users, isLoading, refetch } = usePolling(fetchUsers, 60_000);

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);

  const [showCreate, setShowCreate] = useState(false);
  const [email, setEmail] = useState("");
  const [userRole, setUserRole] = useState("DISPATCHER");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [inviteSent, setInviteSent] = useState(false);

  const [editUser, setEditUser] = useState<UserRecord | null>(null);
  const [editRole, setEditRole] = useState("");
  const [editActive, setEditActive] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (users ?? []).filter(
      (u) => !q || u.email.toLowerCase().includes(q) || u.role.toLowerCase().includes(q),
    );
  }, [users, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const paginated = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function openEdit(user: UserRecord) {
    setEditUser(user);
    setEditRole(user.role);
    setEditActive(user.is_active);
    setEditError(null);
  }

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      await inviteUser(token!, { email, role: userRole });
      setInviteSent(true);
      addToast("Invitation sent", "success");
      refetch();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed");
    } finally {
      setCreating(false);
    }
  }

  function closeInviteModal() {
    setShowCreate(false);
    setEmail("");
    setUserRole("DISPATCHER");
    setCreateError(null);
    setInviteSent(false);
  }

  async function handleEdit(event: FormEvent) {
    event.preventDefault();
    if (!editUser) return;
    setSaving(true);
    setEditError(null);
    try {
      await updateUser(token!, editUser.id, { role: editRole, is_active: editActive });
      setEditUser(null);
      addToast("User updated", "success");
      refetch();
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleToggleActive(user: UserRecord) {
    try {
      await updateUser(token!, user.id, { is_active: !user.is_active });
      addToast(user.is_active ? "User deactivated" : "User activated", "success");
      refetch();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Failed to update user", "error");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <input
          type="search"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(0); }}
          placeholder="Search by email or role…"
          className="flex-1 rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-brand-500 focus:ring-1 focus:ring-brand-500 outline-none"
        />
        <button
          onClick={() => { setShowCreate(true); setCreateError(null); setInviteSent(false); }}
          className="shrink-0 px-3 py-2 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700"
        >
          + Invite User
        </button>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {isLoading && !users ? (
          <LoadingSpinner label="Loading users…" />
        ) : !filtered.length ? (
          <div className="p-10 text-center">
            <p className="text-sm text-gray-500">{search ? "No users match your search." : "No users yet."}</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50/50">
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Email</th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Role</th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Status</th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Created</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {paginated.map((user) => (
                <tr key={user.id} className="hover:bg-gray-50/50">
                  <td className="px-4 py-3 text-gray-900 font-medium">{user.email}</td>
                  <td className="px-4 py-3"><StatusBadge value={user.role} /></td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => handleToggleActive(user)}
                      className={`inline-flex items-center gap-1.5 text-xs font-medium px-2 py-1 rounded-full transition-colors ${
                        user.is_active
                          ? "bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
                          : "bg-gray-100 text-gray-500 hover:bg-gray-200"
                      }`}
                    >
                      <span className={`w-1.5 h-1.5 rounded-full ${user.is_active ? "bg-emerald-500" : "bg-gray-400"}`} />
                      {user.is_active ? "Active" : "Inactive"}
                    </button>
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs">{new Date(user.created_at).toLocaleDateString()}</td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => openEdit(user)}
                      className="text-xs text-brand-600 hover:underline"
                    >
                      Edit
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-gray-500">
          <span>{filtered.length} user{filtered.length !== 1 ? "s" : ""}</span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="px-3 py-1.5 rounded-lg border border-gray-200 text-xs disabled:opacity-40 hover:bg-gray-50"
            >
              Previous
            </button>
            <span className="text-xs">{page + 1} / {totalPages}</span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="px-3 py-1.5 rounded-lg border border-gray-200 text-xs disabled:opacity-40 hover:bg-gray-50"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {/* Invite modal */}
      <Modal open={showCreate} onClose={closeInviteModal} title="Invite User">
        {inviteSent ? (
          <div className="space-y-4">
            <div className="bg-emerald-50 border border-emerald-200 rounded-lg px-4 py-3 text-sm text-emerald-700">
              Invitation sent to <strong>{email}</strong>. They will receive an email with a link to set their password.
            </div>
            <div className="flex justify-end">
              <button
                onClick={closeInviteModal}
                className="px-4 py-2 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700"
              >
                Done
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleCreate} className="space-y-4">
            {createError && (
              <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">{createError}</div>
            )}
            <p className="text-sm text-gray-500">
              An invitation email will be sent with a link to set their password and activate their account.
            </p>
            <div>
              <label htmlFor="invite-email" className="block text-sm font-medium text-gray-700 mb-1">Email</label>
              <input
                id="invite-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                required
              />
            </div>
            <div>
              <label htmlFor="invite-role" className="block text-sm font-medium text-gray-700 mb-1">Role</label>
              <select id="invite-role" value={userRole} onChange={(e) => setUserRole(e.target.value)} className={SETTINGS_INPUT_CLASSNAME}>
                <option value="ADMIN">Admin</option>
                <option value="DISPATCHER">Dispatcher</option>
                <option value="COMPANY_ADMIN">Company admin (read-only)</option>
                <option value="COURIER">Courier</option>
              </select>
            </div>
            <div className="flex justify-end gap-3 pt-2">
              <button
                type="button"
                onClick={closeInviteModal}
                className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={creating}
                className="px-4 py-2 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700 disabled:opacity-50"
              >
                {creating ? "Sending…" : "Send Invitation"}
              </button>
            </div>
          </form>
        )}
      </Modal>

      {/* Edit modal */}
      <Modal open={!!editUser} onClose={() => setEditUser(null)} title="Edit User">
        {editUser && (
          <form onSubmit={handleEdit} className="space-y-4">
            {editError && (
              <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">{editError}</div>
            )}
            <p className="text-sm text-gray-500">{editUser.email}</p>
            <div>
              <label htmlFor="edit-role" className="block text-sm font-medium text-gray-700 mb-1">Role</label>
              <select id="edit-role" value={editRole} onChange={(e) => setEditRole(e.target.value)} className={SETTINGS_INPUT_CLASSNAME}>
                <option value="ADMIN">Admin</option>
                <option value="DISPATCHER">Dispatcher</option>
                <option value="COMPANY_ADMIN">Company admin (read-only)</option>
                <option value="COURIER">Courier</option>
              </select>
            </div>
            <div className="flex items-center gap-3">
              <label className="text-sm font-medium text-gray-700">Active</label>
              <button
                type="button"
                onClick={() => setEditActive((v) => !v)}
                className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${editActive ? "bg-brand-600" : "bg-gray-300"}`}
              >
                <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${editActive ? "translate-x-4" : "translate-x-1"}`} />
              </button>
            </div>
            <div className="flex justify-end gap-3 pt-2">
              <button
                type="button"
                onClick={() => setEditUser(null)}
                className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={saving}
                className="px-4 py-2 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700 disabled:opacity-50"
              >
                {saving ? "Saving…" : "Save Changes"}
              </button>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
}
