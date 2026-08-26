"""Ad-hoc test to verify render scene positioning and camera framing.

This test helps diagnose the issue where stock, fixtures, and geometries
are not visible in the OpenGL viewport (only the grid is visible).
"""

from __future__ import annotations

import numpy as np

from antcam_rc2.core.geometry.curves import Circle, LineSegment
from antcam_rc2.core.geometry.paths import Contour
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
from antcam_rc2.core.project.models import Fixture, FixtureKind, Project, Stock
from antcam_rc2.core.rendering import (
    compose_scenes,
    geometry_to_scene,
    setup_to_scene,
)
from antcam_rc2.core.rendering.scene_graph import RenderScene
from antcam_rc2.frontends.pyside.viewport.camera import OrbitCamera


def make_test_geometry() -> GeometryScene:
    """Create a simple test geometry scene."""
    scene = GeometryScene(source=SourceInfo(format="dxf", path="test.dxf"))
    # Add a square contour at origin
    points = [Point2(0.0, 0.0), Point2(50.0, 0.0), Point2(50.0, 50.0), Point2(0.0, 50.0)]
    scene.add_entity(
        Contour([LineSegment(a, b) for a, b in zip(points, points[1:] + [points[0]], strict=True)]),
        "profile"
    )
    # Add a circle in the center
    scene.add_entity(Circle(Point2(25.0, 25.0), 10.0), "holes")
    return scene


def make_test_project() -> Project:
    """Create a test project with stock and fixture."""
    stock = Stock(
        width_mm=100.0,
        length_mm=100.0,
        height_mm=20.0,
        material_id="aluminum_6061",
        position_x_mm=0.0,
        position_y_mm=0.0,
        position_z_mm=0.0,
    )
    fixture = Fixture(
        id="fix_1234abcd",
        name="vise",
        kind=FixtureKind.FIXED,
        width_mm=60.0,
        length_mm=30.0,
        height_mm=15.0,
        position_x_mm=20.0,
        position_y_mm=35.0,
        position_z_mm=0.0,
    )
    return Project(
        id="proj_1234abcd",
        name="positioning_test",
        created_at="2024-01-01T00:00:00Z",
        modified_at="2024-01-01T00:00:00Z",
        machine_id="makera_z1",
        stock=stock,
        fixtures=(fixture,),
    )


def print_scene_info(name: str, scene: RenderScene) -> None:
    """Print detailed info about a render scene."""
    box = scene.bounding_box()
    print(f"\n=== {name} ===")
    print(f"  Nodes: {len(scene.nodes)}, Meshes: {len(scene.meshes)}, Picking entries: {len(scene.picking)}")
    print(f"  Bounding box:")
    if box.is_empty:
        print(f"    EMPTY")
    else:
        print(f"    min: ({box.min_x:.2f}, {box.min_y:.2f}, {box.min_z:.2f})")
        print(f"    max: ({box.max_x:.2f}, {box.max_y:.2f}, {box.max_z:.2f})")
        print(f"    center: ({box.center[0]:.2f}, {box.center[1]:.2f}, {box.center[2]:.2f})")
        print(f"    size: ({box.width:.2f}, {box.height:.2f}, {box.depth:.2f})")
    
    for i, node in enumerate(scene.nodes):
        if node.box is not None:
            print(f"  Node {i}: {node.kind.value} box=({node.box.min_x:.1f},{node.box.min_y:.1f},{node.box.min_z:.1f})->({node.box.max_x:.1f},{node.box.max_y:.1f},{node.box.max_z:.1f}) color={node.color}")
        else:
            pts = np.array(node.points).reshape(-1, 3) if node.points else np.array([])
            if len(pts) > 0:
                print(f"  Node {i}: {node.kind.value} points={len(pts)} range=({pts[:,0].min():.1f},{pts[:,1].min():.1f},{pts[:,2].min():.1f})->({pts[:,0].max():.1f},{pts[:,1].max():.1f},{pts[:,2].max():.1f}) color={node.color}")
            else:
                print(f"  Node {i}: {node.kind.value} EMPTY color={node.color}")
    
    for i, mesh in enumerate(scene.meshes):
        verts = np.array(mesh.vertices).reshape(-1, 3) if mesh.vertices else np.array([])
        if len(verts) > 0:
            print(f"  Mesh {i}: vertices={len(verts)} triangles={len(mesh.triangles)//3} range=({verts[:,0].min():.1f},{verts[:,1].min():.1f},{verts[:,2].min():.1f})->({verts[:,0].max():.1f},{verts[:,1].max():.1f},{verts[:,2].max():.1f}) color={mesh.color}")


def run_camera_fit(camera: OrbitCamera, scene_box, viewport_size: tuple[int, int]) -> None:
    """Test camera fit_to and verify the result."""
    print(f"\n=== Camera Fit Test ===")
    print(f"  Before fit: target={camera.target}, distance={camera.distance:.2f}")
    camera.fit_to(scene_box, viewport_size)
    print(f"  After fit:  target={camera.target}, distance={camera.distance:.2f}")
    
    # Check if target matches scene center
    expected_target = np.array(scene_box.center)
    actual_target = camera.target
    diff = np.linalg.norm(actual_target - expected_target)
    print(f"  Target error: {diff:.4f} mm (expected {expected_target}, got {actual_target})")
    
    # Check if distance is reasonable
    diagonal = np.sqrt(scene_box.width**2 + scene_box.height**2 + scene_box.depth**2)
    expected_distance = diagonal / (2.0 * np.tan(camera.fov / 2.0)) * 1.4 + diagonal * 0.1
    print(f"  Expected distance: {expected_distance:.2f}, Actual: {camera.distance:.2f}")


def run_projection(camera: OrbitCamera, scene_box, viewport_size: tuple[int, int]) -> None:
    """Test that scene corners project to reasonable screen coordinates."""
    print(f"\n=== Projection Test ===")
    view = camera.view_matrix()
    proj = camera.projection_matrix(viewport_size[0] / viewport_size[1])
    vp = proj @ view
    
    corners = [
        (scene_box.min_x, scene_box.min_y, scene_box.min_z),
        (scene_box.max_x, scene_box.min_y, scene_box.min_z),
        (scene_box.min_x, scene_box.max_y, scene_box.min_z),
        (scene_box.max_x, scene_box.max_y, scene_box.min_z),
        (scene_box.min_x, scene_box.min_y, scene_box.max_z),
        (scene_box.max_x, scene_box.min_y, scene_box.max_z),
        (scene_box.min_x, scene_box.max_y, scene_box.max_z),
        (scene_box.max_x, scene_box.max_y, scene_box.max_z),
    ]
    
    all_inside = True
    for i, (x, y, z) in enumerate(corners):
        # Project to clip space
        p = vp @ np.array([x, y, z, 1.0], dtype=np.float64)
        if abs(p[3]) > 1e-9:
            ndc = p[:3] / p[3]
            screen_x = (ndc[0] + 1.0) * 0.5 * viewport_size[0]
            screen_y = (1.0 - ndc[1]) * 0.5 * viewport_size[1]
            inside = -0.5 <= ndc[0] <= 1.5 and -0.5 <= ndc[1] <= 1.5 and -1.0 <= ndc[2] <= 1.0
            print(f"  Corner {i}: ({x:.1f},{y:.1f},{z:.1f}) -> NDC({ndc[0]:.3f},{ndc[1]:.3f},{ndc[2]:.3f}) Screen({screen_x:.1f},{screen_y:.1f}) inside={inside}")
            if not inside:
                all_inside = False
        else:
            print(f"  Corner {i}: ({x:.1f},{y:.1f},{z:.1f}) -> CLIP (w={p[3]:.3f}) BEHIND CAMERA")
            all_inside = False
    
    print(f"  All corners in frustum: {all_inside}")
    return all_inside


def main():
    """Run the positioning diagnostic test."""
    print("=" * 60)
    print("RENDER POSITIONING DIAGNOSTIC TEST")
    print("=" * 60)
    
    # Create test data - simulate the actual import_geometry flow
    project = make_test_project()
    geometry_scene = make_test_geometry()
    
    # Simulate _center_geometry_in_stock (what import_geometry does)
    from antcam_rc2.core.geometry.transform import Affine2D, apply
    from antcam_rc2.core.io.scene import GeometryScene
    
    bbox = geometry_scene.bounding_box()
    stock = project.stock
    wcs = project.wcs
    target_x = stock.position_x_mm + wcs.offset_x_mm + stock.width_mm / 2.0
    target_y = stock.position_y_mm + wcs.offset_y_mm + stock.length_mm / 2.0
    translation = Affine2D.translate(target_x - bbox.center.x, target_y - bbox.center.y)
    
    centered_scene = GeometryScene(
        source=geometry_scene.source.model_copy(deep=True),
        units=geometry_scene.units,
        tolerance_mm=geometry_scene.tolerance_mm,
        diagnostics=geometry_scene.diagnostics,
    )
    for layer in geometry_scene.layers:
        for entity in layer.entities:
            centered_scene.add_entity(apply(translation, entity), layer.name)
    
    print(f"Original geometry bbox: min=({bbox.min_x:.1f},{bbox.min_y:.1f}) max=({bbox.max_x:.1f},{bbox.max_y:.1f}) center=({bbox.center.x:.1f},{bbox.center.y:.1f})")
    print(f"Stock: pos=({stock.position_x_mm:.1f},{stock.position_y_mm:.1f},{stock.position_z_mm:.1f}) size=({stock.width_mm:.1f},{stock.length_mm:.1f},{stock.height_mm:.1f})")
    print(f"WCS offset: ({wcs.offset_x_mm:.1f},{wcs.offset_y_mm:.1f},{wcs.offset_z_mm:.1f})")
    print(f"Target center: ({target_x:.1f},{target_y:.1f})")
    
    centered_bbox = centered_scene.bounding_box()
    print(f"Centered geometry bbox: min=({centered_bbox.min_x:.1f},{centered_bbox.min_y:.1f}) max=({centered_bbox.max_x:.1f},{centered_bbox.max_y:.1f}) center=({centered_bbox.center.x:.1f},{centered_bbox.center.y:.1f})")
    
    # Build render scenes with CENTERED geometry
    geometry_graph = geometry_to_scene(centered_scene)
    setup_graph = setup_to_scene(project, machine=None)
    composed = compose_scenes(setup_graph, geometry_graph)
    
    # Print detailed info
    print_scene_info("Geometry Graph", geometry_graph)
    print_scene_info("Setup Graph", setup_graph)
    print_scene_info("Composed Scene", composed)
    
    # Test camera framing
    scene_box = composed.bounding_box()
    viewport_size = (1280, 860)
    
    # Test camera framing - initial state (before fit_to)
    print(f"\n=== Initial Camera State (before fit_to) ===")
    camera_initial = OrbitCamera()  # Default: target=(0,0,0), distance=100, yaw=-45, pitch=35
    print(f"  Default camera: target={camera_initial.target}, distance={camera_initial.distance:.2f}, yaw={np.degrees(camera_initial.yaw):.1f}°, pitch={np.degrees(camera_initial.pitch):.1f}°")
    run_projection(camera_initial, scene_box, viewport_size)
    
    # Simulate the FIX: call fit_view() after set_render_scene (what _on_scene_changed now does)
    print(f"\n=== After Auto-Fix (fit_view called) ===")
    camera_after_fix = OrbitCamera()
    camera_after_fix.fit_to(scene_box, viewport_size)
    print(f"  Camera after fit: target={camera_after_fix.target}, distance={camera_after_fix.distance:.2f}")
    run_projection(camera_after_fix, scene_box, viewport_size)
    
    # Test grid vertices at initial camera
    print(f"\n=== Grid Vertices Test ===")
    from antcam_rc2.frontends.pyside.viewport.buffers import grid_vertices
    grid_data = grid_vertices(scene_box, 10.0, major_spacing_mm=50.0)
    print(f"  Grid vertices: {len(grid_data)} vertices")
    if len(grid_data) > 0:
        grid_z = grid_data[0, 2]  # Z coordinate of first vertex
        print(f"  Grid Z level: {grid_z:.2f}")
        # Check grid corner projections
        grid_positions = grid_data[:, 0:3]
        print(f"  Grid X range: [{grid_positions[:,0].min():.1f}, {grid_positions[:,0].max():.1f}]")
        print(f"  Grid Y range: [{grid_positions[:,1].min():.1f}, {grid_positions[:,1].max():.1f}]")
        
        # Project grid corners with initial camera
        view = camera_initial.view_matrix()
        proj = camera_initial.projection_matrix(viewport_size[0] / viewport_size[1])
        vp = proj @ view
        for i in range(min(4, len(grid_positions))):
            x, y, z = grid_positions[i]
            p = vp @ np.array([x, y, z, 1.0], dtype=np.float64)
            if abs(p[3]) > 1e-9:
                ndc = p[:3] / p[3]
                screen_x = (ndc[0] + 1.0) * 0.5 * viewport_size[0]
                screen_y = (1.0 - ndc[1]) * 0.5 * viewport_size[1]
                print(f"  Grid corner {i}: ({x:.1f},{y:.1f},{z:.1f}) -> Screen({screen_x:.1f},{screen_y:.1f}) NDC({ndc[0]:.3f},{ndc[1]:.3f})")
        
        # Project grid corners with FIXED camera
        print(f"  Grid corners with FIXED camera:")
        view_fixed = camera_after_fix.view_matrix()
        proj_fixed = camera_after_fix.projection_matrix(viewport_size[0] / viewport_size[1])
        vp_fixed = proj_fixed @ view_fixed
        for i in range(min(4, len(grid_positions))):
            x, y, z = grid_positions[i]
            p = vp_fixed @ np.array([x, y, z, 1.0], dtype=np.float64)
            if abs(p[3]) > 1e-9:
                ndc = p[:3] / p[3]
                screen_x = (ndc[0] + 1.0) * 0.5 * viewport_size[0]
                screen_y = (1.0 - ndc[1]) * 0.5 * viewport_size[1]
                inside = -0.5 <= ndc[0] <= 1.5 and -0.5 <= ndc[1] <= 1.5
                print(f"  Grid corner {i}: ({x:.1f},{y:.1f},{z:.1f}) -> Screen({screen_x:.1f},{screen_y:.1f}) inside={inside}")
    
    # Test camera framing - after fit_to (old test)
    camera = OrbitCamera()
    run_camera_fit(camera, scene_box, viewport_size)
    
    # Test projection of scene corners
    all_inside = run_projection(camera, scene_box, viewport_size)
    
    # Also test with stock box directly
    print(f"\n=== Stock Box Camera Test ===")
    from antcam_rc2.core.rendering.builder import RenderBox
    stock_box = RenderBox(
        min_x=project.stock.position_x_mm,
        min_y=project.stock.position_y_mm,
        min_z=project.stock.position_z_mm,
        max_x=project.stock.position_x_mm + project.stock.width_mm,
        max_y=project.stock.position_y_mm + project.stock.length_mm,
        max_z=project.stock.position_z_mm + project.stock.height_mm,
    )
    print(f"  Stock box: ({stock_box.min_x:.1f},{stock_box.min_y:.1f},{stock_box.min_z:.1f})->({stock_box.max_x:.1f},{stock_box.max_y:.1f},{stock_box.max_z:.1f})")
    
    camera2 = OrbitCamera()
    camera2.fit_to(stock_box, viewport_size)
    print(f"  Camera target: {camera2.target}, distance: {camera2.distance:.2f}")
    run_projection(camera2, stock_box, viewport_size)
    
    # Test with WCS offset
    print(f"\n=== With WCS Offset Test ===")
    from antcam_rc2.core.project.models import WorkCoordinateSystem
    project_with_wcs = Project(
        id="proj_5678abcd",
        name="wcs_test",
        created_at="2024-01-01T00:00:00Z",
        modified_at="2024-01-01T00:00:00Z",
        machine_id="makera_z1",
        stock=Stock(width_mm=100.0, length_mm=100.0, height_mm=20.0, material_id="aluminum_6061"),
        fixtures=(Fixture(id="fix_5678abcd", name="vise2", kind=FixtureKind.FIXED, width_mm=60.0, length_mm=30.0, height_mm=15.0, position_x_mm=20.0, position_y_mm=35.0, position_z_mm=0.0),),
        wcs=WorkCoordinateSystem(offset_x_mm=10.0, offset_y_mm=20.0, offset_z_mm=5.0),
    )
    setup_with_offset = setup_to_scene(project_with_wcs, machine=None)
    print_scene_info("Setup with WCS offset", setup_with_offset)
    
    # Verify the geometry was centered in stock
    print(f"\n=== Geometry Centering Test ===")
    print(f"  Original geometry bbox: {geometry_scene.bounding_box()}")
    print(f"  After centering in stock (100x100 at 0,0):")
    print(f"    Expected geometry center at stock center (50, 50)")
    print(f"    Stock: pos=(0,0,0) size=(100,100,20)")
    print(f"    WCS offset: (0,0,0)")
    print(f"    Stock center: (50, 50, 10)")


if __name__ == "__main__":
    main()