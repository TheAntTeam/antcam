"""Shared test fixtures for AntCAM RC2."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from antcam_rc2.app.application import Application
from antcam_rc2.core.config import AppConfig
from antcam_rc2.core.databases.models import MachineProfile
from antcam_rc2.core.databases.repository import CatalogBundle, CatalogRepository
from antcam_rc2.core.feeds_speeds.calculator import FeedsSpeedsCalculator
from antcam_rc2.core.feeds_speeds.models import FeedSpeedRequest
from antcam_rc2.core.operations.contracts import PlanningContext
from antcam_rc2.core.operations.registry import build_standard_registry
from antcam_rc2.core.project.models import Operation, OperationParameters, OperationType
from antcam_rc2.core.toolpath.depth_passes import plan_depth_passes


@pytest.fixture()
def app_config(tmp_path: Path) -> AppConfig:
    """An isolated AppConfig pointing into a temp directory."""
    return AppConfig(
        log_level="WARNING",
        log_file=None,
        user_data_dir=tmp_path / "user",
        cache_dir=tmp_path / "cache",
    )


@pytest.fixture()
def application(app_config: AppConfig) -> Iterator[Application]:
    """A bootstrapped Application with an isolated config."""
    core = Application(config=app_config)
    yield core
    core.shutdown()


@pytest.fixture()
def catalog_bundle() -> CatalogBundle:
    """The validated package-data catalog bundle."""
    return CatalogRepository.from_package().load()


@pytest.fixture()
def end_mill(catalog_bundle: CatalogBundle):
    """The packaged 3.175 mm two-flute end mill."""
    return catalog_bundle.tools["end_mill_3_175_2f"]


@pytest.fixture()
def drill_tool(catalog_bundle: CatalogBundle):
    """The packaged 2 mm drill."""
    return catalog_bundle.tools["drill_2"]


@pytest.fixture()
def v_bit_tool(catalog_bundle: CatalogBundle):
    """The packaged 30-degree V-bit."""
    return catalog_bundle.tools["v_bit_30"]


@pytest.fixture()
def feeds_calculator() -> FeedsSpeedsCalculator:
    """The deterministic feed/speed calculator."""
    return FeedsSpeedsCalculator()


@pytest.fixture()
def post_mini_plan(application: Application):
    """A tiny executable plan (rapid -> plunge -> cut -> arc -> retract) for post tests."""
    from antcam_rc2.core.feeds_speeds.models import FeedSpeedResult, ValueOrigin
    from antcam_rc2.core.project.models import Operation, OperationParameters, OperationType, Stock
    from antcam_rc2.core.toolpath.models import (
        MotionCommand,
        MotionKind,
        MotionProgram,
        OperationPlanStatus,
        Position3,
        ToolpathOperationResult,
        ToolpathPlan,
    )

    project = application.project_service.create_project(
        "post fixture",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=30.0, height_mm=10.0, material_id="aluminum_6061"),
    )
    project = application.project_service.get_project(project.id)
    operation = Operation(
        id="op_1234abcd",
        name="mini",
        operation_type=OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        parameters=OperationParameters(depth_mm=2.0),
    )
    feeds = FeedSpeedResult(
        requested_rpm=13000.0,
        rpm=12000.0,
        cut_feed_mm_min=520.0,
        plunge_feed_mm_min=208.0,
        stepdown_mm=1.0,
        stepover_mm=1.0,
        effective_surface_speed_m_min=180.0,
        effective_chip_load_mm_tooth=0.02,
        engagement_factor=1.0,
        origins={"rpm": ValueOrigin.AUTOMATIC},
        clamps_applied=(),
    )
    result = ToolpathOperationResult(
        operation_id=operation.id,
        operation_type=operation.operation_type,
        status=OperationPlanStatus.SUCCEEDED,
        program=MotionProgram(
            motions=(
                MotionCommand(
                    kind=MotionKind.RAPID, endpoint=Position3(x_mm=5.0, y_mm=5.0, z_mm=15.0), operation_id=operation.id
                ),
                MotionCommand(
                    kind=MotionKind.CUT_LINEAR,
                    endpoint=Position3(x_mm=5.0, y_mm=5.0, z_mm=9.0),
                    feed_mm_min=208.0,
                    operation_id=operation.id,
                ),
                MotionCommand(
                    kind=MotionKind.CUT_LINEAR,
                    endpoint=Position3(x_mm=15.0, y_mm=5.0, z_mm=9.0),
                    feed_mm_min=520.0,
                    operation_id=operation.id,
                ),
                MotionCommand(
                    kind=MotionKind.CUT_ARC_CCW,
                    endpoint=Position3(x_mm=15.0, y_mm=5.0, z_mm=9.0),
                    feed_mm_min=520.0,
                    arc_center_xy=(15.0, 0.0),
                    operation_id=operation.id,
                ),
                MotionCommand(
                    kind=MotionKind.RAPID, endpoint=Position3(x_mm=15.0, y_mm=5.0, z_mm=15.0), operation_id=operation.id
                ),
            )
        ),
        feeds_speeds=feeds,
        pass_count=1,
    )
    plan = ToolpathPlan(project_id=project.id, operations=(result,))
    project = project.model_copy(update={"operations": (operation,)})
    application.project_service._projects.replace(project)
    return application.project_service.get_project(project.id), plan


@pytest.fixture()
def make_context(catalog_bundle: CatalogBundle, feeds_calculator: FeedsSpeedsCalculator):
    """Factory building a fully resolved PlanningContext for strategy unit tests.

    The stock is 5 mm tall with its bottom at Z=0, so the top is at
    ``top_z_mm`` and the bottom at ``top_z_mm - 5`` by default.
    """

    def _make(
        *,
        op_type: OperationType | str,
        tool_id: str,
        entities: tuple,
        params: dict | None = None,
        depth_mm: float = 2.0,
        stepdown_mm: float | None = None,
        stock_allowance_mm: float = 0.0,
        machine_id: str = "makera_z1",
        material_id: str = "aluminum_6061",
        cooling_id: str = "aerodust",
        machine_override: MachineProfile | None = None,
        top_z_mm: float = 5.0,
        clearance_z_mm: float = 10.0,
        tolerance_mm: float = 0.01,
        operation: Operation | None = None,
        depth_passes: tuple[float, ...] | None = None,
    ) -> PlanningContext:
        registry = build_standard_registry()
        definition = registry.definition(op_type)
        tool = catalog_bundle.tools[tool_id]
        machine = machine_override or catalog_bundle.machines[machine_id]
        material = catalog_bundle.materials[material_id]
        cooling = catalog_bundle.cooling[cooling_id]
        strategy_parameters = params or {}
        feeds = feeds_calculator.calculate(
            FeedSpeedRequest(
                tool=tool,
                material=material,
                machine=machine,
                cooling=cooling,
                operation_family=definition.family,
                pitch_mm=strategy_parameters.get("pitch_mm"),
            )
        )
        resolved_op = operation or Operation(
            id="op_1234abcd",
            name="test operation",
            operation_type=definition.operation_type,
            tool_id=tool_id,
            cooling_id=cooling_id,
            parameters=OperationParameters(
                depth_mm=depth_mm,
                stepdown_mm=stepdown_mm,
                stock_allowance_mm=stock_allowance_mm,
                strategy_parameters=strategy_parameters,
            ),
        )
        params_model = definition.parameters_model.model_validate(strategy_parameters)
        if depth_passes is None:
            target_z = top_z_mm - (depth_mm if depth_mm is not None else stock_allowance_mm)
            depths = plan_depth_passes(
                top_z_mm=top_z_mm,
                target_z_mm=target_z,
                max_stepdown_mm=stepdown_mm or feeds.stepdown_mm,
                stock_bottom_z_mm=top_z_mm - 5.0,
            )
        else:
            depths = depth_passes
        return PlanningContext(
            operation=resolved_op,
            params=params_model,
            entities=entities,
            tool=tool,
            machine=machine,
            feeds_speeds=feeds,
            depth_passes_z_mm=depths,
            top_z_mm=top_z_mm,
            clearance_z_mm=clearance_z_mm,
            tolerance_mm=tolerance_mm,
        )

    return _make
