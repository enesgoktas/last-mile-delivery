import { useCallback, useState, type FormEvent } from "react";
import { useAuth } from "../../state/AuthContext";
import { usePolling } from "../../hooks/usePolling";
import {
  createWebhook,
  deleteWebhook,
  getWebhookLogs,
  listWebhooks,
} from "../../api/client";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Modal } from "../../components/Modal";
import { StatusBadge } from "../../components/StatusBadge";
import type { WebhookDeliveryLog } from "../../types";
import { SETTINGS_INPUT_CLASSNAME } from "./shared";

const WEBHOOK_EVENTS = [
  "order.created",
  "order.assigned",
  "order.picked_up",
  "order.delivered",
  "order.failed",
  "order.cancelled",
  "route.started",
  "route.completed",
];

export function WebhooksTab() {
  const { token } = useAuth();
  const fetchWebhooks = useCallback(() => listWebhooks(token!), [token]);
  const { data: webhooks, refetch } = usePolling(fetchWebhooks, 60_000);

  const [showCreate, setShowCreate] = useState(false);
  const [whClientId, setWhClientId] = useState("");
  const [whUrl, setWhUrl] = useState("");
  const [whEvents, setWhEvents] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [logsForId, setLogsForId] = useState<string | null>(null);
  const [logs, setLogs] = useState<WebhookDeliveryLog[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);

    try {
      await createWebhook(token!, {
        client_id: whClientId,
        url: whUrl,
        events: whEvents,
      });
      setShowCreate(false);
      setWhClientId("");
      setWhUrl("");
      setWhEvents([]);
      refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(id: string) {
    try {
      await deleteWebhook(token!, id);
      refetch();
    } catch {
      // Ignore delete errors here and let polling catch the latest state.
    }
  }

  async function viewLogs(id: string) {
    setLogsForId(id);
    setLogsLoading(true);
    try {
      setLogs(await getWebhookLogs(token!, id));
    } catch {
      setLogs([]);
    } finally {
      setLogsLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">{webhooks?.length ?? 0} webhooks</p>
        <button
          onClick={() => {
            setShowCreate(true);
            setError(null);
          }}
          className="px-3 py-2 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700"
        >
          + Create Webhook
        </button>
      </div>
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {!webhooks?.length ? (
          <div className="p-8 text-center text-gray-400 text-sm">No webhooks</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50/50">
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  URL
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  Events
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  Status
                </th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {webhooks.map((webhook) => (
                <tr key={webhook.id} className="hover:bg-gray-50/50">
                  <td className="px-4 py-3 text-gray-700 truncate max-w-[300px]">
                    {webhook.url}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">
                    {webhook.events.join(", ")}
                  </td>
                  <td className="px-4 py-3">
                    {webhook.is_active ? (
                      <StatusBadge value="ACTIVE" />
                    ) : (
                      <StatusBadge value="OFFLINE" />
                    )}
                  </td>
                  <td className="px-4 py-3 text-right space-x-3">
                    <button
                      onClick={() => viewLogs(webhook.id)}
                      className="text-brand-600 hover:text-brand-700 text-sm font-medium"
                    >
                      Logs
                    </button>
                    <button
                      onClick={() => setDeleteId(webhook.id)}
                      className="text-red-600 hover:text-red-700 text-sm font-medium"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="Create Webhook">
        <form onSubmit={handleCreate} className="space-y-4">
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Client ID</label>
            <input
              value={whClientId}
              onChange={(event) => setWhClientId(event.target.value)}
              className={SETTINGS_INPUT_CLASSNAME}
              required
              placeholder="API client UUID"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">URL</label>
            <input
              type="url"
              value={whUrl}
              onChange={(event) => setWhUrl(event.target.value)}
              className={SETTINGS_INPUT_CLASSNAME}
              required
              placeholder="https://..."
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Events</label>
            <div className="flex flex-wrap gap-2">
              {WEBHOOK_EVENTS.map((webhookEvent) => (
                <button
                  key={webhookEvent}
                  type="button"
                  onClick={() =>
                    setWhEvents((prev) =>
                      prev.includes(webhookEvent)
                        ? prev.filter((value) => value !== webhookEvent)
                        : [...prev, webhookEvent],
                    )
                  }
                  className={`px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors ${
                    whEvents.includes(webhookEvent)
                      ? "border-brand-300 bg-brand-50 text-brand-700"
                      : "border-gray-200 text-gray-600 hover:bg-gray-50"
                  }`}
                >
                  {webhookEvent}
                </button>
              ))}
            </div>
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={() => setShowCreate(false)}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-4 py-2 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700 disabled:opacity-50"
            >
              {loading ? "Creating..." : "Create"}
            </button>
          </div>
        </form>
      </Modal>

      <Modal open={!!logsForId} onClose={() => setLogsForId(null)} title="Delivery Logs" wide>
        {logsLoading ? (
          <p className="text-sm text-gray-400">Loading...</p>
        ) : !logs.length ? (
          <p className="text-sm text-gray-400">No logs</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="text-left px-3 py-2 font-medium text-gray-500 text-xs uppercase">
                  Event
                </th>
                <th className="text-left px-3 py-2 font-medium text-gray-500 text-xs uppercase">
                  Status
                </th>
                <th className="text-left px-3 py-2 font-medium text-gray-500 text-xs uppercase">
                  HTTP
                </th>
                <th className="text-left px-3 py-2 font-medium text-gray-500 text-xs uppercase">
                  Sent
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {logs.map((log) => (
                <tr key={log.id}>
                  <td className="px-3 py-2 text-gray-700">{log.event_type}</td>
                  <td className="px-3 py-2">
                    <StatusBadge value={log.delivery_status} />
                  </td>
                  <td className="px-3 py-2 text-gray-600">{log.response_status ?? "—"}</td>
                  <td className="px-3 py-2 text-gray-500 text-xs">
                    {new Date(log.sent_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Modal>

      <ConfirmDialog
        open={!!deleteId}
        onCancel={() => setDeleteId(null)}
        onConfirm={() => {
          if (deleteId) {
            handleDelete(deleteId);
            setDeleteId(null);
          }
        }}
        title="Delete webhook?"
        message="This endpoint will stop receiving events."
        confirmLabel="Delete"
        danger
      />
    </div>
  );
}
