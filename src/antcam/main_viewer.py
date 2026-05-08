import sys
import logging
from typing import Tuple
from antcam.importer import Model
from antcam.feature_extractor import FeatureExtractor
from antcam.contour_extractor import ContourExtractor
from antcam.viewer import show_model_with_features
from antcam.path_generator import AutoToolpathPlanner

"""Entry point for loading a model and opening the interactive feature viewer.

This module wires together import, feature extraction, contour extraction, and
viewer presentation. It is intentionally thin and orchestration-focused.
"""

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("antcam")


def run_feature_viewer(file_path, rx=0.0, ry=0.0, rz=0.0, enable_planar_merge: bool = True):
    """Load a CAD file, extract analysis data, and launch the viewer.

    The function supports STEP/STP and STL input. For STEP/STP, optional
    Euler rotations can be applied before extraction. For STL, the current
    pipeline runs in mesh mode and gracefully skips BRep-only operations
    downstream when no BRep is available.

    Args:
        file_path: Input file path.
        rx: Rotation around X axis (degrees), STEP/STP only.
        ry: Rotation around Y axis (degrees), STEP/STP only.
        rz: Rotation around Z axis (degrees), STEP/STP only.
        enable_planar_merge: Reserved compatibility flag for STL import path.
    """
    logger.info(f"Avvio estrazione feature per {file_path}")

    # Route import path by extension to keep STEP and STL workflows explicit.
    file_extension = file_path.lower().split('.')[-1]

    try:
        if file_extension in ['stl']:
            logger.info("File STL rilevato - conversione automatica in BRep")
            if not enable_planar_merge:
                logger.info("Merging planare disabilitato")
            model = Model.from_stl(file_path, convert_to_brep=True)
        elif file_extension in ['step', 'stp']:
            logger.info("File STEP rilevato")
            model = Model.from_step(file_path, rx=rx, ry=ry, rz=rz)
        else:
            raise ValueError(f"Formato file non supportato: .{file_extension}. Usa .stl o .step/.stp")

        # Hard stop if import produced neither a BRep nor a mesh.
        if not model.brep and not model.mesh:
            raise ValueError("Nessun modello valido caricato")

    except Exception as e:
        logger.error(f"Errore caricamento file: {e}")
        return

    extractor = FeatureExtractor(model)
    features = extractor.extract()
    vertical_arc_groups = extractor.find_vertical_faces_with_xy_arcs()
    hole_groups = [f for f in features if getattr(f, "type", "") == "hole_group"]
    through_hole_groups = [f for f in hole_groups if f.props.get("through")]
    logger.info(
        "Diagnostica fori: hole_group=%d, passanti=%d, arc_groups=%d",
        len(hole_groups),
        len(through_hole_groups),
        len(vertical_arc_groups),
    )
    logger.info(f"Trovate {len(features)} feature:")
    for f in features:
        logger.info(f"  - {f}")

    # Normalize to plain floats for stable downstream serialization/logging.
    working_plane_normal: Tuple[float, float, float] = tuple(float(v) for v in extractor.working_plane_normal)
    contour_extractor = ContourExtractor(model, working_plane_normal=working_plane_normal, features=features)
    contour_shadow = contour_extractor.extract()
    perimeter = contour_extractor.extract_perimeter(contour_shadow) if contour_shadow is not None else []

    planner = AutoToolpathPlanner(working_plane_normal=working_plane_normal)
    toolpath_plan = planner.generate(features=features, perimeter_wires=perimeter)
    drill_ops = sum(1 for op in toolpath_plan.operations if op.strategy == "drilling")
    profile_ops = sum(1 for op in toolpath_plan.operations if op.strategy == "2p5d_profile")
    logger.info(
        "Toolpath plan: operazioni=%d (drilling=%d, profile=%d) warning=%d",
        len(toolpath_plan.operations),
        drill_ops,
        profile_ops,
        len(toolpath_plan.warnings),
    )

    show_model_with_features(model, features,
                             working_plane_normal=working_plane_normal,
                             contour_shadow=contour_shadow,
                             perimeter=perimeter,
                             vertical_arc_groups=vertical_arc_groups)


MODELS = [
    # File STEP (geometria CAD nativa)
    ("../../tests/data/bottle_opener.step", 0.0, 0.0, 0.0),
    ("../../tests/data/flange.step",        0.0, 0.0, 0.0),
    ("../../tests/data/mounting_spider.step", 0.0, 0.0, 0.0),
    ("../../tests/data/servo_mount.step",   90.0, 0.0, 0.0),
    # File STL (mesh triangolare - convertito automaticamente in BRep)
    ("../../tests/data/MALE_BUCKLE.stl",   0.0, 0.0, 0.0),
]

if __name__ == "__main__":
    path, rx, ry, rz = MODELS[2]
    if len(sys.argv) > 1:
        path = sys.argv[1]
    run_feature_viewer(path, rx=rx, ry=ry, rz=rz)
