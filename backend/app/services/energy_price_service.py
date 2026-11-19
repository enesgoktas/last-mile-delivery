from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.energy_price import EnergyPriceSnapshot
from app.models.enums import FuelType
from app.utils.compat import utcnow_naive

logger = logging.getLogger(__name__)

EPDK_PETROL_URL = "https://apigateway.epdk.gov.tr/petrolBayiSatisFiyatBulten"
EPDK_LPG_URL = "https://apigateway.epdk.gov.tr/lpgBayiSatisFiyatBultenGunluk"
EPDK_CHARGING_STATIONS_URL = "https://apigateway.epdk.gov.tr/sarjIstasyonlari"
EV_SARJ_FIYATLARI_URL = "https://www.evsarjfiyatlari.com/"
ROTAVOLT_URL = "https://rotavolt.net/how-it-works"

TURKEY_FUEL_TYPES = (
    FuelType.GASOLINE,
    FuelType.DIESEL,
    FuelType.LPG,
    FuelType.ELECTRIC,
)

TURKEY_PROVINCES: dict[str, str] = {
    "01": "Adana",
    "02": "Adiyaman",
    "03": "Afyonkarahisar",
    "04": "Agri",
    "05": "Amasya",
    "06": "Ankara",
    "07": "Antalya",
    "08": "Artvin",
    "09": "Aydin",
    "10": "Balikesir",
    "11": "Bilecik",
    "12": "Bingol",
    "13": "Bitlis",
    "14": "Bolu",
    "15": "Burdur",
    "16": "Bursa",
    "17": "Canakkale",
    "18": "Cankiri",
    "19": "Corum",
    "20": "Denizli",
    "21": "Diyarbakir",
    "22": "Edirne",
    "23": "Elazig",
    "24": "Erzincan",
    "25": "Erzurum",
    "26": "Eskisehir",
    "27": "Gaziantep",
    "28": "Giresun",
    "29": "Gumushane",
    "30": "Hakkari",
    "31": "Hatay",
    "32": "Isparta",
    "33": "Mersin",
    "34": "Istanbul",
    "35": "Izmir",
    "36": "Kars",
    "37": "Kastamonu",
    "38": "Kayseri",
    "39": "Kirklareli",
    "40": "Kirsehir",
    "41": "Kocaeli",
    "42": "Konya",
    "43": "Kutahya",
    "44": "Malatya",
    "45": "Manisa",
    "46": "Kahramanmaras",
    "47": "Mardin",
    "48": "Mugla",
    "49": "Mus",
    "50": "Nevsehir",
    "51": "Nigde",
    "52": "Ordu",
    "53": "Rize",
    "54": "Sakarya",
    "55": "Samsun",
    "56": "Siirt",
    "57": "Sinop",
    "58": "Sivas",
    "59": "Tekirdag",
    "60": "Tokat",
    "61": "Trabzon",
    "62": "Tunceli",
    "63": "Sanliurfa",
    "64": "Usak",
    "65": "Van",
    "66": "Yozgat",
    "67": "Zonguldak",
    "68": "Aksaray",
    "69": "Bayburt",
    "70": "Karaman",
    "71": "Kirikkale",
    "72": "Batman",
    "73": "Sirnak",
    "74": "Bartin",
    "75": "Ardahan",
    "76": "Igdir",
    "77": "Yalova",
    "78": "Karabuk",
    "79": "Kilis",
    "80": "Osmaniye",
    "81": "Duzce",
}

TURKEY_PROVINCE_ALIASES: dict[str, str] = {
    "afyon": "Afyonkarahisar",
    "icel": "Mersin",
    "istanbul": "Istanbul",
    "izmir": "Izmir",
    "maras": "Kahramanmaras",
    "urfa": "Sanliurfa",
}

TURKEY_PROVINCE_CENTROIDS: dict[str, tuple[float, float]] = {
    "Ankara": (39.9334, 32.8597),
    "Antalya": (36.8969, 30.7133),
    "Bursa": (40.1950, 29.0600),
    "Gaziantep": (37.0662, 37.3833),
    "Istanbul": (41.0082, 28.9784),
    "Izmir": (38.4237, 27.1428),
    "Kocaeli": (40.8533, 29.8815),
    "Konya": (37.8714, 32.4846),
    "Mersin": (36.8121, 34.6415),
}

_PROVINCE_NAME_TO_CODE = {name: code for code, name in TURKEY_PROVINCES.items()}


@dataclass(frozen=True)
class ProvinceResolution:
    country: str = "TR"
    province_code: str | None = None
    province_name: str | None = None
    confidence: str = "COUNTRY_FALLBACK"
    source: str = "fallback"


@dataclass(frozen=True)
class EnergyPriceBasis:
    prices: dict[FuelType, float]
    sources: list[dict[str, Any]]
    freshness_status: str
    province_code: str | None = None
    province_name: str | None = None


def normalize_turkish_text(value: str | None) -> str:
    if not value:
        return ""
    translated = value.casefold().translate(
        str.maketrans({
            "ç": "c",
            "ğ": "g",
            "ı": "i",
            "ö": "o",
            "ş": "s",
            "ü": "u",
        })
    )
    normalized = unicodedata.normalize("NFKD", translated)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


_NORMALIZED_PROVINCES = {
    normalize_turkish_text(name): (code, name)
    for code, name in TURKEY_PROVINCES.items()
}


def resolve_turkey_province(
    *,
    address: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> ProvinceResolution:
    normalized_address = normalize_turkish_text(address)
    if normalized_address:
        for alias, canonical in TURKEY_PROVINCE_ALIASES.items():
            if re.search(rf"\b{re.escape(alias)}\b", normalized_address):
                return _province_resolution(canonical, confidence="ADDRESS_ALIAS", source="address")
        for normalized, (_code, province_name) in _NORMALIZED_PROVINCES.items():
            if re.search(rf"\b{re.escape(normalized)}\b", normalized_address):
                return _province_resolution(province_name, confidence="ADDRESS_MATCH", source="address")

    if lat is not None and lon is not None and TURKEY_PROVINCE_CENTROIDS:
        nearest_name = min(
            TURKEY_PROVINCE_CENTROIDS,
            key=lambda name: (TURKEY_PROVINCE_CENTROIDS[name][0] - lat) ** 2
            + (TURKEY_PROVINCE_CENTROIDS[name][1] - lon) ** 2,
        )
        return _province_resolution(nearest_name, confidence="COORDINATE_NEAREST", source="coordinates")

    return ProvinceResolution()


def _province_resolution(name: str, *, confidence: str, source: str) -> ProvinceResolution:
    canonical = TURKEY_PROVINCE_ALIASES.get(normalize_turkish_text(name), name)
    return ProvinceResolution(
        province_code=_PROVINCE_NAME_TO_CODE.get(canonical),
        province_name=canonical,
        confidence=confidence,
        source=source,
    )


def _parse_try_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    text = str(value).strip()
    match = re.search(r"(\d{1,4}(?:[.,]\d{1,4})?)", text)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def _iter_dict_rows(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload:
            rows.extend(_iter_dict_rows(item))
    elif isinstance(payload, dict):
        if any(_is_price_key(key) for key in payload):
            rows.append(payload)
        for value in payload.values():
            if isinstance(value, (list, dict)):
                rows.extend(_iter_dict_rows(value))
    return rows


def _is_price_key(key: Any) -> bool:
    normalized = normalize_turkish_text(str(key))
    return "fiyat" in normalized or "price" in normalized or normalized in {"tutar", "tl"}


def _row_value(row: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    normalized_candidates = {normalize_turkish_text(candidate) for candidate in candidates}
    for key, value in row.items():
        normalized_key = normalize_turkish_text(str(key))
        if normalized_key in normalized_candidates:
            return value
    return None


def _row_text(row: dict[str, Any]) -> str:
    return " ".join(str(value) for value in row.values() if value is not None)


def _resolve_fossil_fuel_type(row: dict[str, Any], *, default: FuelType | None = None) -> FuelType | None:
    product_text = normalize_turkish_text(
        str(
            _row_value(
                row,
                (
                    "urun",
                    "urun_adi",
                    "urunAdi",
                    "yakit",
                    "yakit_adi",
                    "yakitAdi",
                    "akaryakitTipi",
                    "urunTipi",
                ),
            )
            or _row_text(row)
        )
    )
    if "lpg" in product_text or "otogaz" in product_text:
        return FuelType.LPG
    if "motorin" in product_text or "diesel" in product_text or "dizel" in product_text:
        return FuelType.DIESEL
    if "benzin" in product_text or "gasoline" in product_text:
        return FuelType.GASOLINE
    return default


def parse_epdk_fuel_payload(
    payload: Any,
    *,
    source_name: str,
    source_url: str,
    effective_date: date,
    default_fuel_type: FuelType | None = None,
) -> list[EnergyPriceSnapshot]:
    snapshots: list[EnergyPriceSnapshot] = []
    for row in _iter_dict_rows(payload):
        fuel_type = _resolve_fossil_fuel_type(row, default=default_fuel_type)
        if fuel_type not in {FuelType.GASOLINE, FuelType.DIESEL, FuelType.LPG}:
            continue
        price = _parse_try_number(
            _row_value(
                row,
                (
                    "fiyat",
                    "bayiFiyati",
                    "tavanFiyat",
                    "pompaFiyati",
                    "price",
                    "tl",
                    "tutar",
                ),
            )
        )
        if price is None or price <= 0:
            continue
        province_value = _row_value(row, ("il", "ilAdi", "il_adi", "sehir", "city", "province"))
        province = resolve_turkey_province(address=str(province_value or ""))
        snapshots.append(
            EnergyPriceSnapshot(
                country="TR",
                province_code=province.province_code,
                province_name=province.province_name,
                fuel_type=fuel_type,
                unit="LITER",
                effective_date=effective_date,
                price_try=round(price, 3),
                source_name=source_name,
                source_url=source_url,
                freshness_status="FRESH",
                raw_payload={"row": row},
            )
        )
    return snapshots


def parse_ev_tariff_html(
    html: str,
    *,
    source_name: str,
    source_url: str,
    effective_date: date,
    station_metadata: dict[str, Any] | None = None,
) -> list[EnergyPriceSnapshot]:
    prices = [
        float(match.group(1).replace(",", "."))
        for match in re.finditer(r"(\d{1,3}(?:[,.]\d{1,3})?)\s*(?:TL|TRY|₺)\s*/?\s*kWh", html, re.I)
    ]
    prices = [price for price in prices if 0 < price < 100]
    if not prices:
        return []
    avg_price = sum(prices) / len(prices)
    return [
        EnergyPriceSnapshot(
            country="TR",
            province_code=None,
            province_name=None,
            fuel_type=FuelType.ELECTRIC,
            unit="KWH",
            effective_date=effective_date,
            price_try=round(avg_price, 3),
            source_name=source_name,
            source_url=source_url,
            freshness_status="FALLBACK",
            raw_payload={
                "observed_prices": prices[:200],
                "sample_count": len(prices),
                "station_metadata": station_metadata or {},
            },
        )
    ]


async def refresh_turkey_energy_prices(
    db: AsyncSession,
    *,
    effective_date: date | None = None,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    target_date = effective_date or utcnow_naive().date()
    snapshots: list[EnergyPriceSnapshot] = []
    errors: list[str] = []
    station_metadata: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        for source_name, url, default_fuel in (
            ("EPDK_PETROL_BAYI_SATIS", EPDK_PETROL_URL, None),
            ("EPDK_LPG_BAYI_SATIS", EPDK_LPG_URL, FuelType.LPG),
        ):
            try:
                payload = await _fetch_epdk_json(client, url, target_date)
                snapshots.extend(
                    parse_epdk_fuel_payload(
                        payload,
                        source_name=source_name,
                        source_url=url,
                        effective_date=target_date,
                        default_fuel_type=default_fuel,
                    )
                )
            except Exception as exc:
                logger.warning("Energy price refresh failed for %s", source_name, exc_info=True)
                errors.append(f"{source_name}: {exc}")

        try:
            station_payload = await _fetch_epdk_json(client, EPDK_CHARGING_STATIONS_URL, target_date=None)
            station_rows = _iter_dict_rows(station_payload)
            station_metadata = {
                "source": EPDK_CHARGING_STATIONS_URL,
                "station_row_count": len(station_rows),
            }
        except Exception as exc:
            logger.warning("EPDK charging station fetch failed", exc_info=True)
            errors.append(f"EPDK_SARJ_TR: {exc}")

        for source_name, url in (
            ("EV_SARJ_FIYATLARI", EV_SARJ_FIYATLARI_URL),
            ("ROTAVOLT", ROTAVOLT_URL),
        ):
            try:
                html = (await client.get(url)).text
                parsed = parse_ev_tariff_html(
                    html,
                    source_name=source_name,
                    source_url=url,
                    effective_date=target_date,
                    station_metadata=station_metadata,
                )
                snapshots.extend(parsed)
                if parsed:
                    break
            except Exception as exc:
                logger.warning("EV tariff fallback fetch failed for %s", source_name, exc_info=True)
                errors.append(f"{source_name}: {exc}")

    stored = 0
    for snapshot in snapshots:
        await _upsert_snapshot(db, snapshot)
        stored += 1
    await db.flush()

    return {
        "country": "TR",
        "effective_date": target_date.isoformat(),
        "snapshots_seen": len(snapshots),
        "snapshots_stored": stored,
        "errors": errors,
    }


async def _fetch_epdk_json(
    client: httpx.AsyncClient,
    url: str,
    target_date: date | None,
) -> Any:
    kwargs: dict[str, Any] = {}
    if target_date is not None:
        kwargs["json"] = {"raporTarihi": target_date.strftime("%d.%m.%Y")}
    response = await client.request("GET", url, **kwargs)
    response.raise_for_status()
    return response.json()


async def _upsert_snapshot(db: AsyncSession, snapshot: EnergyPriceSnapshot) -> EnergyPriceSnapshot:
    result = await db.execute(
        select(EnergyPriceSnapshot).where(
            EnergyPriceSnapshot.country == snapshot.country,
            EnergyPriceSnapshot.province_code.is_(None)
            if snapshot.province_code is None
            else EnergyPriceSnapshot.province_code == snapshot.province_code,
            EnergyPriceSnapshot.fuel_type == snapshot.fuel_type,
            EnergyPriceSnapshot.effective_date == snapshot.effective_date,
            EnergyPriceSnapshot.source_name == snapshot.source_name,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is None:
        db.add(snapshot)
        return snapshot
    existing.province_name = snapshot.province_name
    existing.unit = snapshot.unit
    existing.price_try = snapshot.price_try
    existing.source_url = snapshot.source_url
    existing.freshness_status = snapshot.freshness_status
    existing.raw_payload = snapshot.raw_payload
    return existing


async def get_effective_energy_prices(
    db: AsyncSession,
    *,
    province_code: str | None = None,
    province_name: str | None = None,
    as_of: date | None = None,
    fallback_prices: dict[FuelType, float] | None = None,
    stale_after_days: int = 3,
) -> EnergyPriceBasis:
    target_date = as_of or utcnow_naive().date()
    fallback_prices = fallback_prices or {}
    result = await db.execute(
        select(EnergyPriceSnapshot)
        .where(
            EnergyPriceSnapshot.country == "TR",
            EnergyPriceSnapshot.effective_date <= target_date,
            or_(
                EnergyPriceSnapshot.province_code == province_code,
                EnergyPriceSnapshot.province_name == province_name,
                EnergyPriceSnapshot.province_code.is_(None),
            ),
        )
        .order_by(
            EnergyPriceSnapshot.fuel_type,
            EnergyPriceSnapshot.province_code.is_(None),
            EnergyPriceSnapshot.effective_date.desc(),
            EnergyPriceSnapshot.created_at.desc(),
        )
    )
    rows = list(result.scalars().all())
    prices: dict[FuelType, float] = {}
    sources: list[dict[str, Any]] = []
    seen: set[FuelType] = set()
    for row in rows:
        if row.fuel_type in seen:
            continue
        seen.add(row.fuel_type)
        stale = row.effective_date < target_date - timedelta(days=stale_after_days)
        prices[row.fuel_type] = row.price_try
        sources.append(
            {
                "fuel_type": row.fuel_type.value,
                "unit": row.unit,
                "price_try": row.price_try,
                "effective_date": row.effective_date.isoformat(),
                "source_name": row.source_name,
                "source_url": row.source_url,
                "freshness_status": "STALE" if stale else row.freshness_status,
                "province_code": row.province_code,
                "province_name": row.province_name,
            }
        )

    for fuel_type in TURKEY_FUEL_TYPES:
        if fuel_type in prices:
            continue
        fallback = fallback_prices.get(fuel_type)
        if fallback is None and fuel_type == FuelType.HYBRID:
            fallback = fallback_prices.get(FuelType.GASOLINE)
        if fallback is None:
            continue
        prices[fuel_type] = fallback
        sources.append(
            {
                "fuel_type": fuel_type.value,
                "unit": "KWH" if fuel_type == FuelType.ELECTRIC else "LITER",
                "price_try": fallback,
                "effective_date": target_date.isoformat(),
                "source_name": "OPERATIONS_POLICY_FALLBACK",
                "source_url": "",
                "freshness_status": "FALLBACK",
                "province_code": province_code,
                "province_name": province_name,
            }
        )

    freshness = "FRESH"
    statuses = {str(source["freshness_status"]) for source in sources}
    if "STALE" in statuses:
        freshness = "STALE"
    elif "FALLBACK" in statuses:
        freshness = "FALLBACK"

    return EnergyPriceBasis(
        prices=prices,
        sources=sources,
        freshness_status=freshness,
        province_code=province_code,
        province_name=province_name,
    )
