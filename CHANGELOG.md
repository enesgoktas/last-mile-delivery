# Changelog

All notable changes to Last-Mile Delivery are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Commits use the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) spec so that
changelog entries and version bumps can be automated by the release-drafter workflow.

---

## [Unreleased]

### Changed
- README rewritten as technical-architecture document (sections 1–10 plus appendices): system overview diagram, routing engine deep-dive (ALNS + HGS + SP-recombination + CP-SAT), order lifecycle state diagram, fleet constraints table, mobile offline sequence, customer tracking sequence, decomposition pipeline diagram, auth/security table, ops stack table.

### Added
- Phase 7: CI/CD & Release improvements
  - Frontend and tracking CD workflow (`web-cd.yml`) — S3 + CloudFront deploy
  - Mobile CD workflow (`mobile-cd.yml`) — EAS build for dev/preview/production profiles
  - Ruff linting in backend CI with `pyproject.toml` rule set
  - Test coverage tracking via `pytest-cov`, `@vitest/coverage-v8`, Jest `--coverage`; reports uploaded to Codecov
  - `npm run lint` (TypeScript type-check) added to frontend, tracking, and mobile `package.json`
  - Lint step added to `reusable-web-mobile-ci.yml`
  - `release-readiness` workflow now auto-triggers on push to `release/**`/`hotfix/**` branches, version tags, and PRs targeting `main`
  - Docker BuildKit layer caching (`docker/build-push-action`) in `backend-cd.yml`
  - Healthchecks for `api` (curl), `celery_worker` (inspect ping), `celery_beat` (pidfile) in `docker-compose.yml`
  - `dev.sh` now supports `--tracking` (backend + tracking SPA) and `--all` (backend + frontend + tracking in parallel) flags
  - This `CHANGELOG.md` and `release-drafter` automation config

- Phase 6: Test Coverage
  - Frontend Vitest: LiveOpsPage, LoginPage, SLAAlertsPage, OperationsPolicyTab, UsersTab, AnalyticsPage tests (109 tests total)
  - Playwright E2E: data-testid attributes, wrong-password error, analytics time-range tabs, settings page
  - Mobile Jest: MyRouteScreen helper tests, StopDetailScreen policy tests (68 tests total)
  - Tracking Vitest: TrackingMap, api.ts, expanded App.test.tsx (38 tests total)
  - Backend pytest: negative-path integration tests (422/403/409), OpenAPI contract tests, expanded pod-policy tests (246 tests total)

- Phases 0–5: Core feature completeness
  - Proof-of-delivery (photo, signature, barcode, safe-drop, receiver name)
  - Operations policy customization endpoint and UI tab
  - SLA monitoring and alert system
  - Audit logging API
  - Data retention service (batched deletion)
  - Analytics aggregation service
  - Rate limiting, JWT revocation, RBAC enforcement
  - Tracking SPA: rescheduling, ETA visibility modes, WCAG 2.1 AA focus styles
  - Mobile offline queue, push notification handler, JWT expiry detection

---

## How to create a release

1. Ensure all CI checks pass on `main`.
2. Create a branch `release/vX.Y.Z` (or push a tag `vX.Y.Z` directly).
3. The `release-readiness` workflow runs automatically.
4. Once it passes, create the GitHub Release — release-drafter will pre-populate the notes from merged PRs.
5. Merge the release branch back to `main` if needed.

[Unreleased]: https://github.com/Comp491-Spring26/LastMileDelivery/compare/HEAD...HEAD
