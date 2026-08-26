"""Integration test: import 3D -> feature ref -> plan (3-axis stretch)."""

from __future__ import annotations


def test_plan3d_workflow(application) -> None:
    import trimesh

    from antcam_rc2.core.geometry3d.scene import FeatureKind
    from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
    from antcam_rc2.core.io3d import import_file_3d
    from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock
    from antcam_rc2.core.project.solid_refs import create_solid_ref
    from antcam_rc2.core.toolpath.settings import PlanningSettings

    project = application.project_service.create_project(
        "3d plan",
        machine_id="makera_z1",
        stock=Stock(width_mm=30, length_mm=30, height_mm=10, material_id="aluminum_6061"),
    )

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        solid_path = Path(directory) / "box.stl"
        trimesh.creation.box(extents=[20.0, 10.0, 5.0]).export(str(solid_path))
        solid = import_file_3d(str(solid_path))

    face = next(
        (body_index, feature_index, feature)
        for body_index, body in enumerate(solid.bodies)
        for feature_index, feature in enumerate(body.features)
        if feature.kind is FeatureKind.FACE_PLANAR and feature.facing
    )
    reference = create_solid_ref(solid, face[0], face[1])
    application.project_service.add_operation(
        project.id,
        OperationType.POCKETING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        solid_refs=(reference,),
        operation_parameters=OperationParameters(depth_mm=2.0),
    )
    project = application.project_service.get_project(project.id)
    empty = GeometryScene(source=SourceInfo(format="dxf"))
    plan = application.toolpath_service.plan_snapshot(
        project, empty, PlanningSettings(clearance_z_mm=5.0), solid_scene=solid
    )
    result = plan.operations[0]
    assert result.status.value == "succeeded"
    assert result.program is not None
    z_values = {round(motion.endpoint.z_mm, 2) for motion in result.program.motions}
    # The centred box has its top face at z=2.5; depth 2 -> cut to z=0.5,
    # clearance = 2.5 + 5 = 7.5.
    assert 0.5 in z_values
    assert 7.5 in z_values
    assert plan.is_executable
