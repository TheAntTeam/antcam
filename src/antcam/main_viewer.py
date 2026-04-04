import sys
import logging
from antcam.importer import Model
from antcam.feature_extractor import FeatureExtractor
from antcam.contour_extractor import ContourExtractor
from antcam.viewer import show_model_with_features

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("antcam")

def run_feature_viewer(step_file_path, rx=0.0, ry=0.0, rz=0.0):
    logger.info(f"Avvio estrazione feature per {step_file_path}")
    try:
        model = Model.from_step(step_file_path, rx=rx, ry=ry, rz=rz)
    except Exception as e:
        logger.error(f"Errore caricamento file: {e}")
        return

    extractor = FeatureExtractor(model)
    features = extractor.extract()
    vertical_arc_groups = extractor.find_vertical_faces_with_xy_arcs()
    logger.info(f"Trovate {len(features)} feature:")
    for f in features:
        logger.info(f"  - {f}")

    contour_extractor = ContourExtractor(model, working_plane_normal=tuple(extractor.working_plane_normal), features=features)
    contour_shadow = contour_extractor.extract()
    perimeter = contour_extractor.extract_perimeter(contour_shadow) if contour_shadow is not None else []

    show_model_with_features(model, features,
                             working_plane_normal=tuple(extractor.working_plane_normal),
                             contour_shadow=contour_shadow,
                             perimeter=perimeter,
                             vertical_arc_groups=vertical_arc_groups)


MODELS = [
    ("../../tests/data/bottle_opener.step", 0.0, 0.0, 0.0),
    ("../../tests/data/flange.step",        0.0, 0.0, 0.0),
    ("../../tests/data/mounting_spider.step", 0.0, 0.0, 0.0),
    ("../../tests/data/servo_mount.step",   90.0, 0.0, 0.0),
]

if __name__ == "__main__":
    path, rx, ry, rz = MODELS[3]
    if len(sys.argv) > 1:
        path = sys.argv[1]
    run_feature_viewer(path, rx=rx, ry=ry, rz=rz)
