# ADR 0001: Modular Monolith
- Status: accepted
- Context: Startup-stage delivery platform requires high speed with low operational overhead.
- Decision: Keep backend as modular monolith (FastAPI + SQLAlchemy + Celery workers).
- Consequences:
  - Faster iteration than microservices.
  - Clear bounded modules still required to avoid entanglement.
  - Can split services later if load or team topology requires it.
