import pytest
import os
import trimesh
from antcam.importer import Model
from antcam.feature_extractor import FeatureExtractor, HoleFeature, CountersunkHoleFeature, PocketFeature, SlotFeature

@pytest.fixture
def step_file_path():
    # Cerca il file step nei dati di test
    path = "tests/data/cube.step"
    if not os.path.exists(path):
        pytest.skip(f"File {path} non trovato per il test")
    return path

@pytest.fixture
def stl_file_path():
    path = "tests/data/cube.stl"
    if not os.path.exists(path):
        pytest.skip(f"File {path} non trovato per il test")
    return path

def test_extract_from_real_step(step_file_path):
    # Carica il modello reale usando l'importer del progetto
    model = Model.from_step(step_file_path)
    extractor = FeatureExtractor(model)
    features = extractor.extract()
    
    print(f"\nFeature trovate nello STEP ({step_file_path}):")
    for f in features:
        print(f"  - {f}")
    
    # Asserzioni minime: deve trovare almeno qualcosa
    assert len(features) > 0
    # Se il tuo cube.step ha un foro, qui possiamo aggiungere controlli specifici

def test_extract_from_real_stl(stl_file_path):
    model = Model.from_stl(stl_file_path)
    extractor = FeatureExtractor(model)
    features = extractor.extract()
    
    print(f"\nFeature trovate nell'STL ({stl_file_path}):")
    for f in features:
        print(f"  - {f}")
    
    assert len(features) > 0

def test_composite_feature_logic_mock():
    # Test della logica di raggruppamento con dati simulati (mock)
    class MockModel:
        def __init__(self, brep=None, mesh=None):
            self.brep = brep
            self.mesh = mesh

    extractor = FeatureExtractor(MockModel())
    
    # Simuliamo un foro svasato (cilindro + cono coassiali)
    cylinders = [{
        "radius": 5.0,
        "axis": (0, 0, 1),
        "center": (0, 0, 0),
        "depth": 10.0,
        "face": None
    }]
    
    cones = [{
        "angle": 45.0,
        "radii": (5.0, 8.0),
        "axis": (0, 0, 1),
        "center": (0, 0, 10.0), # Posizionato sopra il cilindro
        "v_range": (0, 5),
        "face": None
    }]
    
    extractor._group_composite_features(cylinders, cones)
    
    # Verifica che sia stata creata la feature composta corretta
    assert any(isinstance(f, CountersunkHoleFeature) for f in extractor.features)
    cs_hole = next(f for f in extractor.features if isinstance(f, CountersunkHoleFeature))
    assert cs_hole.props["diameter"] == 10.0
    assert cs_hole.props["cs_diameter"] == 16.0
