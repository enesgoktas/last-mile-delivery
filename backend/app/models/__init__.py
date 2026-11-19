# Re-export all models so Alembic can auto-detect them
from app.database import Base  # noqa: F401
from app.models.audit import AuditLog  # noqa: F401
from app.models.courier import Courier  # noqa: F401
from app.models.courier_depot import courier_depots  # noqa: F401
from app.models.courier_history import CourierAvailabilityHistory  # noqa: F401
from app.models.daily_tenant_kpi import DailyTenantKPI  # noqa: F401
from app.models.depot import Depot  # noqa: F401
from app.models.demand_observation import DemandObservation  # noqa: F401
from app.models.dispatch_decision_log import DispatchDecisionLog  # noqa: F401
from app.models.driver_event import DriverEvent, DriverEventType  # noqa: F401
from app.models.empirical_service_time import EmpiricalServiceTime  # noqa: F401
from app.models.eta_prediction_log import (  # noqa: F401
    ETAPredictedBy,
    ETAPredictionLog,
)
from app.models.depot_edge import DepotEdge  # noqa: F401
from app.models.energy_price import EnergyPriceSnapshot  # noqa: F401
from app.models.import_job import ImportJob  # noqa: F401
from app.models.inventory_event import InventoryEvent  # noqa: F401
from app.models.location import LocationSnapshot  # noqa: F401
from app.models.notification import Notification, ProofOfDelivery  # noqa: F401
from app.models.operations_policy_snapshot import (  # noqa: F401
    OperationsPolicySnapshot,
)
from app.models.optimization_counterfactual import (  # noqa: F401
    CounterfactualBaselineKind,
    OptimizationCounterfactual,
)
from app.models.optimization_run import OptimizationRun  # noqa: F401
from app.models.optimization_run_replay import OptimizationRunReplay  # noqa: F401
from app.models.order_rating import OrderRating  # noqa: F401
from app.models.plan_stability_score import PlanStabilityScore  # noqa: F401
from app.models.order import Order  # noqa: F401
from app.models.order_history import OrderStatusHistory  # noqa: F401
from app.models.order_leg import OrderLeg  # noqa: F401
from app.models.package import Package  # noqa: F401
from app.models.recurring_order import (  # noqa: F401
    RecurrenceCadence,
    RecurringOrderOccurrence,
    RecurringOrderTemplate,
)
from app.models.revoked_token import RevokedToken  # noqa: F401
from app.models.route import Route, RouteStop  # noqa: F401
from app.models.route_history import RouteStatusHistory  # noqa: F401
from app.models.route_performance_snapshot import (  # noqa: F401
    RoutePerformanceSnapshot,
)
from app.models.route_scenario import RouteScenario, RouteScenarioComparison  # noqa: F401
from app.models.stop_history import StopStatusHistory  # noqa: F401
from app.models.system_settings import SystemSettings  # noqa: F401
from app.models.tenant import Tenant  # noqa: F401
from app.models.traffic_cell_prior import TrafficCellPrior  # noqa: F401
from app.models.traffic_profile import TrafficCellProfile  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.vehicle_spec import VehicleSpec  # noqa: F401
from app.models.webhook import ApiClient, WebhookDeliveryLog, WebhookEndpoint  # noqa: F401
