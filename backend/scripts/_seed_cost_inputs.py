"""Fill the data gap so cost-savings + counterfactual fuel math
actually produces non-zero numbers:

  * Seed 2 ICE vehicle specs (one diesel, one gasoline) — the existing
    catalog is EV-only, so all routes were costing TRY 0.
  * Assign each of the default tenant's 3 couriers a vehicle_spec
    (round-robin diesel / gasoline / motorcycle-gasoline).
  * Set the depot's province_code to '34' (Istanbul).
  * Seed an EnergyPriceSnapshot for each fuel type at today's date for
    province 34. Prices reflect May 2026 Turkish retail.

Idempotent — re-running upserts each row instead of duplicating.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

import app.models.vehicle_tag  # noqa: F401

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session
from app.models.courier import Courier
from app.models.depot import Depot
from app.models.energy_price import EnergyPriceSnapshot
from app.models.enums import FuelType, VehicleType
from app.models.tenant import Tenant
from app.models.vehicle_spec import VehicleSpec


PROVINCE_CODE = "34"
EFFECTIVE = date(2026, 5, 13)

DIESEL_SPEC = {
    "make": "Ford",
    "model": "Transit",
    "year": 2024,
    "vehicle_type": VehicleType.CAR_VAN,
    "fuel_type": FuelType.DIESEL,
    "fuel_efficiency_km_per_liter": 11.0,
    "co2_emissions_g_per_km": 168.0,
    "length_cm": 530,
    "width_cm": 200,
    "height_cm": 245,
    "gross_weight_kg": 3500,
}
GASOLINE_VAN_SPEC = {
    "make": "Renault",
    "model": "Kangoo",
    "year": 2023,
    "vehicle_type": VehicleType.CAR_VAN,
    "fuel_type": FuelType.GASOLINE,
    "fuel_efficiency_km_per_liter": 14.0,
    "co2_emissions_g_per_km": 142.0,
    "length_cm": 449,
    "width_cm": 187,
    "height_cm": 184,
    "gross_weight_kg": 2050,
}
GASOLINE_MOTO_SPEC = {
    "make": "Honda",
    "model": "PCX 160",
    "year": 2023,
    "vehicle_type": VehicleType.MOTORCYCLE,
    "fuel_type": FuelType.GASOLINE,
    "fuel_efficiency_km_per_liter": 42.0,
    "co2_emissions_g_per_km": 55.0,
    "length_cm": 192,
    "width_cm": 74,
    "height_cm": 110,
    "gross_weight_kg": 320,
}

FUEL_PRICES_TRY_PER_L = {
    FuelType.GASOLINE: 44.20,
    FuelType.DIESEL: 42.50,
}


async def _upsert_spec(db, payload: dict) -> uuid.UUID:
    existing = (
        await db.execute(
            select(VehicleSpec).where(
                VehicleSpec.make == payload["make"],
                VehicleSpec.model == payload["model"],
                VehicleSpec.year == payload["year"],
                VehicleSpec.tenant_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id
    spec = VehicleSpec(tenant_id=None, **payload)
    db.add(spec)
    await db.flush()
    return spec.id


async def main() -> None:
    async with async_session() as db:
        diesel_id = await _upsert_spec(db, DIESEL_SPEC)
        gas_van_id = await _upsert_spec(db, GASOLINE_VAN_SPEC)
        gas_moto_id = await _upsert_spec(db, GASOLINE_MOTO_SPEC)
        print(f"vehicle_specs: diesel={diesel_id} gas-van={gas_van_id} gas-moto={gas_moto_id}")

        tenant = (
            await db.execute(select(Tenant).where(Tenant.slug == "default"))
        ).scalar_one()

        # Set province on every active depot
        await db.execute(
            update(Depot)
            .where(Depot.tenant_id == tenant.id)
            .values(province_code=PROVINCE_CODE, province_name="İstanbul")
        )
        depots_count = await db.scalar(
            select(__import__("sqlalchemy").func.count())
            .select_from(Depot)
            .where(Depot.tenant_id == tenant.id)
        )
        print(f"depots tagged with province '{PROVINCE_CODE}': {depots_count}")

        # Pick a representative depot to home all couriers at — the route-
        # performance-snapshot service uses `courier.depot.province_code`
        # for the fuel-price lookup.
        depot = (
            await db.execute(
                select(Depot).where(Depot.tenant_id == tenant.id).limit(1)
            )
        ).scalar_one_or_none()

        # Assign vehicle_spec + depot round-robin to couriers
        couriers = list(
            (await db.execute(select(Courier).where(Courier.tenant_id == tenant.id)))
            .scalars()
            .all()
        )
        for i, c in enumerate(couriers):
            spec_id = [diesel_id, gas_van_id, gas_moto_id][i % 3]
            c.vehicle_spec_id = spec_id
            if depot is not None and c.depot_id is None:
                c.depot_id = depot.id
            print(
                f"  courier {c.id} → vehicle_spec {spec_id} · depot {c.depot_id}"
            )

        # Seed energy prices
        for fuel_type, price in FUEL_PRICES_TRY_PER_L.items():
            stmt = (
                pg_insert(EnergyPriceSnapshot)
                .values(
                    country="TR",
                    province_code=PROVINCE_CODE,
                    fuel_type=fuel_type,
                    unit="LITER",
                    effective_date=EFFECTIVE,
                    price_try=price,
                    source_name="manual-pilot-seed",
                    source_url="https://example.com",
                )
                .on_conflict_do_update(
                    index_elements=[
                        "country",
                        "province_code",
                        "fuel_type",
                        "effective_date",
                        "source_name",
                    ],
                    set_={"price_try": price},
                )
            )
            await db.execute(stmt)
        print(
            f"energy prices upserted for province {PROVINCE_CODE} ({EFFECTIVE}): "
            + ", ".join(f"{ft.value}=₺{p}" for ft, p in FUEL_PRICES_TRY_PER_L.items())
        )

        await db.commit()
        print("cost inputs seeded")


if __name__ == "__main__":
    asyncio.run(main())
