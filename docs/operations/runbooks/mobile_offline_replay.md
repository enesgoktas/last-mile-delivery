# Mobile Offline Replay Runbook

## Objective
Validate courier offline queue replay, automatic reconnect sync, and conflict handling for stop actions.

## Prerequisites
- Backend API running.
- Mobile app running on emulator or device.
- A courier account with an active route containing at least two pending stops.

## Scenario A: Offline Replay Success
1. Open courier app and navigate to `My Route`.
2. Disable network on the device/emulator.
3. Open stop detail and execute one action:
 - `Mark Arrived`, or
 - `Complete Stop` with optional POD attachment.
4. Confirm app shows offline-queued confirmation.
5. Return to `My Route` and verify queued count increased.
6. Re-enable network.
7. Wait up to 30 seconds for automatic background sync (or tap `Sync Queue`).
8. Expected:
 - Sync report shows processed actions > 0.
 - Remaining queue count becomes 0.
 - Backend route stop state reflects the replayed action.
 - Background sync summary appears in `My Route` when auto-sync runs.

## Scenario B: Conflict Handling (Reassigned/Invalidated Stop)
1. Queue an offline action for a stop while offline.
2. Before reconnecting mobile, reassign/cancel/complete that stop via dispatcher/backend.
3. Re-enable network and tap `Sync Queue`.
4. Expected:
 - Sync report shows `conflicts > 0`.
 - Conflict item includes action type + stop reference + reason.
 - Conflicting action is removed from queue (no endless retries).
 - Non-conflicting queued actions continue replaying in order.

## Scenario C: Transient Error Deferral
1. Queue action offline.
2. Re-enable network but make API temporarily unavailable (simulate 5xx / timeout).
3. Trigger sync.
4. Expected:
 - Sync report shows `deferred > 0`.
 - Queue item remains with incremented attempt count, future `retryAt`, and last error message.
5. Immediately trigger sync again before retry window expires.
6. Expected:
 - Sync report shows `skipped > 0` for retry-window-protected items.
 - API is not called again for skipped items until retry window is reached.
7. Wait for retry window or reconnect later and sync again.
8. Expected:
 - Deferred item eventually processes and queue clears.

## Evidence to Capture
- Mobile screenshots: queued count before/after sync, sync report modal.
- Backend state proof: route stop status before/after replay.
- Logs: mobile sync summary and backend request traces for replayed actions.
