from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.models.enums import AvailabilityStatus, OrderType, UserRole, VehicleType
from app.utils.compat import utcnow_naive


def signup_owner(client, label: str) -> dict:
    response = client.post(
        "/api/v1/auth/signup",
        json={
            "email": f"{label}-{uuid.uuid4().hex[:10]}@example.com",
            "password": "Password1!",
            "company_name": f"{label.title()} {uuid.uuid4().hex[:8]}",
            "full_name": f"{label.title()} Owner",
        },
    )
    assert response.status_code == 201
    return response.json()


def auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def register_user(
    client,
    *,
    access_token: str,
    email: str,
    password: str,
    role: UserRole,
) -> dict:
    response = client.post(
        "/api/v1/auth/register",
        headers=auth_headers(access_token),
        json={
            "email": email,
            "password": password,
            "role": role.value,
        },
    )
    assert response.status_code == 201
    return response.json()


def login(client, *, email: str, password: str) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return response.json()


def create_courier_profile(client, *, access_token: str, user_id: str) -> dict:
    response = client.post(
        "/api/v1/couriers",
        headers=auth_headers(access_token),
        json={
            "user_id": user_id,
            "vehicle_type": VehicleType.CAR_VAN.value,
            "max_weight_kg": 100.0,
            "depot_lat": 41.0082,
            "depot_lon": 28.9784,
            "working_hours_start": "08:00:00",
            "working_hours_end": "20:00:00",
        },
    )
    assert response.status_code == 201
    return response.json()


def set_courier_status(
    client,
    *,
    access_token: str,
    courier_id: str,
    status: AvailabilityStatus,
) -> dict:
    response = client.put(
        f"/api/v1/couriers/{courier_id}/status",
        headers=auth_headers(access_token),
        json={"status": status.value},
    )
    assert response.status_code == 200
    return response.json()


def build_order_payload(
    *,
    external_ref: str,
    order_type: OrderType = OrderType.SCHEDULED,
    notes: str | None = None,
    earliest_pickup: datetime | None = None,
    latest_pickup: datetime | None = None,
    earliest_delivery: datetime | None = None,
    latest_delivery: datetime | None = None,
) -> dict:
    resolved_earliest_delivery = earliest_delivery or (utcnow_naive() + timedelta(hours=2))
    resolved_latest_delivery = latest_delivery or (resolved_earliest_delivery + timedelta(hours=2))
    return {
        "type": order_type.value,
        "priority": "HIGH",
        "pickup_address": "Warehouse A",
        "pickup_lat": 41.015,
        "pickup_lon": 28.979,
        "delivery_address": f"Customer Address {external_ref}",
        "delivery_lat": 41.032,
        "delivery_lon": 28.991,
        "weight_kg": 4.2,
        "is_fragile": False,
        "is_temp_sensitive": False,
        "earliest_pickup": earliest_pickup.isoformat() if earliest_pickup else None,
        "latest_pickup": latest_pickup.isoformat() if latest_pickup else None,
        "earliest_delivery": resolved_earliest_delivery.isoformat(),
        "latest_delivery": resolved_latest_delivery.isoformat(),
        "customer_email": f"customer-{external_ref}@example.com",
        "customer_phone": "+15550001111",
        "external_ref": external_ref,
        "notes": notes,
    }
