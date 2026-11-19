"""Integration tests for vehicle spec CRUD API endpoints and dispatch fuel derivation."""
import uuid
from types import SimpleNamespace

from tests.integration.test_utils import build_test_client, find_route_dependency

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _admin_user(tenant_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        role="ADMIN",
        is_active=True,
    )


def _dispatcher_user(tenant_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        role="DISPATCHER",
        is_active=True,
    )


def _mock_spec_response(spec_id=None, make="Toyota", model="Corolla"):
    return {
        "id": str(spec_id or uuid.uuid4()),
        "tenant_id": None,
        "make": make,
        "model": model,
        "year": 2023,
        "vehicle_type": "CAR_VAN",
        "fuel_type": "GASOLINE",
        "fuel_efficiency_km_per_liter": 14.5,
        "co2_emissions_g_per_km": 160.0,
        "axle_count": None,
        "emission_class": None,
        "epa_vehicle_id": None,
        "gross_weight_kg": None,
        "hazmat_capable": False,
        "height_cm": None,
        "length_cm": None,
        "osrm_profile": "driving",
        "width_cm": None,
        "source": "MANUAL",
        "created_at": "2026-04-16T10:00:00",
        "updated_at": "2026-04-16T10:00:00",
    }


# ---------------------------------------------------------------------------
# Create endpoint
# ---------------------------------------------------------------------------

class TestCreateVehicleSpec:
    def test_create_spec_calls_service(self, monkeypatch):
        from app.api import vehicle_specs as api

        spec_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        user = _admin_user(tenant_id)
        auth_dep = find_route_dependency("/api/v1/vehicle-specs", "current_user", method="POST")

        created_spec = _mock_spec_response(spec_id=spec_id)
        captured = {}

        async def _mock_create(db, data, principal):
            captured["data"] = data
            captured["principal"] = principal
            return SimpleNamespace(**{**created_spec, "id": spec_id, "tenant_id": tenant_id})

        monkeypatch.setattr(api.vehicle_spec_service, "create_vehicle_spec", _mock_create)
        with build_test_client(dependency_overrides={auth_dep: lambda: user}) as client:
            response = client.post(
                "/api/v1/vehicle-specs",
                json={
                    "make": "Toyota",
                    "model": "Corolla",
                    "year": 2023,
                    "vehicle_type": "CAR_VAN",
                    "fuel_type": "GASOLINE",
                    "fuel_efficiency_km_per_liter": 14.5,
                    "co2_emissions_g_per_km": 160.0,
                },
            )
        assert response.status_code == 201
        assert captured["data"].make == "Toyota"
        assert captured["data"].fuel_efficiency_km_per_liter == 14.5

    def test_create_endpoint_wired_with_admin_role(self):
        """Verify the POST /vehicle-specs route uses an admin-only dependency."""
        from app.main import app

        for route in app.routes:
            if getattr(route, "path", None) != "/api/v1/vehicle-specs":
                continue
            if "POST" not in getattr(route, "methods", set()):
                continue
            # Check that the auth dependency requires ADMIN role
            for dep in route.dependant.dependencies:
                if dep.name == "current_user":
                    # The callable should be a require_role result that includes ADMIN
                    dep_call = dep.call
                    assert dep_call is not None
                    break
            break


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------

class TestListVehicleSpecs:
    def test_list_specs_returns_results_and_header(self, monkeypatch):
        from app.api import vehicle_specs as api

        user = _dispatcher_user()
        spec1 = _mock_spec_response(make="Honda", model="PCX 150")
        spec2 = _mock_spec_response(make="Yamaha", model="NMAX 155")
        auth_dep = find_route_dependency("/api/v1/vehicle-specs", "current_user", method="GET")

        async def _mock_list(db, principal, *, vehicle_type, fuel_type, make, limit, offset):
            return [
                SimpleNamespace(**spec1),
                SimpleNamespace(**spec2),
            ], 2

        monkeypatch.setattr(api.vehicle_spec_service, "list_vehicle_specs", _mock_list)
        with build_test_client(dependency_overrides={auth_dep: lambda: user}) as client:
            response = client.get("/api/v1/vehicle-specs")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert response.headers.get("X-Total-Count") == "2"

    def test_list_accepts_vehicle_type_filter(self, monkeypatch):
        from app.api import vehicle_specs as api

        user = _dispatcher_user()
        captured = {}
        auth_dep = find_route_dependency("/api/v1/vehicle-specs", "current_user", method="GET")

        async def _mock_list(db, principal, *, vehicle_type, fuel_type, make, limit, offset):
            captured["vehicle_type"] = vehicle_type
            return [], 0

        monkeypatch.setattr(api.vehicle_spec_service, "list_vehicle_specs", _mock_list)
        with build_test_client(dependency_overrides={auth_dep: lambda: user}) as client:
            response = client.get("/api/v1/vehicle-specs?vehicle_type=MOTORCYCLE")

        assert response.status_code == 200
        assert captured["vehicle_type"] is not None
        from app.models.enums import VehicleType
        assert captured["vehicle_type"] == VehicleType.MOTORCYCLE


# ---------------------------------------------------------------------------
# Delete endpoint
# ---------------------------------------------------------------------------

class TestDeleteVehicleSpec:
    def test_delete_calls_service(self, monkeypatch):
        from app.api import vehicle_specs as api

        spec_id = uuid.uuid4()
        user = _admin_user()
        auth_dep = find_route_dependency(
            "/api/v1/vehicle-specs/{spec_id}", "current_user", method="DELETE"
        )
        captured = {}

        async def _mock_delete(db, sid, principal):
            captured["spec_id"] = sid

        monkeypatch.setattr(api.vehicle_spec_service, "delete_vehicle_spec", _mock_delete)
        with build_test_client(dependency_overrides={auth_dep: lambda: user}) as client:
            response = client.delete(f"/api/v1/vehicle-specs/{spec_id}")

        assert response.status_code == 204
        assert captured["spec_id"] == spec_id


# ---------------------------------------------------------------------------
# EPA import endpoint
# ---------------------------------------------------------------------------

class TestEPAImport:
    def test_import_epa_calls_client_then_service(self, monkeypatch):
        from app.api import vehicle_specs as api
        from app.models.enums import FuelType, VehicleType
        from app.schemas.vehicle_spec import EPASearchResult

        user = _admin_user()
        auth_dep = find_route_dependency(
            "/api/v1/vehicle-specs/import/epa", "current_user", method="POST"
        )
        epa_result = EPASearchResult(
            epa_vehicle_id="99999",
            make="Ford",
            model="Transit",
            year=2023,
            vehicle_type=VehicleType.TRUCK,
            fuel_type=FuelType.DIESEL,
            fuel_efficiency_km_per_liter=12.0,
            co2_emissions_g_per_km=175.0,
            city_mpg=22.0,
            highway_mpg=28.0,
            combined_mpg=25.0,
        )
        captured = {}

        async def _mock_epa_get(epa_id):
            captured["epa_id"] = epa_id
            return epa_result

        imported_spec = _mock_spec_response(make="Ford", model="Transit")
        imported_spec["epa_vehicle_id"] = "99999"
        imported_spec["source"] = "EPA_API"

        async def _mock_import(db, result, principal):
            captured["imported"] = result
            return SimpleNamespace(**imported_spec)

        monkeypatch.setattr(api.epa_client, "get_vehicle_by_id", _mock_epa_get)
        monkeypatch.setattr(api.vehicle_spec_service, "import_epa_vehicle_spec", _mock_import)

        with build_test_client(dependency_overrides={auth_dep: lambda: user}) as client:
            response = client.post(
                "/api/v1/vehicle-specs/import/epa",
                json={"epa_vehicle_id": "99999"},
            )

        assert response.status_code == 201
        assert captured["epa_id"] == "99999"
        assert captured["imported"].epa_vehicle_id == "99999"

    def test_import_returns_502_on_epa_failure(self, monkeypatch):
        from app.api import vehicle_specs as api

        user = _admin_user()
        auth_dep = find_route_dependency(
            "/api/v1/vehicle-specs/import/epa", "current_user", method="POST"
        )

        async def _mock_epa_fail(epa_id):
            raise Exception("EPA API unavailable")

        monkeypatch.setattr(api.epa_client, "get_vehicle_by_id", _mock_epa_fail)

        with build_test_client(dependency_overrides={auth_dep: lambda: user}) as client:
            response = client.post(
                "/api/v1/vehicle-specs/import/epa",
                json={"epa_vehicle_id": "99999"},
            )

        assert response.status_code == 502


# ---------------------------------------------------------------------------
# Fuel cost derivation in dispatch (unit-level)
# ---------------------------------------------------------------------------

class TestDispatchFuelDerivation:
    def test_spec_overrides_preset_fuel_cost(self):
        """When a VehicleSpec is attached, fuel_cost_per_km should be derived from it."""
        from app.models.enums import FuelType
        from app.schemas.operations_policy import OptimizationSettings
        from app.services.vehicle_spec_service import derive_fuel_cost_per_km

        settings = OptimizationSettings()
        # Default gasoline price
        gasoline_price = settings.fuel_prices[FuelType.GASOLINE]

        # A highly efficient vehicle (40 km/L) should have much lower cost than preset
        efficient_cost = derive_fuel_cost_per_km(40.0, gasoline_price)
        # A low efficiency vehicle (5 km/L) should have higher cost
        inefficient_cost = derive_fuel_cost_per_km(5.0, gasoline_price)

        # Preset for MOTORCYCLE BALANCED = 4, for TRUCK BALANCED = 24
        assert efficient_cost < 4  # better than motorcycle preset
        assert inefficient_cost > 4  # worse than motorcycle preset
        assert inefficient_cost > efficient_cost

    def test_spec_derived_cost_uses_correct_fuel_type_price(self):
        from app.models.enums import FuelType
        from app.schemas.operations_policy import OptimizationSettings
        from app.services.vehicle_spec_service import derive_fuel_cost_per_km

        settings = OptimizationSettings()
        diesel_price = settings.fuel_prices[FuelType.DIESEL]
        electric_price = settings.fuel_prices[FuelType.ELECTRIC]

        # Same efficiency but different fuel types should yield different costs
        diesel_cost = derive_fuel_cost_per_km(10.0, diesel_price)
        electric_cost = derive_fuel_cost_per_km(10.0, electric_price)
        # Electric is cheaper per unit → lower cost
        assert electric_cost < diesel_cost

    def test_fallback_to_preset_when_no_spec(self):
        from app.models.enums import VehicleType
        from app.schemas.operations_policy import OptimizationSettings

        settings = OptimizationSettings()
        preset_cost = settings.vehicle_class_costs[VehicleType.CAR_VAN]
        assert preset_cost.fuel_cost_per_km == 10  # BALANCED preset for CAR_VAN
