from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.models.energy_price import EnergyPriceSnapshot
from app.models.enums import FuelType
from app.services.energy_price_service import (
    get_effective_energy_prices,
    parse_epdk_fuel_payload,
    parse_ev_tariff_html,
    resolve_turkey_province,
)
from app.utils.compat import utcnow_naive


def test_parse_epdk_fuel_payload_extracts_city_prices() -> None:
    payload = {
        "data": [
            {"il": "İstanbul", "urunAdi": "Benzin", "fiyat": "43,50"},
            {"il": "Ankara", "urunAdi": "Motorin", "fiyat": "45.25 TL"},
        ]
    }

    snapshots = parse_epdk_fuel_payload(
        payload,
        source_name="EPDK_TEST",
        source_url="https://example.test/epdk",
        effective_date=date(2026, 5, 4),
    )

    assert [(item.province_code, item.fuel_type, item.price_try) for item in snapshots] == [
        ("34", FuelType.GASOLINE, 43.5),
        ("06", FuelType.DIESEL, 45.25),
    ]


def test_parse_ev_tariff_html_returns_attributed_fallback() -> None:
    snapshots = parse_ev_tariff_html(
        "<main>AC 8,99 TL/kWh DC 11.50 TRY/kWh</main>",
        source_name="EV_TEST",
        source_url="https://example.test/ev",
        effective_date=date(2026, 5, 4),
        station_metadata={"station_row_count": 120},
    )

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.fuel_type == FuelType.ELECTRIC
    assert snapshot.unit == "KWH"
    assert snapshot.price_try == pytest.approx(10.245)
    assert snapshot.freshness_status == "FALLBACK"
    assert snapshot.raw_payload["station_metadata"]["station_row_count"] == 120


def test_resolve_turkey_province_prefers_address_match_then_country_fallback() -> None:
    assert resolve_turkey_province(address="Ataşehir, İstanbul").province_code == "34"
    fallback = resolve_turkey_province(address="Unknown")
    assert fallback.country == "TR"
    assert fallback.province_code is None
    assert fallback.confidence == "COUNTRY_FALLBACK"


@pytest.mark.asyncio
async def test_get_effective_energy_prices_marks_stale_and_uses_policy_fallback() -> None:
    old_date = utcnow_naive().date() - timedelta(days=10)
    row = EnergyPriceSnapshot(
        country="TR",
        province_code="34",
        province_name="Istanbul",
        fuel_type=FuelType.DIESEL,
        unit="LITER",
        effective_date=old_date,
        price_try=44.0,
        source_name="EPDK_TEST",
        source_url="https://example.test/epdk",
        freshness_status="FRESH",
    )

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return [row]

    class FakeDb:
        async def execute(self, _query):
            return FakeResult()

    basis = await get_effective_energy_prices(
        FakeDb(),
        province_code="34",
        province_name="Istanbul",
        fallback_prices={FuelType.ELECTRIC: 12.5},
    )

    assert basis.prices[FuelType.DIESEL] == 44.0
    assert basis.prices[FuelType.ELECTRIC] == 12.5
    assert basis.freshness_status == "STALE"
    assert {source["freshness_status"] for source in basis.sources} >= {"STALE", "FALLBACK"}
