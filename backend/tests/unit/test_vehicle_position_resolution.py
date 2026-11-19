import pytest
from unittest.mock import AsyncMock

from app.models.courier import Courier
from app.algorithms.vrp_solver import VehicleData
from app.services.dispatch_vrp_helpers import resolve_vehicle_positions


@pytest.mark.asyncio
async def test_resolve_vehicle_positions(monkeypatch):
    c1 = Courier(id="11111111-1111-1111-1111-111111111111")
    c2 = Courier(id="22222222-2222-2222-2222-222222222222")

    locations = [
        (40.0, -70.0), # 0
        (41.0, -71.0), # 1
        (42.0, -72.0), # 2
    ]

    v1 = VehicleData(courier_id=str(c1.id), vehicle_type=0, capacity_grams=1, depot_index=1)
    v2 = VehicleData(courier_id=str(c2.id), vehicle_type=0, capacity_grams=1, depot_index=2)

    db = AsyncMock()

    # Mock cache
    cache = {
        c1.id: {"lat": 10.0, "lon": 20.0},
        c2.id: None, # fallback
    }

    async def mock_get(cid, db):
        return cache.get(cid)

    import app.services.courier_service
    monkeypatch.setattr(
        app.services.courier_service, 
        "get_courier_location_from_cache", 
        mock_get, 
        raising=False
    )

    resolved = await resolve_vehicle_positions(
        [c1, c2], locations, [v1, v2], db
    )

    # v1 has cache, v2 falls back to depot_index 2
    assert resolved == [
        (10.0, 20.0), # from cache
        (42.0, -72.0), # fallback to loc[2]
    ]
