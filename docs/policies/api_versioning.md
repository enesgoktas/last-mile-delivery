# API Versioning and Compatibility Policy

- Public API base path is frozen at `/api/v1`.
- Additive changes are preferred (new optional fields/endpoints).
- Breaking changes require:
  1. version bump (`/api/v2`),
  2. deprecation notice,
  3. migration guide,
  4. compatibility window.
- External API key clients must not break silently.
