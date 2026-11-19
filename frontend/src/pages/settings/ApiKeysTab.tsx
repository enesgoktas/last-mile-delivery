import { useCallback, useState, type FormEvent } from "react";
import { useAuth } from "../../state/AuthContext";
import { usePolling } from "../../hooks/usePolling";
import { createApiKey, listApiKeys, revokeApiKey } from "../../api/client";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Modal } from "../../components/Modal";
import { StatusBadge } from "../../components/StatusBadge";
import { SETTINGS_INPUT_CLASSNAME } from "./shared";

const API_KEY_SCOPES = [
  "orders:read",
  "orders:create",
  "orders:update",
  "couriers:read",
  "routes:read",
  "webhooks:read",
];

export function ApiKeysTab() {
  const { token } = useAuth();
  const fetchKeys = useCallback(() => listApiKeys(token!), [token]);
  const { data: apiKeys, refetch } = usePolling(fetchKeys, 60_000);

  const [showModal, setShowModal] = useState(false);
  const [keyName, setKeyName] = useState("");
  const [keyScopes, setKeyScopes] = useState<string[]>([]);
  const [keyRateLimit, setKeyRateLimit] = useState("60");
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [revokeId, setRevokeId] = useState<string | null>(null);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const result = await createApiKey(token!, {
        name: keyName,
        permissions: keyScopes,
        rate_limit_per_min: Number(keyRateLimit),
      });
      setCreatedKey(result.api_key);
      refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleRevoke(id: string) {
    try {
      await revokeApiKey(token!, id);
      refetch();
    } catch {
      // Ignore revoke errors here and let polling catch the latest state.
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">{apiKeys?.length ?? 0} API keys</p>
        <button
          onClick={() => {
            setShowModal(true);
            setCreatedKey(null);
            setKeyName("");
            setKeyScopes([]);
            setKeyRateLimit("60");
            setError(null);
          }}
          className="px-3 py-2 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700"
        >
          + Create Key
        </button>
      </div>
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {!apiKeys?.length ? (
          <div className="p-8 text-center text-gray-400 text-sm">No API keys</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50/50">
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  Name
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  Scopes
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  Rate
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  Status
                </th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {apiKeys.map((key) => (
                <tr key={key.id} className="hover:bg-gray-50/50">
                  <td className="px-4 py-3 font-medium text-gray-700">{key.name}</td>
                  <td className="px-4 py-3 text-xs text-gray-500">
                    {key.permissions.join(", ")}
                  </td>
                  <td className="px-4 py-3 text-gray-600">{key.rate_limit_per_min}/min</td>
                  <td className="px-4 py-3">
                    {key.is_active ? (
                      <StatusBadge value="ACTIVE" />
                    ) : (
                      <StatusBadge value="REVOKED" />
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {key.is_active && (
                      <button
                        onClick={() => setRevokeId(key.id)}
                        className="text-red-600 hover:text-red-700 text-sm font-medium"
                      >
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <Modal
        open={showModal}
        onClose={() => setShowModal(false)}
        title={createdKey ? "API Key Created" : "Create API Key"}
      >
        {createdKey ? (
          <div className="space-y-4">
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
              <p className="text-sm text-amber-800 font-medium mb-2">
                Copy this key now — it won't be shown again.
              </p>
              <code className="block bg-white border border-amber-200 rounded px-3 py-2 text-sm font-mono text-gray-800 break-all">
                {createdKey}
              </code>
            </div>
            <button
              onClick={() => setShowModal(false)}
              className="w-full px-4 py-2 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700"
            >
              Done
            </button>
          </div>
        ) : (
          <form onSubmit={handleCreate} className="space-y-4">
            {error && (
              <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
                {error}
              </div>
            )}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
              <input
                value={keyName}
                onChange={(event) => setKeyName(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">Scopes</label>
              <div className="flex flex-wrap gap-2">
                {API_KEY_SCOPES.map((scope) => (
                  <button
                    key={scope}
                    type="button"
                    onClick={() =>
                      setKeyScopes((prev) =>
                        prev.includes(scope)
                          ? prev.filter((value) => value !== scope)
                          : [...prev, scope],
                      )
                    }
                    className={`px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors ${
                      keyScopes.includes(scope)
                        ? "border-brand-300 bg-brand-50 text-brand-700"
                        : "border-gray-200 text-gray-600 hover:bg-gray-50"
                    }`}
                  >
                    {scope}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Rate Limit
              </label>
              <input
                type="number"
                value={keyRateLimit}
                onChange={(event) => setKeyRateLimit(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
              />
            </div>
            <div className="flex justify-end gap-3 pt-2">
              <button
                type="button"
                onClick={() => setShowModal(false)}
                className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={loading}
                className="px-4 py-2 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700 disabled:opacity-50"
              >
                {loading ? "Creating..." : "Create Key"}
              </button>
            </div>
          </form>
        )}
      </Modal>
      <ConfirmDialog
        open={!!revokeId}
        onCancel={() => setRevokeId(null)}
        onConfirm={() => {
          if (revokeId) {
            handleRevoke(revokeId);
            setRevokeId(null);
          }
        }}
        title="Revoke API key?"
        message="This key will immediately stop working."
        confirmLabel="Revoke Key"
        danger
      />
    </div>
  );
}
