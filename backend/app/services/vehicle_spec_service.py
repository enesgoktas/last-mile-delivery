import uuid
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import FuelType, VehicleType
from app.models.vehicle_spec import VehicleSpec
from app.schemas.vehicle_spec import EPASearchResult, VehicleSpecCreate, VehicleSpecUpdate
from app.utils.compat import utcnow_naive
from app.utils.tenant_context import get_current_tenant_id


@dataclass(frozen=True)
class VehicleTypeFuelProfile:
    fuel_type: FuelType
    fuel_efficiency_km_per_liter: float


DEFAULT_VEHICLE_TYPE_FUEL_PROFILES: dict[VehicleType, VehicleTypeFuelProfile] = {
    VehicleType.MOTORCYCLE: VehicleTypeFuelProfile(
        fuel_type=FuelType.GASOLINE,
        fuel_efficiency_km_per_liter=36.0,
    ),
    VehicleType.CAR_VAN: VehicleTypeFuelProfile(
        fuel_type=FuelType.DIESEL,
        fuel_efficiency_km_per_liter=16.0,
    ),
    VehicleType.TRUCK: VehicleTypeFuelProfile(
        fuel_type=FuelType.DIESEL,
        fuel_efficiency_km_per_liter=5.0,
    ),
}


def get_default_vehicle_type_fuel_profile(
    vehicle_type: VehicleType | str,
) -> VehicleTypeFuelProfile | None:
    try:
        normalized_vehicle_type = VehicleType(vehicle_type)
    except ValueError:
        return None
    return DEFAULT_VEHICLE_TYPE_FUEL_PROFILES.get(normalized_vehicle_type)


def derive_default_vehicle_type_fuel_cost_per_km(
    vehicle_type: VehicleType | str,
    fuel_prices: dict[FuelType, float],
) -> int | None:
    profile = get_default_vehicle_type_fuel_profile(vehicle_type)
    if profile is None:
        return None
    fuel_price = fuel_prices.get(profile.fuel_type, 35.0)
    return derive_fuel_cost_per_km(profile.fuel_efficiency_km_per_liter, fuel_price)


async def create_vehicle_spec(
    db: AsyncSession,
    data: VehicleSpecCreate,
    principal,
) -> VehicleSpec:
    tenant_id = get_current_tenant_id(principal)
    spec = VehicleSpec(
        tenant_id=tenant_id,
        make=data.make,
        model=data.model,
        year=data.year,
        vehicle_type=data.vehicle_type,
        fuel_type=data.fuel_type,
        fuel_efficiency_km_per_liter=data.fuel_efficiency_km_per_liter,
        co2_emissions_g_per_km=data.co2_emissions_g_per_km,
        height_cm=data.height_cm,
        width_cm=data.width_cm,
        length_cm=data.length_cm,
        gross_weight_kg=data.gross_weight_kg,
        axle_count=data.axle_count,
        emission_class=data.emission_class,
        hazmat_capable=data.hazmat_capable,
        osrm_profile=data.osrm_profile,
        supported_connector_types=data.supported_connector_types or [],
        battery_kwh=data.battery_kwh,
        source="MANUAL",
    )
    db.add(spec)
    await db.flush()
    await db.refresh(spec)
    return spec


async def list_vehicle_specs(
    db: AsyncSession,
    principal,
    *,
    vehicle_type: VehicleType | None = None,
    fuel_type: FuelType | None = None,
    make: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[VehicleSpec], int]:
    tenant_id = get_current_tenant_id(principal)

    query = select(VehicleSpec).where(
        or_(VehicleSpec.tenant_id == tenant_id, VehicleSpec.tenant_id.is_(None))
    )
    count_query = select(VehicleSpec).where(
        or_(VehicleSpec.tenant_id == tenant_id, VehicleSpec.tenant_id.is_(None))
    )

    if vehicle_type is not None:
        query = query.where(VehicleSpec.vehicle_type == vehicle_type)
        count_query = count_query.where(VehicleSpec.vehicle_type == vehicle_type)
    if fuel_type is not None:
        query = query.where(VehicleSpec.fuel_type == fuel_type)
        count_query = count_query.where(VehicleSpec.fuel_type == fuel_type)
    if make is not None:
        query = query.where(VehicleSpec.make.ilike(f"%{make}%"))
        count_query = count_query.where(VehicleSpec.make.ilike(f"%{make}%"))

    total_result = await db.execute(count_query)
    total = len(total_result.scalars().all())

    query = query.order_by(VehicleSpec.make, VehicleSpec.model, VehicleSpec.year).offset(offset).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all()), total


async def get_vehicle_spec(
    db: AsyncSession,
    spec_id: uuid.UUID,
    principal,
) -> VehicleSpec:
    tenant_id = get_current_tenant_id(principal)
    result = await db.execute(
        select(VehicleSpec).where(
            VehicleSpec.id == spec_id,
            or_(VehicleSpec.tenant_id == tenant_id, VehicleSpec.tenant_id.is_(None)),
        )
    )
    spec = result.scalar_one_or_none()
    if spec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle spec not found")
    return spec


async def update_vehicle_spec(
    db: AsyncSession,
    spec_id: uuid.UUID,
    data: VehicleSpecUpdate,
    principal,
) -> VehicleSpec:
    spec = await get_vehicle_spec(db, spec_id, principal)

    if data.make is not None:
        spec.make = data.make
    if data.model is not None:
        spec.model = data.model
    if data.year is not None:
        spec.year = data.year
    if data.vehicle_type is not None:
        spec.vehicle_type = data.vehicle_type
    if data.fuel_type is not None:
        spec.fuel_type = data.fuel_type
    if data.fuel_efficiency_km_per_liter is not None:
        spec.fuel_efficiency_km_per_liter = data.fuel_efficiency_km_per_liter
    if data.co2_emissions_g_per_km is not None:
        spec.co2_emissions_g_per_km = data.co2_emissions_g_per_km
    if data.height_cm is not None:
        spec.height_cm = data.height_cm
    if data.width_cm is not None:
        spec.width_cm = data.width_cm
    if data.length_cm is not None:
        spec.length_cm = data.length_cm
    if data.gross_weight_kg is not None:
        spec.gross_weight_kg = data.gross_weight_kg
    if data.axle_count is not None:
        spec.axle_count = data.axle_count
    if data.emission_class is not None:
        spec.emission_class = data.emission_class
    if data.hazmat_capable is not None:
        spec.hazmat_capable = data.hazmat_capable
    if data.osrm_profile is not None:
        spec.osrm_profile = data.osrm_profile

    spec.updated_at = utcnow_naive()
    await db.flush()
    await db.refresh(spec)
    return spec


async def delete_vehicle_spec(
    db: AsyncSession,
    spec_id: uuid.UUID,
    principal,
) -> None:
    spec = await get_vehicle_spec(db, spec_id, principal)
    await db.delete(spec)
    await db.flush()


async def import_epa_vehicle_spec(
    db: AsyncSession,
    epa_result: EPASearchResult,
    principal,
) -> VehicleSpec:
    """Import an EPA search result into the tenant's catalog.

    If a spec with the same epa_vehicle_id already exists (globally or in
    the tenant), return it rather than creating a duplicate.
    """
    existing_result = await db.execute(
        select(VehicleSpec).where(VehicleSpec.epa_vehicle_id == epa_result.epa_vehicle_id)
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        return existing

    tenant_id = get_current_tenant_id(principal)
    spec = VehicleSpec(
        tenant_id=tenant_id,
        make=epa_result.make,
        model=epa_result.model,
        year=epa_result.year,
        vehicle_type=epa_result.vehicle_type,
        fuel_type=epa_result.fuel_type,
        fuel_efficiency_km_per_liter=epa_result.fuel_efficiency_km_per_liter,
        co2_emissions_g_per_km=epa_result.co2_emissions_g_per_km,
        epa_vehicle_id=epa_result.epa_vehicle_id,
        source="EPA_API",
    )
    db.add(spec)
    await db.flush()
    await db.refresh(spec)
    return spec


def derive_fuel_cost_per_km(
    fuel_efficiency_km_per_liter: float,
    fuel_price_per_liter: float,
) -> int:
    """Compute a fuel_cost_per_km integer in the same cost-unit scale as
    the optimization preset constants.

    Args:
        fuel_efficiency_km_per_liter: km traveled per liter (or kWh equivalent).
        fuel_price_per_liter: Cost per liter in the tenant's cost-unit scale
            (same integer scale used by VehicleClassEconomicCosts).

    Returns:
        fuel_cost_per_km as a rounded integer.
    """
    if fuel_efficiency_km_per_liter <= 0:
        return 0
    return round(fuel_price_per_liter / fuel_efficiency_km_per_liter)
