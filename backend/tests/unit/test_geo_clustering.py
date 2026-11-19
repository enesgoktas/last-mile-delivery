"""Tests for geographic pre-clustering."""

from app.algorithms.geo_clustering import (
    CLUSTERING_THRESHOLD,
    GeoCluster,
    _adjacent_cells,
    _ensure_vehicle_coverage,
    _haversine_approx,
    cluster_orders,
    should_cluster,
)


class TestShouldCluster:
    def test_below_threshold(self):
        assert should_cluster(100) is False

    def test_at_threshold(self):
        assert should_cluster(CLUSTERING_THRESHOLD) is False

    def test_above_threshold(self):
        assert should_cluster(CLUSTERING_THRESHOLD + 1) is True


class TestClusterOrders:
    def test_empty_orders(self):
        result = cluster_orders([], [])
        assert result == []

    def test_below_threshold_single_cluster(self):
        orders = [(40.0 + i * 0.01, 29.0 + i * 0.01) for i in range(50)]
        vehicles = [(40.0, 29.0), (40.5, 29.5)]
        result = cluster_orders(orders, vehicles)
        assert len(result) == 1
        assert result[0].order_count == 50
        assert result[0].vehicle_count == 2

    def test_above_threshold_multiple_clusters(self):
        # Create 300 orders in two geographic groups
        group_a = [(40.0 + i * 0.001, 29.0 + i * 0.001) for i in range(150)]
        group_b = [(42.0 + i * 0.001, 31.0 + i * 0.001) for i in range(150)]
        orders = group_a + group_b
        vehicles = [(40.0, 29.0), (42.0, 31.0)]
        result = cluster_orders(orders, vehicles)
        assert len(result) >= 2
        # All orders accounted for
        all_order_indices = []
        for c in result:
            all_order_indices.extend(c.order_indices)
        assert sorted(all_order_indices) == list(range(300))

    def test_all_orders_assigned_to_exactly_one_cluster(self):
        orders = [(40.0 + i * 0.002, 29.0 + i * 0.002) for i in range(250)]
        vehicles = [(40.0, 29.0)]
        result = cluster_orders(orders, vehicles)
        all_indices = []
        for c in result:
            all_indices.extend(c.order_indices)
        assert len(all_indices) == 250
        assert len(set(all_indices)) == 250

    def test_every_cluster_has_vehicle(self):
        orders = [(40.0 + i * 0.002, 29.0 + i * 0.002) for i in range(250)]
        vehicles = [(40.0, 29.0), (40.5, 29.5)]
        result = cluster_orders(orders, vehicles)
        for c in result:
            assert c.vehicle_count >= 1

    def test_cluster_ids_are_sequential(self):
        orders = [(40.0 + i * 0.002, 29.0 + i * 0.002) for i in range(250)]
        vehicles = [(40.0, 29.0)]
        result = cluster_orders(orders, vehicles)
        ids = [c.cluster_id for c in result]
        assert ids == list(range(len(result)))

    def test_vehicles_distributed_proportionally_with_shared_depot(self):
        """Regression: when every vehicle starts at the same depot, vehicles
        must be distributed across clusters by demand share — NOT all dumped
        into the cluster nearest the depot. Pure proximity assignment used
        to put one cluster with the entire fleet and leave the rest with
        a single vehicle each, which dropped ~65% of orders on a 600-order
        comparison run because tiny clusters with ~120 orders had only one
        vehicle. See geo_clustering._assign_vehicles."""
        orders: list[tuple[float, float]] = []
        for c in range(5):
            base_lat = 40.95 + c * 0.05
            base_lon = 28.95 + c * 0.05
            orders.extend((base_lat + i * 0.001, base_lon + i * 0.001) for i in range(100))

        # 50 vehicles, all sharing one depot
        vehicles = [(41.0, 29.0)] * 50
        result = cluster_orders(orders, vehicles)

        assert len(result) >= 4
        # Every cluster has ≥1 vehicle
        for cluster in result:
            assert cluster.vehicle_count >= 1, (
                f"cluster {cluster.cluster_id} ({cluster.order_count} orders) has no vehicles"
            )
        # No single cluster holds >50% of the fleet
        max_share = max(c.vehicle_count for c in result)
        assert max_share / 50 < 0.5, (
            f"one cluster holds {max_share}/50 vehicles — "
            "vehicle assignment is not demand-proportional"
        )
        # All vehicles assigned, no leaks
        total_assigned = sum(c.vehicle_count for c in result)
        assert total_assigned == 50


class TestAdjacentCells:
    def test_returns_8_neighbors(self):
        neighbors = _adjacent_cells((5, 10))
        assert len(neighbors) == 8
        assert (5, 10) not in neighbors
        assert (4, 9) in neighbors
        assert (6, 11) in neighbors


class TestHaversineApprox:
    def test_same_point_is_zero(self):
        assert _haversine_approx(40.0, 29.0, 40.0, 29.0) == 0.0

    def test_reasonable_distance(self):
        # ~111 km per degree of latitude
        dist = _haversine_approx(40.0, 29.0, 41.0, 29.0)
        assert 100 < dist < 120


class TestEnsureVehicleCoverage:
    def test_empty_cluster_gets_vehicle(self):
        clusters = [
            GeoCluster(cluster_id=0, order_indices=[0, 1], vehicle_indices=[],
                       center_lat=40.0, center_lon=29.0),
            GeoCluster(cluster_id=1, order_indices=[2, 3], vehicle_indices=[0, 1],
                       center_lat=40.1, center_lon=29.1),
        ]
        vehicles = [(40.1, 29.1), (40.05, 29.05)]
        _ensure_vehicle_coverage(clusters, vehicles)
        assert clusters[0].vehicle_count >= 1
