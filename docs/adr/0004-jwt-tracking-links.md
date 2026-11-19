# ADR 0004: JWT Customer Tracking Links
- Status: accepted
- Context: Customer tracking should require no account creation and remain scoped.
- Decision: Tracking links use signed JWT with order scope and expiry.
- Consequences:
  - Low-friction customer experience.
  - Requires token expiry UX and revocation strategy for abuse cases.
