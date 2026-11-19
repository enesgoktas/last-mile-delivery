type QueueRow = {
  id: string;
  action_type: "ARRIVE_STOP" | "COMPLETE_STOP" | "FAIL_STOP";
  route_id: string;
  stop_id: string;
  payload_json: string | null;
  created_at: string;
  next_attempt_at: string;
  attempts: number;
  last_error: string | null;
};

class FakeSqliteDb {
  private readonly rows = new Map<string, QueueRow>();

  async execAsync(): Promise<void> {
    return;
  }

  async runAsync(sql: string, ...params: unknown[]): Promise<void> {
    const normalized = sql.replace(/\s+/g, " ").trim().toUpperCase();

    if (normalized.startsWith("INSERT INTO OFFLINE_QUEUE")) {
      const [
        id,
        actionType,
        routeId,
        stopId,
        payloadJson,
        createdAt,
        nextAttemptAt,
        attempts,
        lastError,
      ] = params;
      this.rows.set(String(id), {
        id: String(id),
        action_type: actionType as QueueRow["action_type"],
        route_id: String(routeId),
        stop_id: String(stopId),
        payload_json: payloadJson == null ? null : String(payloadJson),
        created_at: String(createdAt),
        next_attempt_at: String(nextAttemptAt),
        attempts: Number(attempts),
        last_error: lastError == null ? null : String(lastError),
      });
      return;
    }

    if (normalized.startsWith("DELETE FROM OFFLINE_QUEUE WHERE ID = ?")) {
      this.rows.delete(String(params[0]));
      return;
    }

    if (normalized.startsWith("DELETE FROM OFFLINE_QUEUE")) {
      this.rows.clear();
      return;
    }

    if (normalized.startsWith("UPDATE OFFLINE_QUEUE SET ATTEMPTS = ?")) {
      const [attempts, lastError, nextAttemptAt, id] = params;
      const row = this.rows.get(String(id));
      if (!row) {
        return;
      }
      row.attempts = Number(attempts);
      row.last_error = lastError == null ? null : String(lastError);
      row.next_attempt_at = String(nextAttemptAt);
      this.rows.set(String(id), row);
      return;
    }

    if (normalized.startsWith("ALTER TABLE OFFLINE_QUEUE ADD COLUMN NEXT_ATTEMPT_AT")) {
      return;
    }

    if (normalized.startsWith("UPDATE OFFLINE_QUEUE SET NEXT_ATTEMPT_AT = CREATED_AT")) {
      const [legacyDefault] = params;
      for (const row of this.rows.values()) {
        if (!row.next_attempt_at || row.next_attempt_at === String(legacyDefault)) {
          row.next_attempt_at = row.created_at;
        }
      }
      return;
    }

    throw new Error(`Unhandled SQL in fake DB: ${sql}`);
  }

  async getAllAsync<T>(sql?: string): Promise<T[]> {
    const normalized = (sql ?? "").replace(/\s+/g, " ").trim().toUpperCase();
    if (normalized.startsWith("PRAGMA TABLE_INFO(OFFLINE_QUEUE)")) {
      return [
        { name: "id" },
        { name: "action_type" },
        { name: "route_id" },
        { name: "stop_id" },
        { name: "payload_json" },
        { name: "created_at" },
        { name: "next_attempt_at" },
        { name: "attempts" },
        { name: "last_error" },
      ] as T[];
    }

    const rows = [...this.rows.values()].sort((a, b) => {
      const retrySort = a.next_attempt_at.localeCompare(b.next_attempt_at);
      if (retrySort !== 0) {
        return retrySort;
      }
      return a.created_at.localeCompare(b.created_at);
    });
    return rows as T[];
  }

  async getFirstAsync<T>(): Promise<T> {
    return { count: this.rows.size } as T;
  }
}

class MockApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function setupQueueHarness(options?: { failOpenDbTimes?: number }) {
  jest.resetModules();

  const fakeDb = new FakeSqliteDb();
  let remainingOpenDbFailures = options?.failOpenDbTimes ?? 0;
  const openDatabaseAsync = jest.fn(async () => {
    if (remainingOpenDbFailures > 0) {
      remainingOpenDbFailures -= 1;
      throw new Error("db unavailable");
    }
    return fakeDb;
  });
  const arriveStop = jest.fn(async () => ({ status: "ARRIVED" }));
  const completeStop = jest.fn(async () => ({ status: "COMPLETED" }));
  const failStop = jest.fn(async () => ({ status: "FAILED" }));
  const resolveStopCompletionPayload = jest.fn(async (_routeId: string, _stopId: string, payload: unknown) => payload);

  jest.doMock("expo-sqlite", () => ({
    openDatabaseAsync,
  }));
  jest.doMock("../api", () => ({
    ApiError: MockApiError,
    arriveStop,
    completeStop,
    failStop,
  }));
  jest.doMock("../pod", () => ({
    resolveStopCompletionPayload,
  }));

  const offlineQueue = require("../offlineQueue");
  return {
    offlineQueue,
    arriveStop,
    completeStop,
    failStop,
    resolveStopCompletionPayload,
  };
}

describe("offlineQueue", () => {
  test("syncOfflineQueue processes queued actions and clears queue", async () => {
    const { offlineQueue, arriveStop, completeStop, resolveStopCompletionPayload } =
      setupQueueHarness();

    await offlineQueue.enqueueOfflineAction({
      type: "ARRIVE_STOP",
      routeId: "route-1",
      stopId: "stop-1",
    });
    await offlineQueue.enqueueOfflineAction({
      type: "COMPLETE_STOP",
      routeId: "route-1",
      stopId: "stop-2",
      payload: { notes: "doorstep", photo_local_uris: ["file:///tmp/photo.jpg"] },
    });

    resolveStopCompletionPayload.mockResolvedValueOnce({
      notes: "doorstep",
      photo_urls: ["https://cdn.example.com/photo.jpg"],
    });

    const report = await offlineQueue.syncOfflineQueue();
    const queue = await offlineQueue.getOfflineQueue();

    expect(report).toMatchObject({
      processed: 2,
      deferred: 0,
      conflicts: 0,
      remaining: 0,
    });
    expect(arriveStop).toHaveBeenCalledWith("route-1", "stop-1");
    expect(completeStop).toHaveBeenCalledWith("route-1", "stop-2", {
      notes: "doorstep",
      photo_urls: ["https://cdn.example.com/photo.jpg"],
    });
    expect(queue).toHaveLength(0);
  });

  test("syncOfflineQueue drops conflicting actions and reports conflicts", async () => {
    const { offlineQueue, failStop } = setupQueueHarness();

    failStop.mockRejectedValueOnce(new MockApiError("stop already reassigned", 409));

    await offlineQueue.enqueueOfflineAction({
      type: "FAIL_STOP",
      routeId: "route-2",
      stopId: "stop-9",
      payload: { failure_reason: "NOT_HOME", notes: "no answer" },
    });

    const report = await offlineQueue.syncOfflineQueue();

    expect(report.processed).toBe(0);
    expect(report.deferred).toBe(0);
    expect(report.conflicts).toBe(1);
    expect(report.remaining).toBe(0);
    expect(report.conflictItems).toHaveLength(1);
    expect(report.conflictItems[0]).toMatchObject({
      actionType: "FAIL_STOP",
      routeId: "route-2",
      stopId: "stop-9",
    });
  });

  test("syncOfflineQueue defers transient errors and increments attempts", async () => {
    const { offlineQueue, arriveStop } = setupQueueHarness();

    arriveStop.mockRejectedValueOnce(new Error("network timeout"));

    await offlineQueue.enqueueOfflineAction({
      type: "ARRIVE_STOP",
      routeId: "route-5",
      stopId: "stop-5",
    });

    const report = await offlineQueue.syncOfflineQueue();
    const queued = await offlineQueue.getOfflineQueue();

    expect(report).toMatchObject({
      processed: 0,
      deferred: 1,
      conflicts: 0,
      remaining: 1,
    });
    expect(queued).toHaveLength(1);
    expect(queued[0].attempts).toBe(1);
    expect(queued[0].lastError).toContain("network timeout");
    expect(new Date(queued[0].retryAt).getTime()).toBeGreaterThan(Date.now());
  });

  test("syncOfflineQueue treats idempotent state errors as processed", async () => {
    const { offlineQueue, arriveStop } = setupQueueHarness();

    arriveStop.mockRejectedValueOnce(new Error("Invalid stop transition. Current: ARRIVED"));

    await offlineQueue.enqueueOfflineAction({
      type: "ARRIVE_STOP",
      routeId: "route-7",
      stopId: "stop-7",
    });

    const report = await offlineQueue.syncOfflineQueue();
    const queued = await offlineQueue.getOfflineQueue();

    expect(report).toMatchObject({
      processed: 1,
      deferred: 0,
      conflicts: 0,
      remaining: 0,
    });
    expect(queued).toHaveLength(0);
  });

  test("syncOfflineQueue drops non-retriable payload errors as conflicts", async () => {
    const { offlineQueue, resolveStopCompletionPayload } = setupQueueHarness();

    resolveStopCompletionPayload.mockRejectedValueOnce(
      new Error("1 photo upload(s) failed: POD upload URL is only available for delivery stops"),
    );

    await offlineQueue.enqueueOfflineAction({
      type: "COMPLETE_STOP",
      routeId: "route-11",
      stopId: "stop-11",
      payload: { photo_local_uris: ["file:///tmp/photo.jpg"] },
    });

    const report = await offlineQueue.syncOfflineQueue();
    const queued = await offlineQueue.getOfflineQueue();

    expect(report).toMatchObject({
      processed: 0,
      deferred: 0,
      conflicts: 1,
      remaining: 0,
    });
    expect(report.conflictItems[0]).toMatchObject({
      actionType: "COMPLETE_STOP",
      routeId: "route-11",
      stopId: "stop-11",
    });
    expect(queued).toHaveLength(0);
  });

  test("syncOfflineQueue preserves action order and continues after conflicts", async () => {
    const { offlineQueue, failStop, arriveStop } = setupQueueHarness();

    failStop.mockRejectedValueOnce(new MockApiError("stop already reassigned", 409));

    await offlineQueue.enqueueOfflineAction({
      type: "FAIL_STOP",
      routeId: "route-4",
      stopId: "stop-1",
      payload: { failure_reason: "NOT_HOME", notes: "not reachable" },
    });
    await offlineQueue.enqueueOfflineAction({
      type: "ARRIVE_STOP",
      routeId: "route-4",
      stopId: "stop-2",
    });

    const report = await offlineQueue.syncOfflineQueue();
    const queued = await offlineQueue.getOfflineQueue();

    expect(report).toMatchObject({
      processed: 1,
      deferred: 0,
      conflicts: 1,
      remaining: 0,
    });
    expect(report.conflictItems[0]).toMatchObject({
      actionType: "FAIL_STOP",
      stopId: "stop-1",
    });
    expect(queued).toHaveLength(0);
    expect(failStop).toHaveBeenCalledWith("route-4", "stop-1", "NOT_HOME", "not reachable", undefined);
    expect(arriveStop).toHaveBeenCalledWith("route-4", "stop-2");
    expect(failStop.mock.invocationCallOrder[0]).toBeLessThan(
      arriveStop.mock.invocationCallOrder[0],
    );
  });

  test("syncOfflineQueue skips items waiting for retry window", async () => {
    const { offlineQueue, arriveStop } = setupQueueHarness();

    arriveStop.mockRejectedValueOnce(new Error("network timeout"));

    await offlineQueue.enqueueOfflineAction({
      type: "ARRIVE_STOP",
      routeId: "route-8",
      stopId: "stop-8",
    });

    const firstReport = await offlineQueue.syncOfflineQueue();
    const secondReport = await offlineQueue.syncOfflineQueue();

    expect(firstReport).toMatchObject({
      processed: 0,
      deferred: 1,
      skipped: 0,
      conflicts: 0,
      remaining: 1,
    });
    expect(secondReport).toMatchObject({
      processed: 0,
      deferred: 0,
      skipped: 1,
      conflicts: 0,
      remaining: 1,
    });
    expect(arriveStop).toHaveBeenCalledTimes(1);
  });

  test("retries database initialization after transient open failure", async () => {
    const { offlineQueue } = setupQueueHarness({ failOpenDbTimes: 1 });

    await expect(
      offlineQueue.enqueueOfflineAction({
        type: "ARRIVE_STOP",
        routeId: "route-db",
        stopId: "stop-db-1",
      }),
    ).rejects.toThrow("db unavailable");

    await offlineQueue.enqueueOfflineAction({
      type: "ARRIVE_STOP",
      routeId: "route-db",
      stopId: "stop-db-2",
    });

    const queue = await offlineQueue.getOfflineQueue();
    expect(queue).toHaveLength(1);
    expect(queue[0].action.stopId).toBe("stop-db-2");
  });
});
