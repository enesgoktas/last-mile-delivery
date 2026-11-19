# Redis Loss and Worker Replay Tabletop

- Conducted at: `2026-03-27T05:10:29Z`
- Environment: `tabletop`
- Scenario: `redis_loss_worker_replay`
- Result: `PASS_WITH_LIMITS`

## Objective
Verify the expected operational response if Redis state is lost during an active delivery window.

## Assumptions
- PostgreSQL remains healthy and is still the source of truth.
- Redis-backed rate limits, revocation state, Celery broker state, and reoptimization locks are lost.
- Workers can be restarted after Redis is rebuilt.

## Expected Recovery Steps
1. Freeze new deploys and stop accepting risky operational changes.
2. Rebuild or replace Redis.
3. Restart API and worker processes so they reconnect to the rebuilt broker/cache.
4. Reconstruct durable work from PostgreSQL-backed truth where possible:
   - optimization runs
   - delivery logs
   - order state
5. Accept transient loss of revocation and rate-limit counters until they repopulate.

## Outcome
- The declared DR posture remains consistent: PostgreSQL is durable, Redis is rebuildable runtime state.
- A real staging replay drill is still needed to prove worker recovery timing and job replay behavior.

## Follow-Up
- Run a staging broker rebuild drill after the deployment platform is chosen.
- Capture actual worker restart and replay timings.
