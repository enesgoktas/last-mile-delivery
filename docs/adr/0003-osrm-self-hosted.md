# ADR 0003: Self-hosted OSRM Distance Matrix

- Status: accepted
- Context: Per-request map API pricing is cost-prohibitive at scale.
- Decision: Use self-hosted OSRM + Redis matrix cache for route and matrix generation.
- Consequences:
  - Low unit routing cost.
  - Requires map data management and uptime ownership.

Current implementation note:

- `backend/app/algorithms/osrm_client.py` fails loudly for planning matrix and route-geometry failures after retry. It does not currently use a haversine or straight-line fallback for dispatch planning or map geometry.
- OSRM is an open-source road-routing engine, not our courier-assignment logic. In this app it supplies road routes, travel-time matrices, route geometry, and nearest-road snapping. Courier assignment and stop sequencing are handled above OSRM by our dispatch code and optimization model.
- OSRM is BSD-2-Clause licensed. The Istanbul road data prepared for OSRM is derived from OpenStreetMap data, which has separate ODbL attribution and data-sharing obligations. This note is an engineering summary, not legal advice.
