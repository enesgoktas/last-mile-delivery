from app.models.enums import VehicleType
from app.models.vehicle_spec import VehicleSpec


def resolve_osrm_profile_from_values(
    vehicle_type: VehicleType,
    *,
    spec: VehicleSpec | None = None,
    gross_weight_kg: int | None = None,
) -> str:
    spec_profile = getattr(spec, "osrm_profile", None) if spec is not None else None
    if spec_profile:
        return spec_profile
    effective_weight = gross_weight_kg
    if effective_weight is None and spec is not None:
        effective_weight = getattr(spec, "gross_weight_kg", None)
    if vehicle_type == VehicleType.MOTORCYCLE:
        return "motorcycle"
    if effective_weight is not None and effective_weight >= 3500:
        return "truck"
    if vehicle_type == VehicleType.TRUCK:
        return "truck"
    return "driving"


def resolve_osrm_profile(spec: VehicleSpec) -> str:
    return resolve_osrm_profile_from_values(spec.vehicle_type, spec=spec)
