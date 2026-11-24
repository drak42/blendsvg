"""Run inside Blender:  blender -b --factory-startup -P tests/run_tests.py

Minimal test runner (pytest isn't available in Blender's Python). Exits non-zero on failure.
"""

from __future__ import annotations

import math
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))

from blendsvg import meshops  # noqa: E402
from blendsvg.blend import BlendError, _wall_at, _wall_once, blend  # noqa: E402
from blendsvg.export import write_3mf, write_stl  # noqa: E402
from blendsvg.place import place_stamp  # noqa: E402
from blendsvg.stamp import import_svg_stamp  # noqa: E402
from blendsvg.wrap import SurfaceMap  # noqa: E402

# badge.svg: 40x20 rounded rect (r=3) blue, white 10x10 square with a 4x4 hole, red r=6 circle
RECT = 40 * 20 - (4 - math.pi) * 9
WHITE = 100 - 16
RED = math.pi * 36
BLUE = RECT - WHITE - RED  # the white square's hole shows blue again (part of RECT - 100 + 16)


def reset() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _box(size, z0):
    bpy.ops.mesh.primitive_cube_add(size=1)
    o = bpy.context.object
    o.scale = size
    o.location = (0, 0, z0 + size[2] / 2)


def _fillet_box():
    _box((80, 50, 30), 0)
    o = bpy.context.object
    bpy.ops.object.transform_apply(scale=True)
    m = o.modifiers.new("fillet", "BEVEL")
    m.width, m.segments, m.limit_method = 3, 6, "NONE"


MODELS = {  # generated, so the repo carries no binary fixtures
    "box.stl": lambda: _box((60, 40, 10), 0),  # 60 x 40 x 10, top face at z=10
    "plate.stl": lambda: _box((60, 40, 1.2), 0),  # 1.2 mm thin plate
    "sphere.stl": lambda: bpy.ops.mesh.primitive_uv_sphere_add(
        radius=25, segments=64, ring_count=32
    ),
    "cylinder.stl": lambda: bpy.ops.mesh.primitive_cylinder_add(
        radius=20, depth=60, vertices=128
    ),  # axis Z, centred at the origin
    "mug.stl": lambda: bpy.ops.mesh.primitive_cylinder_add(
        radius=40, depth=95, vertices=256, location=(0, 0, 47.5)
    ),
    "fillet_box.stl": _fillet_box,  # 80 x 50 x 30 with 3 mm CAD-style fillets (sliver faces)
}


def load(name: str) -> bpy.types.Object:
    """Build the model, round-trip it through STL like a real user file, and import it."""
    MODELS[name]()
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / name)
        bpy.ops.wm.stl_export(filepath=path)
        reset()
        bpy.ops.wm.stl_import(filepath=path)
    return bpy.context.selected_objects[0]


def vol(obj: bpy.types.Object) -> float:
    return meshops.volume(obj.data)


def closed(obj: bpy.types.Object) -> bool:
    return meshops.is_closed_manifold(obj.data)


def near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def run(model: str, svg: str = "badge.svg", **kw):
    reset()
    target = load(model)
    place_kw = {
        k: kw.pop(k) for k in list(kw) if k in ("normal", "offset", "width", "rotate", "at")
    }
    t0 = time.perf_counter()
    info = import_svg_stamp(FIX / svg)
    place_stamp(info.stamp, target, place_kw.pop("normal", "top"), **place_kw)
    bpy.context.view_layer.update()
    original = vol(target)
    result = blend(target, info.stamp, **kw)
    print(f"  {model} {kw.get('mode', 'project')}: {time.perf_counter() - t0:.2f}s")
    return target, original, result


def test_box_three_colors_flush_and_conserved():
    _, original, r = run("box.stl")
    assert near(original, 60 * 40 * 10, 1e-3)
    assert r.depth == 0.6 and not r.warnings, r.warnings
    colors = dict(r.inlays)
    assert set(colors) == {"#1e88e5", "#ffffff", "#e53935"}, colors
    total = vol(r.base) + sum(vol(o) for o in colors.values())
    assert near(total, original, 1e-2), (total, original)
    for color, area in (("#1e88e5", BLUE), ("#ffffff", WHITE), ("#e53935", RED)):
        assert near(vol(colors[color]), area * 0.6, area * 0.6 * 0.01), (color, vol(colors[color]))
    for o in r.parts:
        assert closed(o), f"{o.name} is not watertight"
    for o in colors.values():
        zs = [v.co.z for v in o.data.vertices]
        assert near(max(zs), 10.0, 1e-4) and near(min(zs), 9.4, 1e-4), (o.name, min(zs), max(zs))


def test_thin_plate_depth_clamped():
    _, original, r = run("plate.stl", width=30)
    assert near(r.min_thickness, 1.2, 1e-4)
    assert near(r.depth, 0.4, 1e-4), r.depth
    assert any("depth reduced" in w for w in r.warnings)
    bottom = min(v.co.z for _, o in r.inlays for v in o.data.vertices)
    assert near(bottom, 0.8, 1e-4), bottom  # 0.8 mm floor kept, nothing cut through
    assert near(vol(r.base) + sum(vol(o) for _, o in r.inlays), original, 1e-2)


def test_thin_plate_no_clamp_fails():
    try:
        run("plate.stl", width=30, clamp_depth=False)
    except BlendError as e:
        assert "1.20 mm thick" in str(e)
    else:
        raise AssertionError("expected BlendError")


def test_sphere_curved_surface_flush():
    _, original, r = run("sphere.stl", normal="front", width=20)
    inlaid = sum(vol(o) for _, o in r.inlays)
    total = vol(r.base) + inlaid
    # Boolean rounding on the faceted sphere: Blender 4.5 is off by ~0.07% of the inlay volume
    # (sub-micron over the artwork area); a missing or doubled sliver would be far larger.
    assert near(total, original, 0.002 * inlaid), (total, original, inlaid)
    for _, o in r.inlays:
        assert closed(o)
        # every vertex lies on or just under the sphere surface (radius 25, faceted)
        radii = [v.co.length for v in o.data.vertices]
        assert max(radii) <= 25.0 + 1e-3, max(radii)
        assert min(radii) >= 25.0 - 0.6 - 0.15, min(radii)
    skin = [
        f
        for f in r.base.data.polygons
        if f.center.length > 25.0 - 0.02
        and f.center.y < -20
        and abs(f.center.x) < 5
        and abs(f.center.z) < 2
    ]
    assert not skin, f"{len(skin)} base faces cover the inlays"


def test_side_face_and_rotation():
    _, original, r = run("box.stl", normal="right", width=30, rotate=90)
    xs = [v.co.x for _, o in r.inlays for v in o.data.vertices]
    assert near(max(xs), 30.0, 1e-4) and near(min(xs), 29.4, 1e-4)
    # rotated 90°: the 30 mm-wide art now runs 30 mm along Z... clipped by the 10 mm tall box
    assert any("hangs off" in w for w in r.warnings), r.warnings
    pocket = original - vol(r.base)
    # where art is clipped by the face edges, float rounding loses < 0.1% (sub-micron gaps)
    assert near(sum(vol(o) for _, o in r.inlays), pocket, pocket * 1e-3)


def test_missing_the_model_fails():
    try:
        run("box.stl", offset=(200, 0))
    except BlendError as e:
        assert "does not land" in str(e)
    else:
        raise AssertionError("expected BlendError")


def _art_area():
    return {"#1e88e5": BLUE, "#ffffff": WHITE, "#e53935": RED}


def test_wrap_cylinder_like_a_sticker():
    _, original, r = run("cylinder.stl", normal="front", width=40, mode="wrap")
    assert r.depth == 0.6, (r.depth, r.warnings)
    angles, radii = [], []
    for _, o in r.inlays:
        assert closed(o), o.name
        for v in o.data.vertices:
            radii.append(math.hypot(v.co.x, v.co.y))
            angles.append(math.atan2(v.co.x, -v.co.y))  # 0 at the front (-Y)
    # flush: nothing above the surface; floor 0.6 mm below it (faceting sag ~0.01 mm)
    assert max(radii) <= 20.0 + 1e-3, max(radii)
    assert min(radii) >= 20.0 - 0.6 - 0.03, min(radii)
    # wrapped: 40 mm of sticker covers 40 / 20 = 2 radians of arc, centred on the front
    span = max(angles) - min(angles)
    assert near(span, 2.0, 0.03), span
    assert near(max(angles), -min(angles), 0.02)
    # area preserved: shell volume = area * depth * (1 - depth / 2R)
    for color, area in _art_area().items():
        expect = area * 0.6 * (1 - 0.6 / 40)
        got = vol(dict(r.inlays)[color])
        assert near(got, expect, expect * 0.02), (color, got, expect)
    assert near(vol(r.base) + sum(vol(o) for _, o in r.inlays), original, original * 1e-4)
    assert closed(r.base)
    # no skin left on the base over the inlays (the base must be open where the sticker is)
    skin = [
        f
        for f in r.base.data.polygons
        if math.hypot(f.center.x, f.center.y) > 20.0 - 0.01
        and abs(f.center.z) < 6
        and abs(math.atan2(f.center.x, -f.center.y)) < 0.8
    ]
    assert not skin, f"{len(skin)} base faces cover the inlays"


def test_wrap_flat_matches_project():
    _, _, flat = run("box.stl", mode="wrap")
    for color, area in _art_area().items():
        got = vol(dict(flat.inlays)[color])
        assert near(got, area * 0.6, area * 0.6 * 0.01), (color, got)
    zs = [v.co.z for _, o in flat.inlays for v in o.data.vertices]
    assert near(max(zs), 10.0, 1e-4) and near(min(zs), 9.4, 1e-4), (min(zs), max(zs))


def test_wrap_sphere():
    _, original, r = run("sphere.stl", normal="front", width=20, mode="wrap")
    for _, o in r.inlays:
        assert closed(o), o.name
        radii = [v.co.length for v in o.data.vertices]
        assert max(radii) <= 25.0 + 1e-3, max(radii)
        assert min(radii) >= 25.0 - 0.6 - 0.15, min(radii)
    assert near(vol(r.base) + sum(vol(o) for _, o in r.inlays), original, original * 1e-4)
    for color, area in _art_area().items():  # 20 mm on r=25: a few % distortion at most
        scale = (20 / 40) ** 2
        got = vol(dict(r.inlays)[color])
        assert near(got, area * scale * 0.6, area * scale * 0.6 * 0.06), (color, got)


def test_wrap_thin_wall_clamped():
    _, _, r = run("plate.stl", width=30, mode="wrap")
    assert near(r.depth, 0.4, 1e-3), r.depth
    bottom = min(v.co.z for _, o in r.inlays for v in o.data.vertices)
    assert near(bottom, 0.8, 1e-3), bottom


def test_wall_ray_through_edges():
    # On a 256-sided mug an edge faces an edge; this ray (captured from a real run) slipped
    # between the faces and reported "no back wall (open mesh?)" on a closed model.
    reset()
    target = load("mug.stl")
    me = meshops.clean_copy(target.data, target.matrix_world)
    bvh = BVHTree.FromPolygons(
        [v.co.copy() for v in me.vertices], [p.vertices[:] for p in me.polygons]
    )
    start = Vector((-1.1751156989703304e-06, -40.04999542236328, 35.00263977050781))
    ray = Vector((1.133813682940854e-07, 1.0, -8.896118112051163e-16))
    assert _wall_once(bvh, start, ray, 300)[1] is None, "the edge ray no longer misses; update"
    _, t = _wall_at(bvh, start, ray, 300)
    assert t is not None and near(t, 80.0, 0.01), t


def test_open_mesh_warns():
    reset()
    target = load("box.stl")
    bm = bmesh.new()
    bm.from_mesh(target.data)
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.normal.z < -0.5], context="FACES")
    bm.to_mesh(target.data)
    bm.free()
    info = import_svg_stamp(FIX / "badge.svg")
    place_stamp(info.stamp, target, "top")
    bpy.context.view_layer.update()
    r = blend(target, info.stamp, mode="wrap")
    assert any("back wall" in w for w in r.warnings), r.warnings


def test_parts_survive_stl_round_trip():
    # Booleans leave sub-micron edges; STL import (Blender's, and slicers') welds and drops
    # them, which used to open holes in parts that were closed in Blender.
    _, original, r = run("fillet_box.stl", normal="front", width=60, mode="wrap")
    assert near(sum(vol(o) for o in r.parts), original, original * 1e-4)
    with tempfile.TemporaryDirectory() as d:
        for o in r.parts:
            assert closed(o), o.name
            path = write_stl(Path(d) / "part.stl", o)
            bpy.ops.wm.stl_import(filepath=str(path))
            back = bpy.context.selected_objects[0]
            assert closed(back), f"{o.name} is open after an STL round trip"
            bpy.data.objects.remove(back)


def test_wrap_refine_cad_slivers():
    # A CAD cylinder's sides are 60 x 1 mm slivers. Refinement must finish, reach the target
    # spacing, and not pile up slivers: this once hung, then built 406k faces in 10 s.
    reset()
    target = load("cylinder.stl")
    bm = bmesh.new()
    bm.from_mesh(target.data)
    bmesh.ops.triangulate(bm, faces=bm.faces)
    anchor, spacing, radius = Vector((0, -20, 0)), 0.5, 26.0
    t0 = time.perf_counter()
    SurfaceMap._refine(bm, anchor, radius, spacing)
    print(f"  refine: {len(bm.faces)} faces, {time.perf_counter() - t0:.2f}s")
    try:
        assert all(len(f.verts) == 3 for f in bm.faces)
        assert not [f for f in bm.faces if f.calc_area() < 1e-9], "zero-area faces"
        near_edges = [
            e for e in bm.edges if ((e.verts[0].co + e.verts[1].co) / 2 - anchor).length < radius
        ]
        longest = max(e.calc_length() for e in near_edges)
        assert longest <= spacing + 1e-6, longest
        tiny = sum(1 for e in near_edges if e.calc_length() < spacing / 16)
        assert tiny < len(near_edges) * 0.01, f"{tiny} near-zero edges (slivers)"
        assert len(bm.faces) < 150_000, len(bm.faces)  # ~93k; the sliver bug made 406k
    finally:
        bm.free()


def test_exports():
    _, _, r = run("box.stl")
    with tempfile.TemporaryDirectory() as d:
        p = write_3mf(Path(d) / "x.3mf", r.parts)
        with zipfile.ZipFile(p) as z:
            model = z.read("3D/3dmodel.model").decode()
        assert model.count("<component ") == 4 and 'displaycolor="#E53935"' in model
        s = write_stl(Path(d) / "x.stl", r.base)
        tris = len([t for t in bmesh_tris(r.base)])
        assert s.stat().st_size == 84 + 50 * tris


def bmesh_tris(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    out = bm.calc_loop_triangles()
    bm.free()
    return out


def main() -> int:
    tests = [(k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.exit(code)
