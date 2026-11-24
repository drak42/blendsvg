"""Turn an SVG file into a "stamp": an empty with one thin mesh slab per fill color.

The stamp's local XY plane holds the artwork (1 unit = 1 mm at the SVG's physical size) and
its local -Z axis is the direction the artwork is projected onto a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import meshops

STAMP_KEY = "blendsvg_stamp"
COLOR_KEY = "blendsvg_color"
SLAB_HALF = 0.1  # half thickness of the preview slabs, mm (only the sign of z matters later)
SVG_TO_MM = 1000.0  # io_curve_svg imports physical sizes in metres


@dataclass
class StampInfo:
    stamp: bpy.types.Object
    colors: list[str]
    warnings: list[str] = field(default_factory=list)


def _ensure_svg_importer() -> None:
    if not hasattr(bpy.ops.import_curve, "svg"):
        import addon_utils

        addon_utils.enable("io_curve_svg", default_set=True)


def _srgb_hex(rgba) -> str:
    def enc(c: float) -> int:
        c = max(0.0, min(1.0, c))
        s = c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
        return round(s * 255)

    return "#{:02x}{:02x}{:02x}".format(*(enc(c) for c in rgba[:3]))


def color_material(hex_color: str) -> bpy.types.Material:
    name = f"BlendSVG {hex_color}"
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        rgb = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        mat.diffuse_color = (*lin, 1.0)  # Solid viewport
        # Material Preview and renders use the shader nodes (always on from Blender 5.0)
        if not mat.use_nodes:
            mat.use_nodes = True
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (*lin, 1.0)
    return mat


def _curve_to_slab(curve: bpy.types.Object, half: float) -> bpy.types.Mesh | None:
    """Filled 2D curve -> closed slab mesh spanning z in [-half, half], in millimetres."""
    dg = bpy.context.evaluated_depsgraph_get()
    flat = bpy.data.meshes.new_from_object(curve.evaluated_get(dg))
    flat.transform(Matrix.Scale(SVG_TO_MM, 4) @ curve.matrix_world)
    bm = bmesh.new()
    bm.from_mesh(flat)
    bpy.data.meshes.remove(flat)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    if not bm.faces:
        bm.free()
        return None
    for v in bm.verts:
        v.co.z = -half
    for f in bm.faces:
        if f.normal.z < 0:
            f.normal_flip()
    ext = bmesh.ops.extrude_face_region(bm, geom=list(bm.faces))
    top = [g for g in ext["geom"] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=top, vec=Vector((0, 0, 2 * half)))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("slab")
    bm.to_mesh(me)
    bm.free()
    return me


def import_svg_stamp(filepath: str | Path, *, merge_colors: bool = False) -> StampInfo:
    """Import an SVG as a stamp object linked to the active collection."""
    _ensure_svg_importer()
    filepath = str(filepath)
    before = set(bpy.data.objects)
    before_mats = set(bpy.data.materials)
    before_cols = set(bpy.data.collections)
    result = bpy.ops.import_curve.svg(filepath=filepath)
    if "FINISHED" not in result:
        raise RuntimeError(f"Blender could not import {filepath}")
    curves = [o for o in bpy.data.objects if o not in before and o.type == "CURVE"]
    # The importer adds curves in document (paint) order; names carry that order too.
    curves.sort(key=lambda o: (len(o.name), o.name))

    warnings: list[str] = []
    scratch = meshops.Scratch()
    shapes: list[tuple[str, bpy.types.Object]] = []
    strokes = 0
    try:
        for curve in curves:
            mat = curve.active_material
            if mat is None or curve.data.dimensions != "2D":
                strokes += 1
                continue
            color = "#000000" if merge_colors else _srgb_hex(mat.diffuse_color)
            me = _curve_to_slab(curve, SLAB_HALF)
            if me is not None:
                shapes.append((color, scratch.object(me)))
    finally:
        for o in curves:
            bpy.data.objects.remove(o)
        for o in set(bpy.data.objects) - before:
            if o.type == "CURVE":
                bpy.data.objects.remove(o)
        for m in set(bpy.data.materials) - before_mats:
            if m.users == 0:
                bpy.data.materials.remove(m)
        for c in set(bpy.data.collections) - before_cols - {scratch.collection}:
            bpy.data.collections.remove(c)
        for cu in [c for c in bpy.data.curves if c.users == 0]:
            bpy.data.curves.remove(cu)

    if strokes:
        warnings.append(
            f"{strokes} stroke-only path(s) ignored; convert strokes to filled paths "
            "(Inkscape: Path > Stroke to Path) to include them."
        )
    if not shapes:
        scratch.cleanup()
        raise RuntimeError(f"{filepath}: no filled shapes found")

    # Painter's order: each shape hides what was painted before it in other colors.
    layers: dict[str, bpy.types.Object] = {}
    for color, obj in shapes:
        cutter = scratch.object(obj.data.copy())
        cutter.scale.z = 2.0  # thicker cutter avoids coplanar caps
        meshops.apply_transform(cutter)
        for other, layer in layers.items():
            if other != color:
                meshops.boolean(layer, cutter, "DIFFERENCE")
        if color in layers:
            meshops.boolean(layers[color], obj, "UNION")
        else:
            layers[color] = obj
            scratch.keep(obj)

    layers = {c: o for c, o in layers.items() if len(o.data.polygons)}
    scratch.cleanup()

    # Centre the artwork on the stamp origin.
    lo = Vector(
        (
            min(v.co.x for o in layers.values() for v in o.data.vertices),
            min(v.co.y for o in layers.values() for v in o.data.vertices),
            0,
        )
    )
    hi = Vector(
        (
            max(v.co.x for o in layers.values() for v in o.data.vertices),
            max(v.co.y for o in layers.values() for v in o.data.vertices),
            0,
        )
    )
    centre = (lo + hi) / 2

    name = Path(filepath).stem
    stamp = bpy.data.objects.new(f"Stamp {name}", None)
    stamp.empty_display_type = "SINGLE_ARROW"
    stamp.empty_display_size = max((hi - lo).x, (hi - lo).y) / 2
    stamp[STAMP_KEY] = True
    stamp["blendsvg_size"] = [(hi - lo).x, (hi - lo).y]
    stamp["blendsvg_source"] = filepath
    bpy.context.collection.objects.link(stamp)

    for color, obj in layers.items():
        obj.data.transform(Matrix.Translation(-centre))
        obj.data.name = obj.name = f"{name} {color}"
        obj.data.materials.clear()
        obj.data.materials.append(color_material(color))
        obj[COLOR_KEY] = color
        for coll in list(obj.users_collection):
            coll.objects.unlink(obj)
        bpy.context.collection.objects.link(obj)
        obj.parent = stamp
        obj.hide_select = True  # select/transform the stamp empty, not its slabs

    return StampInfo(stamp, list(layers), warnings)


def find_stamp(obj: bpy.types.Object | None) -> bpy.types.Object | None:
    while obj is not None:
        if obj.get(STAMP_KEY):
            return obj
        obj = obj.parent
    return None


def stamp_layers(stamp: bpy.types.Object) -> list[bpy.types.Object]:
    return [c for c in stamp.children if c.get(COLOR_KEY) and c.type == "MESH"]
