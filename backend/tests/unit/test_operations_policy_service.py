"""Unit tests for operations_policy_service logic that doesn't require a DB."""
from app.services.operations_policy_service import (
    _merge_public_optimization_settings,
    build_internal_optimization_settings,
    optimization_settings_for_preset,
)
from app.schemas.operations_policy import OptimizationSettings


# ---------------------------------------------------------------------------
# _merge_public_optimization_settings
# ---------------------------------------------------------------------------

class TestMergePublicOptimizationSettings:
    def _incoming(
        self,
        *,
        routing_objective="FUEL_AWARE",
        preset_key="BALANCED",
        fuel_multiplier=1.0,
        economic=True,
        shadow=False,
    ) -> OptimizationSettings:
        return OptimizationSettings(
            routing_objective=routing_objective,
            preset_key=preset_key,
            economic_batch_optimizer_enabled=economic,
            fuel_priority_multiplier=fuel_multiplier,
            shadow_mode_enabled=shadow,
        )

    def test_no_internal_override_returns_clean_preset_settings(self):
        current = optimization_settings_for_preset("BALANCED")
        incoming = self._incoming(preset_key="BALANCED", fuel_multiplier=2.0, economic=True)
        result = _merge_public_optimization_settings(current=current, incoming=incoming)

        assert result.routing_objective == "FUEL_AWARE"
        assert result.preset_key == "COST_SAVER"
        assert result.fuel_priority_multiplier == 1.0
        assert result.economic_batch_optimizer_enabled is True
        assert result.internal_override_enabled is False

    def test_internal_override_preserves_public_fields_when_preset_unchanged(self):
        current = optimization_settings_for_preset(
            "BALANCED",
            economic_batch_optimizer_enabled=True,
            fuel_priority_multiplier=1.0,
            internal_override_enabled=True,
        )
        current = current.model_copy(update={"internal_override_enabled": True})
        incoming = self._incoming(preset_key="BALANCED", fuel_multiplier=2.0, economic=True, shadow=False)

        result = _merge_public_optimization_settings(current=current, incoming=incoming)

        # Public fields must be updated even under internal override
        assert result.routing_objective == "FUEL_AWARE"
        assert result.fuel_priority_multiplier == 1.0
        assert result.economic_batch_optimizer_enabled is True
        assert result.internal_override_enabled is True

    def test_internal_override_is_reset_when_public_objective_changes(self):
        """Changing the public objective while internal_override_enabled=True must
        NOT preserve the internal override — the new objective takes full effect."""
        current = optimization_settings_for_preset("BALANCED")
        current = current.model_copy(update={"internal_override_enabled": True})

        incoming = self._incoming(
            routing_objective="SERVICE_FIRST",
            preset_key="SERVICE_FIRST",
            fuel_multiplier=0.0,
            economic=True,
        )
        result = _merge_public_optimization_settings(current=current, incoming=incoming)

        assert result.routing_objective == "SERVICE_FIRST"
        assert result.preset_key == "SERVICE_FIRST"
        assert result.fuel_priority_multiplier == 0.0
        assert result.internal_override_enabled is False

    def test_service_first_disables_fuel_objective_but_keeps_optimizer_enabled(self):
        current = optimization_settings_for_preset("BALANCED")
        incoming = self._incoming(
            routing_objective="SERVICE_FIRST",
            preset_key="SERVICE_FIRST",
            fuel_multiplier=0.0,
            economic=True,
            shadow=False,
        )

        result = _merge_public_optimization_settings(current=current, incoming=incoming)

        assert result.routing_objective == "SERVICE_FIRST"
        assert result.fuel_priority_multiplier == 0.0
        assert result.economic_batch_optimizer_enabled is True
        assert result.shadow_mode_enabled is False


# ---------------------------------------------------------------------------
# build_internal_optimization_settings
# ---------------------------------------------------------------------------

class TestBuildInternalOptimizationSettings:
    def test_preserves_fuel_multiplier_from_current(self):
        current = OptimizationSettings(fuel_priority_multiplier=2.0, economic_batch_optimizer_enabled=True)
        result = build_internal_optimization_settings("SERVICE_FIRST", current=current)

        assert result.fuel_priority_multiplier == 2.0
        assert result.internal_override_enabled is True

    def test_overrides_applied_on_top_of_preset(self):
        result = build_internal_optimization_settings(
            "COST_SAVER",
            overrides={"lateness_penalty_per_minute": 999},
        )
        assert result.lateness_penalty_per_minute == 999
        assert result.preset_key == "COST_SAVER"
        assert result.internal_override_enabled is True
