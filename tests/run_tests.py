"""Run inside Blender:  blender -b --factory-startup -P tests/run_tests.py

Minimal test runner (pytest isn't available in Blender's Python). Exits non-zero on failure.
"""

from __future__ import annotations

import math
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

import bmesh
import bpy

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))

from blendsvg import meshops  # noqa: E402
from blendsvg.blend import BlendError, blend  # noqa: E402
from blendsvg.export import write_3mf, write_stl  # noqa: E402
from blendsvg.place import place_stamp  # noqa: E402
from blendsvg.stamp import import_svg_stamp  # noqa: E402

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


MODELS = {  # generated, so the repo carries no binary fixtures
    "box.stl": lambda: _box((60, 40, 10), 0),  # 60 x 40 x 10, top face at z=10
    "plate.stl": lambda: _box((60, 40, 1.2), 0),  # 1.2 mm thin plate
    "sphere.stl": lambda: bpy.ops.mesh.primitive_uv_sphere_add(
        radius=25, segments=64, ring_count=32
    ),
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
    info = import_svg_stamp(FIX / svg)
    place_stamp(info.stamp, target, place_kw.pop("normal", "top"), **place_kw)
    bpy.context.view_layer.update()
    original = vol(target)
    return target, original, blend(target, info.stamp, **kw)


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
    assert near(vol(r.base) + sum(vol(o) for _, o in r.inlays), original, 0.05)
    for _, o in r.inlays:
        assert closed(o)
        # every vertex lies on or just under the sphere surface (radius 25, faceted)
        radii = [v.co.length for v in o.data.vertices]
        assert max(radii) <= 25.0 + 1e-3, max(radii)
        assert min(radii) >= 25.0 - 0.6 - 0.15, min(radii)


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
