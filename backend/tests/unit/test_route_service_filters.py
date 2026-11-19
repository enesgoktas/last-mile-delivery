from __future__ import annotations

from datetime import date

from app.services import route_service


def test_route_date_filter_matches_stop_operational_day():
    criterion = route_service._route_has_stop_on_date(date(2026, 4, 29))

    sql = str(criterion.compile(compile_kwargs={"literal_binds": True}))

    assert "route_stops.route_id = routes.id" in sql
    assert "route_stops.planned_arrival >= '2026-04-29 00:00:00'" in sql
    assert "route_stops.planned_arrival < '2026-04-30 00:00:00'" in sql
