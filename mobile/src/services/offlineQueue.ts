import { openDatabaseAsync, type SQLiteDatabase } from "expo-sqlite";
import { ApiError, arriveStop, completeStop, failStop } from "./api";
import { resolveStopCompletionPayload, type StopCompletionWithLocalPodPayload } from "./pod";

const DB_NAME = "lastmile_mobile.db";
const TABLE_NAME = "offline_queue";
const LEGACY_RETRY_DEFAULT = "1970-01-01T00:00:00.000Z";
const RETRY_BASE_DELAY_MS = 30_000;
const RETRY_MAX_DELAY_MS = 15 * 60_000;

let dbPromise: Promise<SQLiteDatabase> | null = null;
let initPromise: Promise<void> | null = null;

type ArriveStopAction = {
  type: "ARRIVE_STOP";
  routeId: string;
  stopId: string;
  payload?: Record<string, never>;
};

type CompleteStopAction = {
  type: "COMPLETE_STOP";
  routeId: string;
  stopId: string;
  payload?: StopCompletionWithLocalPodPayload;
};

type FailStopAction = {
  type: "FAIL_STOP";
  routeId: string;
  stopId: string;
  payload: { failure_reason: string; notes?: string; custom_reason_label?: string };
};

export type OfflineAction = ArriveStopAction | CompleteStopAction | FailStopAction;

export type OfflineQueueItem = {
  id: string;
  action: OfflineAction;
  createdAt: string;
  retryAt: string;
  attempts: number;
  lastError?: string;
};

export type QueueSyncConflict = {
  itemId: string;
  actionType: OfflineAction["type"];
  routeId: string;
  stopId: string;
  reason: string;
};

export type QueueSyncReport = {
  processed: number;
  deferred: number;
  skipped: number;
  conflicts: number;
  conflictItems: QueueSyncConflict[];
  remaining: number;
};

type QueueRow = {
  id: string;
  action_type: OfflineAction["type"];
  route_id: string;
  stop_id: string;
  payload_json: string | null;
  created_at: string;
  next_attempt_at: string;
  attempts: number;
  last_error: string | null;
};

async function getDb() {
  if (!dbPromise) {
    dbPromise = openDatabaseAsync(DB_NAME).catch((error) => {
      dbPromise = null;
      throw error;
    });
  }
  return dbPromise;
}

async function ensureSchema() {
  if (!initPromise) {
    initPromise = (async () => {
      const db = await getDb();
      await db.execAsync(`
        CREATE TABLE IF NOT EXISTS ${TABLE_NAME} (
          id TEXT PRIMARY KEY NOT NULL,
          action_type TEXT NOT NULL,
          route_id TEXT NOT NULL,
          stop_id TEXT NOT NULL,
          payload_json TEXT,
          created_at TEXT NOT NULL,
          next_attempt_at TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0,
          last_error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_offline_queue_created_at ON ${TABLE_NAME}(created_at);
        CREATE INDEX IF NOT EXISTS idx_offline_queue_next_attempt_at ON ${TABLE_NAME}(next_attempt_at);
      `);

      const columns = await db.getAllAsync<{ name: string }>(
        `PRAGMA table_info(${TABLE_NAME})`,
      );
      const hasNextAttemptAt = columns.some((column) => column.name === "next_attempt_at");
      if (!hasNextAttemptAt) {
        await db.runAsync(
          `ALTER TABLE ${TABLE_NAME}
           ADD COLUMN next_attempt_at TEXT NOT NULL DEFAULT '${LEGACY_RETRY_DEFAULT}'`,
        );
        await db.runAsync(
          `UPDATE ${TABLE_NAME}
           SET next_attempt_at = created_at
           WHERE next_attempt_at = ? OR next_attempt_at IS NULL`,
          LEGACY_RETRY_DEFAULT,
        );
      }
    })().catch((error) => {
      initPromise = null;
      throw error;
    });
  }
  return initPromise;
}

function makeItemId(): string {
  return `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function isIdempotentStateError(action: OfflineAction, message: string) {
  if (action.type === "ARRIVE_STOP") {
    return (
      message.includes("Current: ARRIVED") ||
      message.includes("Current: COMPLETED") ||
      message.includes("Current: FAILED")
    );
  }
  if (action.type === "COMPLETE_STOP") {
    return message.includes("Current: COMPLETED");
  }
  if (action.type === "FAIL_STOP") {
    return message.includes("Current: FAILED");
  }
  return false;
}

function isConflictError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  return (
    error.status === 400 ||
    error.status === 403 ||
    error.status === 404 ||
    error.status === 409 ||
    error.status === 422
  );
}

function isNonRetriablePayloadError(message: string): boolean {
  // Backend only allows POD uploads for delivery stops. Treat these payload
  // errors as conflicts rather than retrying forever.
  return message.toLowerCase().includes("pod upload url is only available");
}

function hydrateAction(row: QueueRow): OfflineAction {
  let parsedPayload: Record<string, unknown> | undefined;
  if (row.payload_json) {
    try {
      parsedPayload = JSON.parse(row.payload_json) as Record<string, unknown>;
    } catch {
      parsedPayload = undefined;
    }
  }
  if (row.action_type === "ARRIVE_STOP") {
    return {
      type: "ARRIVE_STOP",
      routeId: row.route_id,
      stopId: row.stop_id,
    };
  }
  if (row.action_type === "COMPLETE_STOP") {
    return {
      type: "COMPLETE_STOP",
      routeId: row.route_id,
      stopId: row.stop_id,
    payload: parsedPayload as StopCompletionWithLocalPodPayload | undefined,
    };
  }
  return {
    type: "FAIL_STOP",
    routeId: row.route_id,
    stopId: row.stop_id,
    payload: parsedPayload as { failure_reason: string; notes?: string; custom_reason_label?: string },
  };
}

function asQueueItem(action: OfflineAction): OfflineQueueItem {
  const createdAt = new Date().toISOString();
  return {
    id: makeItemId(),
    action,
    createdAt,
    retryAt: createdAt,
    attempts: 0,
  };
}

function calculateRetryDelayMs(attempts: number) {
  const multiplier = 2 ** attempts;
  return Math.min(RETRY_BASE_DELAY_MS * multiplier, RETRY_MAX_DELAY_MS);
}

async function insertQueueItem(item: OfflineQueueItem) {
  await ensureSchema();
  const db = await getDb();
  await db.runAsync(
    `INSERT INTO ${TABLE_NAME} (id, action_type, route_id, stop_id, payload_json, created_at, next_attempt_at, attempts, last_error)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    item.id,
    item.action.type,
    item.action.routeId,
    item.action.stopId,
    item.action.payload ? JSON.stringify(item.action.payload) : null,
    item.createdAt,
    item.retryAt,
    item.attempts,
    item.lastError ?? null,
  );
}

async function deleteQueueItem(itemId: string) {
  await ensureSchema();
  const db = await getDb();
  await db.runAsync(`DELETE FROM ${TABLE_NAME} WHERE id = ?`, itemId);
}

async function markDeferred(item: OfflineQueueItem, lastError: string) {
  await ensureSchema();
  const db = await getDb();
  const nextAttemptAt = new Date(
    Date.now() + calculateRetryDelayMs(item.attempts),
  ).toISOString();
  await db.runAsync(
    `UPDATE ${TABLE_NAME}
     SET attempts = ?, last_error = ?, next_attempt_at = ?
     WHERE id = ?`,
    item.attempts + 1,
    lastError,
    nextAttemptAt,
    item.id,
  );
}

async function countQueuedItems(): Promise<number> {
  await ensureSchema();
  const db = await getDb();
  const row = await db.getFirstAsync<{ count: number }>(
    `SELECT COUNT(*) as count FROM ${TABLE_NAME}`,
  );
  return row?.count ?? 0;
}

export async function enqueueOfflineAction(action: OfflineAction) {
  await insertQueueItem(asQueueItem(action));
}

export async function getOfflineQueue(): Promise<OfflineQueueItem[]> {
  await ensureSchema();
  const db = await getDb();
  const rows = await db.getAllAsync<QueueRow>(
    `SELECT id, action_type, route_id, stop_id, payload_json, created_at, next_attempt_at, attempts, last_error
     FROM ${TABLE_NAME}
     ORDER BY next_attempt_at ASC, created_at ASC`,
  );
  return rows.map((row) => ({
    id: row.id,
    action: hydrateAction(row),
    createdAt: row.created_at,
    retryAt: row.next_attempt_at,
    attempts: row.attempts,
    lastError: row.last_error ?? undefined,
  }));
}

export async function clearOfflineQueue() {
  await ensureSchema();
  const db = await getDb();
  await db.runAsync(`DELETE FROM ${TABLE_NAME}`);
}

async function replayAction(action: OfflineAction) {
  if (action.type === "ARRIVE_STOP") {
    await arriveStop(action.routeId, action.stopId);
    return;
  }
  if (action.type === "COMPLETE_STOP") {
    const completionPayload = await resolveStopCompletionPayload(
      action.routeId,
      action.stopId,
      action.payload,
    );
    await completeStop(action.routeId, action.stopId, completionPayload);
    return;
  }
  await failStop(
    action.routeId,
    action.stopId,
    action.payload.failure_reason,
    action.payload.notes,
    action.payload.custom_reason_label,
  );
}

export async function syncOfflineQueue(): Promise<QueueSyncReport> {
  const queue = await getOfflineQueue();
  if (!queue.length) {
    return {
      processed: 0,
      deferred: 0,
      skipped: 0,
      conflicts: 0,
      conflictItems: [],
      remaining: 0,
    };
  }

  let processed = 0;
  let deferred = 0;
  let skipped = 0;
  let conflicts = 0;
  const conflictItems: QueueSyncConflict[] = [];
  const now = Date.now();

  for (const item of queue) {
    if (Date.parse(item.retryAt) > now) {
      skipped += 1;
      continue;
    }
    try {
      await replayAction(item.action);
      processed += 1;
      await deleteQueueItem(item.id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown sync error";
      if (isIdempotentStateError(item.action, message)) {
        processed += 1;
        await deleteQueueItem(item.id);
        continue;
      }
      if (isConflictError(error) || isNonRetriablePayloadError(message)) {
        conflicts += 1;
        conflictItems.push({
          itemId: item.id,
          actionType: item.action.type,
          routeId: item.action.routeId,
          stopId: item.action.stopId,
          reason: message,
        });
        await deleteQueueItem(item.id);
        continue;
      }
      deferred += 1;
      await markDeferred(item, message);
    }
  }

  return {
    processed,
    deferred,
    skipped,
    conflicts,
    conflictItems,
    remaining: await countQueuedItems(),
  };
}
