import sys
import logging
from antcam.importer import Model
from antcam.feature_extractor import FeatureExtractor
from antcam.contour_extractor import ContourExtractor
from antcam.viewer import show_model_with_features

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("antcam")


def run_feature_viewer(file_path, rx=0.0, ry=0.0, rz=0.0, enable_planar_merge: bool = True):
    """Carica un modello (STEP o STL) e mostra le feature estratte.

    Per file STL: converte automaticamente in BRep prima dell'analisi.

    Args:
        file_path: percorso del file da caricare
        rx, ry, rz: rotazioni per file STEP
        enable_planar_merge: per STL, abilita/disabilita merging facce planari
    """
    logger.info(f"Avvio estrazione feature per {file_path}")

    # Determina il tipo di file dall'estensione
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

    working_plane_normal = tuple(float(v) for v in extractor.working_plane_normal)
    contour_extractor = ContourExtractor(model, working_plane_normal=working_plane_normal, features=features)
    contour_shadow = contour_extractor.extract()
    perimeter = contour_extractor.extract_perimeter(contour_shadow) if contour_shadow is not None else []

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
    path, rx, ry, rz = MODELS[0]
    if len(sys.argv) > 1:
        path = sys.argv[1]
    run_feature_viewer(path, rx=rx, ry=ry, rz=rz)
