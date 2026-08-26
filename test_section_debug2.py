"""Find root cause: explore section shape type and try direct wire extraction."""
import sys, os
sys.path.insert(0, r"C:\TheAntFarmRepo\antcam\src")
os.chdir(r"C:\TheAntFarmRepo\antcam")

from antcam.importer import Model
import numpy as np

model = Model.from_step(r"C:\TheAntFarmRepo\antcam\tests\data\mounting_spider.step")
brep = model.brep

from OCP.gp import gp_Pnt, gp_Dir, gp_Ax3, gp_Pln
from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
from OCP.TopExp import TopExp_Explorer, TopExp
from OCP.TopAbs import TopAbs_EDGE, TopAbs_WIRE, TopAbs_VERTEX, TopAbs_SHAPE
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_ListOfShape, TopTools_HSequenceOfShape
from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAbs import GeomAbs_CurveType

z = 5.0
nx, ny, nz = (0, 0, 1)
origin = gp_Pnt(nx * z, ny * z, nz * z)
gp_normal = gp_Dir(nx, ny, nz)
ref = gp_Dir(1, 0, 0)
u_dir = ref.Crossed(gp_normal)
plane = gp_Pln(gp_Ax3(origin, gp_normal, u_dir))

algo = BRepAlgoAPI_Section(brep, plane)
algo.Build()
shape = algo.Shape()

# What type is the shape?
print(f"Shape type: {shape.ShapeType()}")
print(f"  TopAbs_SHAPE={TopAbs_SHAPE}")
print(f"  TopAbs_WIRE={TopAbs_WIRE}")

# Check if wires can be found directly
exp_w = TopExp_Explorer(shape, TopAbs_WIRE)
n_w = 0
while exp_w.More():
    n_w += 1
    exp_w.Next()
print(f"Direct wire exploration: {n_w} wires")

# Does the section algorithm support SectionEdges()?
try:
    edges_from_section = algo.SectionEdges()
    print(f"SectionEdges: {edges_from_section}")
except Exception as e:
    print(f"SectionEdges failed: {e}")

# Try SectionEdges
sec_edges = algo.SectionEdges()
print(f"SectionEdges count: {sec_edges.Size()}")

# Get edges from SectionEdges
edges_from_section = []
it = sec_edges
while it.Size() > 0:
    e = it.First()
    edges_from_section.append(e)
    it.Remove(it.First())
    if not it.Size():
        break
# Actually, let's iterate properly
# TopTools_ListOfShape has First/Last/Next etc
from OCP.TopTools import TopTools_ListIteratorOfListOfShape
it2 = TopTools_ListIteratorOfListOfShape(sec_edges)
n_sec_edges = 0
while it2.More():
    n_sec_edges += 1
    it2.Next()
print(f"SectionEdges via iterator: {n_sec_edges} edges")

# Check if degenerate edge (edge 24) is actually a closed wire candidate
# Collect edges, check circular edges
edges_seq = TopTools_HSequenceOfShape()
exp_e = TopExp_Explorer(shape, TopAbs_EDGE)
n_e = 0
while exp_e.More():
    n_e += 1
    edges_seq.Append(TopoDS.Edge_s(exp_e.Current()))
    exp_e.Next()
print(f"\nAll {n_e} edges: type + points:")
for i in range(1, edges_seq.Length() + 1):
    edge = TopoDS.Edge_s(edges_seq.Value(i))
    curve = BRepAdaptor_Curve(edge)
    ct = curve.GetType()
    t1 = curve.FirstParameter()
    t2 = curve.LastParameter()
    p1 = curve.Value(t1)
    p2 = curve.Value(t2)
    degenerate = abs(float(t2 - t1)) < 1e-10
    print(f"  edge {i}: type={ct}, t=[{t1:.4f},{t2:.4f}], "
          f"({float(p1.X()):.3f},{float(p1.Y()):.3f})→({float(p2.X()):.3f},{float(p2.Y()):.3f})"
          f"{' DEGENERATE' if degenerate else ''}")

# Edge 24 is the degenerate one → it's a circle at (20,0) with area ~0 (point)
# This is an edge that collapsed to a point → filter it out!
# Count valid (non-degenerate) edges
valid = 0
for i in range(1, edges_seq.Length() + 1):
    edge = TopoDS.Edge_s(edges_seq.Value(i))
    curve = BRepAdaptor_Curve(edge)
    t1 = curve.FirstParameter()
    t2 = curve.LastParameter()
    if abs(float(t2 - t1)) > 1e-10:
        valid += 1
print(f"\nValid (non-degenerate) edges: {valid}")

# Now try ConnectEdgesToWires_s on non-degenerate edges only
valid_seq = TopTools_HSequenceOfShape()
for i in range(1, edges_seq.Length() + 1):
    edge = TopoDS.Edge_s(edges_seq.Value(i))
    curve = BRepAdaptor_Curve(edge)
    t1 = curve.FirstParameter()
    t2 = curve.LastParameter()
    if abs(float(t2 - t1)) > 1e-10:
        valid_seq.Append(edge)

wires_seq = TopTools_HSequenceOfShape()
ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(valid_seq, 1e-6, True, wires_seq)
print(f"\nAfter filtering degenerate edges: {wires_seq.Length()} wires")

for i in range(1, wires_seq.Length() + 1):
    wire = TopoDS.Wire_s(wires_seq.Value(i))
    we = TopExp_Explorer(wire, TopAbs_EDGE)
    ne = 0
    while we.More():
        ne += 1
        we.Next()
    # Check closed
    from OCP.BRepCheck import BRepCheck_Wire
    wc = BRepCheck_Wire(wire)
    closed = wc.Closed()
    print(f"  wire {i}: {ne} edges, closed check result={closed}")

os.unlink(__file__)
