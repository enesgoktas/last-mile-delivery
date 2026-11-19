# Mobile Route Lifecycle QA

## Goal
Validate courier mobile app critical path in emulator/device:
- login/logout
- active route fetch + next-stop summary
- stop arrive/complete/fail actions
- offline queue and sync behavior
- POD attachment flow

## Preconditions
1. Backend API running with Redis and Postgres.
2. Seeded courier user with assigned route and at least two stops.
3. Mobile environment variable `EXPO_PUBLIC_API_BASE_URL` points to reachable backend.
4. Expo app running on emulator or physical device.

## Test Steps
1. Login with courier credentials and verify My Route screen loads.
2. Confirm active route status and stop list are visible.
3. Tap first stop; execute `Mark Arrived`; verify success alert.
4. Attach photo/signature (optional) and execute `Complete Stop`; verify success and return.
5. For second stop, choose failure reason `OTHER` with empty note and verify validation message.
6. Provide note and execute `Fail Stop`; verify success and return.
7. Disable network, perform one action (arrive/complete/fail), verify `Queued offline` alert.
8. Re-enable network and trigger `Sync Queue`; verify processed/deferred/conflict counts.
9. Trigger `Sync Queue` immediately again if deferred items exist; verify `Skipped` reflects retry window.
10. Trigger logout and verify navigation returns to login screen.
11. Queue an offline action, re-enable network, and wait up to 30 seconds without tapping sync.
12. Verify automatic background sync updates queue count and background summary card.

## Expected Results
- No crashes during lifecycle actions.
- Queue count increments while offline and drains after sync.
- Deferred items are not retried continuously; they are skipped until retry window.
- Conflict banner appears only when server returns conflict statuses.
- POD attachments are retained in completion payload and uploaded when online.

## Evidence Capture
- Screenshots for each major step.
- API logs confirming state transitions.
- Sync summary output for offline replay.
