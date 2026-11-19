import { useEffect, useState } from "react";
import { Battery, RefreshCw, Zap } from "lucide-react";
import { useAuth } from "../../state/AuthContext";
import {
  ChargingStationProviderStatus,
  ChargingStationSyncSummary,
  getChargingStationSyncStatus,
  triggerChargingStationSync,
} from "../../api/superadmin";

export function ChargingStationsTab() {
  const { token } = useAuth();
  const [status, setStatus] = useState<ChargingStationProviderStatus[] | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [lastSync, setLastSync] = useState<ChargingStationSyncSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    if (!token) return;
    try {
      const r = await getChargingStationSyncStatus(token);
      setStatus(r.by_provider);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }

  useEffect(() => {
    void refresh();
  }, [token]);

  async function handleSync() {
    if (!token) return;
    setSyncing(true);
    setError(null);
    try {
      const summary = await triggerChargingStationSync(token);
      setLastSync(summary);
      if (summary.error) {
        setError(summary.error);
      }
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div className="px-8 py-8 max-w-6xl mx-auto w-full">
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-slate-900 flex items-center gap-2">
            <Zap size={18} className="text-accent-500" />
            Charging stations
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Platform-wide EV charger catalogue. Refreshed weekly from Open
            Charge Map; dispatch reads exclusively from our local table, so
            OCM is never a runtime dependency.
          </p>
        </div>
        <button
          onClick={() => void handleSync()}
          disabled={syncing}
          className="inline-flex items-center gap-1.5 h-9 px-3 bg-accent-500 hover:bg-accent-600 disabled:bg-slate-300 text-white text-sm font-medium rounded-md transition-colors"
        >
          <RefreshCw size={14} className={syncing ? "animate-spin" : ""} />
          {syncing ? "Syncing…" : "Run sync now"}
        </button>
      </div>

      {error && (
        <div className="mb-4 px-3 py-2 bg-red-50 border border-red-200 text-red-700 text-sm rounded">
          {error}
          {error.includes("OPEN_CHARGE_MAP_API_KEY") && (
            <p className="mt-1 text-xs text-red-600">
              Set <code className="font-mono">OPEN_CHARGE_MAP_API_KEY</code> in
              the backend's environment to enable syncs.
            </p>
          )}
        </div>
      )}

      {lastSync && !lastSync.error && (
        <div className="mb-4 px-3 py-2 bg-emerald-50 border border-emerald-200 text-emerald-800 text-sm rounded">
          Last sync: {lastSync.fetched} fetched · {lastSync.inserted} new ·{" "}
          {lastSync.updated} updated · {lastSync.marked_inactive} retired.
        </div>
      )}

      <h2 className="text-sm font-semibold text-slate-700 mt-6 mb-3 flex items-center gap-2">
        <Battery size={14} className="text-slate-400" />
        Inventory by provider
      </h2>

      <div className="border border-slate-200 rounded-md overflow-hidden bg-white">
        <div className="grid grid-cols-[1fr_120px_120px_180px] px-4 py-2 text-[10px] font-semibold uppercase tracking-widest text-slate-400 bg-slate-50 border-b border-slate-200">
          <div>Provider</div>
          <div className="text-right">Total</div>
          <div className="text-right">Active</div>
          <div className="text-right">Last synced</div>
        </div>
        {status === null && (
          <div className="px-4 py-6 text-sm text-slate-400">Loading…</div>
        )}
        {status && status.length === 0 && (
          <div className="px-4 py-8 text-sm text-slate-400 text-center">
            No charging stations yet. Click "Run sync now" to pull from Open Charge Map.
          </div>
        )}
        {status &&
          status.map((row) => (
            <div
              key={row.provider}
              className="grid grid-cols-[1fr_120px_120px_180px] items-center px-4 py-2.5 border-b border-slate-100 last:border-b-0 text-sm tabular-nums"
            >
              <div className="text-slate-800 font-medium">{row.provider}</div>
              <div className="text-right text-slate-700">{row.total}</div>
              <div className="text-right text-emerald-700">{row.active}</div>
              <div className="text-right text-xs text-slate-500">
                {row.last_synced_at
                  ? new Date(row.last_synced_at).toLocaleString()
                  : "—"}
              </div>
            </div>
          ))}
      </div>

      <p className="mt-6 text-xs text-slate-400">
        Open Charge Map refreshes weekly via Celery beat. Dispatch reads
        exclusively from our local catalogue, so an OCM outage never
        blocks routing — at worst we fall behind on new stations until
        the next sync. Manual sync above runs the same task synchronously.
      </p>
    </div>
  );
}
