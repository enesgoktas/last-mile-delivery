#!/usr/bin/env python3
"""Schema inspection checks for indexes, constraints, and FK delete rules.

Usage:
    python scripts/schema_inspection_checks.py
"""

from __future__ import annotations

import asyncio
import os
from typing import Iterable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine


REQUIRED_INDEXES = {
    "ix_orders_status_type_priority_created_at",
    "ix_route_stops_route_status_sequence",
    "ix_order_status_history_order_created_at",
    "ix_audit_logs_actor_action_created_at",
    "ix_import_jobs_created_by_user_id",
    "ix_import_jobs_status",
    "ix_notifications_dedupe_key",
    "ix_webhook_delivery_log_delivery_status",
    "ix_optimization_runs_task_id",
    "ix_optimization_runs_status",
    "ix_optimization_runs_started_at",
    "ix_api_clients_api_key_prefix",
    "uq_orders_external_ref_not_null",
    "uq_proof_of_delivery_order_id",
    "uq_proof_of_delivery_route_stop_id",
    "uq_webhook_endpoints_client_url",
    "ix_orders_assigned_courier_status_created_at",
    "ix_routes_courier_status_created_at",
    "ix_route_stops_order_id",
    "ix_webhook_delivery_log_endpoint_sent_at",
    "ix_notifications_order_event_created_at",
    "ix_import_jobs_created_at",
    "ix_location_snapshots_courier_recorded_desc",
}

REQUIRED_CHECK_CONSTRAINTS = {
    "ck_orders_attempt_count_non_negative",
    "ck_orders_max_attempts_positive",
    "ck_orders_attempt_count_lte_max_attempts",
    "ck_orders_delivery_window_valid",
    "ck_orders_pickup_window_valid",
}

REQUIRED_FK_DELETE_RULES = {
    "fk_orders_assigned_courier_id": "SET NULL",
    "fk_orders_route_id": "SET NULL",
    "fk_route_stops_route_id": "CASCADE",
    "fk_location_snapshots_courier_id": "CASCADE",
    "fk_order_status_history_order_id": "CASCADE",
    "fk_order_status_history_actor_user_id": "SET NULL",
    "fk_proof_of_delivery_order_id": "CASCADE",
    "fk_proof_of_delivery_route_stop_id": "CASCADE",
    "fk_webhook_endpoints_client_id": "CASCADE",
    "fk_webhook_delivery_log_endpoint_id": "CASCADE",
    "fk_notifications_order_id": "SET NULL",
}

FK_DELETE_MAP = {
    "a": "NO ACTION",
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}


def _normalize_confdeltype(value: object) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return str(value)


def _missing(expected: Iterable[str], actual: Iterable[str]) -> list[str]:
    return sorted(set(expected).difference(set(actual)))


async def _run_checks(database_url: str) -> tuple[set[str], set[str], dict[str, str]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            index_rows = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT indexname
                        FROM pg_indexes
                        WHERE schemaname = current_schema()
                        """
                    )
                )
            ).all()
            existing_indexes = {row[0] for row in index_rows}

            check_rows = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT conname
                        FROM pg_constraint
                        WHERE contype = 'c'
                        """
                    )
                )
            ).all()
            existing_checks = {row[0] for row in check_rows}

            fk_rows = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT conname, confdeltype
                        FROM pg_constraint
                        WHERE contype = 'f'
                        """
                    )
                )
            ).all()
            fk_rules = {
                name: FK_DELETE_MAP.get(
                    _normalize_confdeltype(confdeltype),
                    f"UNKNOWN({_normalize_confdeltype(confdeltype)})",
                )
                for name, confdeltype in fk_rows
            }
            return existing_indexes, existing_checks, fk_rules
    finally:
        await engine.dispose()


async def main() -> int:
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://delivery:delivery@localhost:5432/delivery_db",
    )
    existing_indexes, existing_checks, fk_rules = await _run_checks(database_url)

    failures: list[str] = []

    for idx in _missing(REQUIRED_INDEXES, existing_indexes):
        failures.append(f"missing index: {idx}")

    for chk in _missing(REQUIRED_CHECK_CONSTRAINTS, existing_checks):
        failures.append(f"missing check constraint: {chk}")

    for fk_name, expected_rule in REQUIRED_FK_DELETE_RULES.items():
        actual_rule = fk_rules.get(fk_name)
        if actual_rule is None:
            failures.append(f"missing fk constraint: {fk_name}")
        elif actual_rule != expected_rule:
            failures.append(
                f"fk delete rule mismatch: {fk_name} expected={expected_rule} actual={actual_rule}"
            )

    if failures:
        print("Schema inspection failed:")
        for item in failures:
            print(f" - {item}")
        return 1

    print("Schema inspection checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
