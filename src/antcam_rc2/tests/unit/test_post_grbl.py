"""Golden tests for the GRBL post-processor (byte-identical output)."""

from __future__ import annotations

from antcam_rc2.core.post import PostService, PostSettings


def _post_lines(application, project, plan, **settings_overrides) -> list[str]:
    service = PostService(application.catalog_repository)
    settings = PostSettings(program_id="mini", **settings_overrides)
    return list(service.post(project, plan, settings=settings, post_id="grbl").lines)


def test_grbl_golden_output(application, post_mini_plan) -> None:
    project, plan = post_mini_plan
    lines = _post_lines(application, project, plan)
    assert lines == [
        "; AntCAM RC2 — post grbl v1.0",
        "; program: mini",
        "; machine: Makera Z1",
        "G90",
        "G21",
        "G17",
        "; --- operation op_1234abcd (mini) ---",
        "M6 T1",
        "M3 S12000",
        "G0 X5 Y5 Z15",
        "G1 X5 Y5 Z9 F208",
        "X15 Y5 Z9 F520",
        "G3 X15 Y5 Z9 I0 J-5",  # feed already modal at 520
        "G0 X15 Y5 Z15",
        "M5",
        "; end — 5 motion lines",
        "M2",
    ]


def test_grbl_merge_rapids(application) -> None:
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
        "merge",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=30.0, height_mm=10.0, material_id="aluminum_6061"),
    )
    project = application.project_service.get_project(project.id)
    operation = Operation(
        id="op_1234abcd",
        name="merge",
        operation_type=OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        parameters=OperationParameters(depth_mm=2.0),
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
                    kind=MotionKind.RAPID, endpoint=Position3(x_mm=20.0, y_mm=5.0, z_mm=15.0), operation_id=operation.id
                ),
                MotionCommand(
                    kind=MotionKind.RAPID, endpoint=Position3(x_mm=25.0, y_mm=5.0, z_mm=15.0), operation_id=operation.id
                ),
                MotionCommand(
                    kind=MotionKind.CUT_LINEAR,
                    endpoint=Position3(x_mm=25.0, y_mm=5.0, z_mm=9.0),
                    feed_mm_min=200.0,
                    operation_id=operation.id,
                ),
            )
        ),
        pass_count=1,
    )
    plan = ToolpathPlan(project_id=project.id, operations=(result,))
    project = project.model_copy(update={"operations": (operation,)})
    application.project_service._projects.replace(project)
    project = application.project_service.get_project(project.id)

    service = PostService(application.catalog_repository)
    merged = service.post(project, plan, settings=PostSettings(program_id="mini"), post_id="grbl")
    unmerged = service.post(
        project, plan, settings=PostSettings(program_id="mini", merge_consecutive_rapids=False), post_id="grbl"
    )
    # Three consecutive rapids at the same Z merge into one motion line.
    assert merged.motion_line_count == 2  # 1 merged link + 1 cut
    assert unmerged.motion_line_count == 4  # 3 separate links + 1 cut
    assert len(merged.lines) < len(unmerged.lines)


def test_grbl_feed_is_modal_and_emitted_on_change(application, post_mini_plan) -> None:
    project, plan = post_mini_plan
    lines = _post_lines(application, project, plan)
    # First cut carries F208 (plunge feed); the feed change to 520 re-emits F;
    # the arc at the same 520 feed does not repeat F (modal).
    assert "G1 X5 Y5 Z9 F208" in lines
    assert "X15 Y5 Z9 F520" in lines
    assert "G3 X15 Y5 Z9 I0 J-5" in lines and "G3 X15 Y5 Z9 I0 J-5 F520" not in lines


def test_grbl_number_formatting_strips_trailing_zeros(application, post_mini_plan) -> None:
    project, plan = post_mini_plan
    lines = _post_lines(application, project, plan, decimals=3)
    assert "G1 X5 Y5 Z9 F208" in lines  # integers stay minimal, no ".000"
