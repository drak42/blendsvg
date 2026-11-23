"""Cut a stamp's artwork into a model surface as flush, fixed-depth inlays."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from . import meshops
from .stamp import COLOR_KEY, color_material, stamp_layers

PART_KEY = "blendsvg_part"


class BlendError(Exception):
    """The design cannot be blended with the requested settings."""


@dataclass
class BlendResult:
    base: bpy.types.Object
    inlays: list[tuple[str, bpy.types.Object]]
    depth: float
    min_thickness: float
    collection: bpy.types.Collection
    warnings: list[str] = field(default_factory=list)

    @property
    def parts(self) -> list[bpy.types.Object]:
        return [self.base, *(o for _, o in self.inlays)]


def _sample_points(layers: list[bpy.types.Object], target: int, rng: random.Random) -> list[Vector]:
    """World-space points spread over the artwork (top faces of the stamp slabs)."""
    tris: list[tuple[Vector, Vector, Vector, float]] = []
    for obj in layers:
        me = obj.data
        me.calc_loop_triangles()
        mw = obj.matrix_world
        for lt in me.loop_triangles:
            if lt.normal.z < 0.5:
                continue
            a, b, c = (mw @ me.vertices[i].co for i in lt.vertices)
            area = (b - a).cross(c - a).length / 2
            if area > 1e-12:
                tris.append((a, b, c, area))
    if not tris:
        return []
    total = sum(t[3] for t in tris)
    pts: list[Vector] = []
    detailed = len(tris) <= 20000
    for a, b, c, area in tris:
        centre = (a + b + c) / 3
        pts.append(centre)
        if detailed:  # inset corners catch thin strokes and sharp tips
            pts.extend(v.lerp(centre, 0.15) for v in (a, b, c))
        for _ in range(round(area / total * target)):
            r1, r2 = rng.random(), rng.random()
            if r1 + r2 > 1:
                r1, r2 = 1 - r1, 1 - r2
            pts.append(a + (b - a) * r1 + (c - a) * r2)
    return pts


def blend(
    target: bpy.types.Object,
    stamp: bpy.types.Object,
    *,
    depth: float = 0.6,
    min_wall: float = 0.8,
    clamp_depth: bool = True,
    samples: int = 3000,
    hide_target: bool = True,
) -> BlendResult:
    """Project ``stamp`` along its local -Z onto ``target`` and cut it in as inlays.

    Under the first surface the artwork lands on, a pocket ``depth`` deep (along the projection
    direction) is removed from a copy of the model. Each SVG fill color becomes one inlay part
    that fills its share of the pocket. The inlays' top faces are the model's own surface, so
    they are perfectly flush, and base + inlays reproduce the original model.

    The wall under the artwork is measured first. The pocket always leaves at least
    ``min_wall`` of material, so it never cuts through to the other side of the model.
    """
    if depth <= 0:
        raise BlendError("depth must be positive")
    if target.type != "MESH":
        raise BlendError(f"{target.name} is not a mesh")
    layers = stamp_layers(stamp)
    if not layers:
        raise BlendError(f"{stamp.name} has no artwork")
    warnings: list[str] = []

    n = stamp.matrix_world.to_3x3() @ Vector((0, 0, 1))
    if n.length < 1e-9:
        raise BlendError("the stamp is scaled to zero along its Z axis")
    n.normalize()

    dg = bpy.context.evaluated_depsgraph_get()
    eval_target = target.evaluated_get(dg)
    model_mesh = meshops.clean_copy(eval_target.to_mesh(), target.matrix_world)
    eval_target.to_mesh_clear()
    if not model_mesh.polygons:
        raise BlendError(f"{target.name} has no faces")

    solver = "MANIFOLD"
    if not meshops.is_closed_manifold(model_mesh):
        solver = "EXACT"
        warnings.append(
            f"{target.name} is not watertight; using the slower exact solver. "
            "Repair the mesh if the result has holes."
        )

    # --- measure the surface and the wall under the artwork -----------------------------
    verts = [v.co.copy() for v in model_mesh.vertices]
    bvh = BVHTree.FromPolygons(verts, [p.vertices[:] for p in model_mesh.polygons])
    s_vals = [v.dot(n) for v in verts]
    s_max, s_min = max(s_vals), min(s_vals)
    reach = s_max - s_min + 2.0
    ray = -n

    points = _sample_points(layers, samples, random.Random(0))
    tops: list[float] = []
    thick: list[float] = []
    misses = open_hits = 0
    for q in points:
        start = q + n * (s_max + 1.0 - q.dot(n))
        loc, _, _, _ = bvh.ray_cast(start, ray, reach)
        if loc is None:
            misses += 1
            continue
        s0 = loc.dot(n)
        exit_s = None
        probe = loc
        for _ in range(8):  # skip coincident/duplicate faces at the entry point
            probe = probe + ray * 1e-4
            loc2, _, _, _ = bvh.ray_cast(probe, ray, reach)
            if loc2 is None:
                break
            if s0 - loc2.dot(n) > 1e-4:
                exit_s = loc2.dot(n)
                break
            probe = loc2
        tops.append(s0)
        if exit_s is None:
            open_hits += 1
        else:
            thick.append(s0 - exit_s)

    if not tops:
        raise BlendError(
            "the artwork does not land on the model; move the stamp in front of the surface "
            "(its arrow points away from the surface)."
        )
    if misses:
        warnings.append(
            f"{100 * misses / len(points):.0f}% of the artwork hangs off the edge of the surface;"
            " that part is dropped."
        )
    if open_hits:
        warnings.append("some rays found no back wall (open mesh?); wall check is incomplete.")

    min_thickness = min(thick) if thick else float("inf")
    max_depth = min_thickness - min_wall
    if depth > max_depth:
        if not clamp_depth or max_depth < 0.1:
            raise BlendError(
                f"the wall under the artwork is only {min_thickness:.2f} mm thick; a {depth:g} mm "
                f"pocket would leave less than the {min_wall:g} mm minimum wall. "
                "Use a smaller depth or minimum wall, or move the stamp."
            )
        warnings.append(
            f"wall under the artwork is only {min_thickness:.2f} mm thick; depth reduced from "
            f"{depth:g} mm to {max_depth:.2f} mm to keep a {min_wall:g} mm wall."
        )
        depth = max_depth

    # --- build projection prisms (one per color) ----------------------------------------
    s_lo = min(tops) - depth  # no pocket point can be deeper; a margin leaves slivers
    s_hi = min(max(tops) + max(depth, 0.5), s_max + 0.01)
    scratch = meshops.Scratch()
    try:
        prisms: list[tuple[str, bpy.types.Object]] = []
        for obj in layers:
            me = obj.data.copy()
            mw = obj.matrix_world
            for v in me.vertices:
                w = mw @ v.co
                level = s_hi if v.co.z > 0 else s_lo
                v.co = w - n * w.dot(n) + n * level
            prisms.append((obj[COLOR_KEY], scratch.object(me, "prism")))

        def copy_of(o: bpy.types.Object) -> bpy.types.Object:
            return scratch.object(o.data.copy())

        model = scratch.object(model_mesh, "model")
        if len(prisms) == 1:
            prism_all = prisms[0][1]
        else:
            prism_all = copy_of(prisms[0][1])
            for _, p in prisms[1:]:
                meshops.boolean(prism_all, p, "UNION")

        # pocket = model ∩ prism, minus everything deeper than `depth` below the surface
        pocket = copy_of(model)
        meshops.boolean(pocket, prism_all, "INTERSECT", solver)
        shifted = copy_of(model)
        shifted.data.transform(Matrix.Translation(-n * depth))
        meshops.boolean(pocket, shifted, "DIFFERENCE", solver)
        if meshops.volume(pocket.data) < 1e-6:
            raise BlendError("the artwork does not overlap the model surface")

        base = copy_of(model)
        meshops.boolean(base, pocket, "DIFFERENCE", solver)

        inlays: list[tuple[str, bpy.types.Object]] = []
        for color, p in prisms:
            part = copy_of(pocket)
            if len(prisms) > 1:
                meshops.boolean(part, p, "INTERSECT")
            if meshops.volume(part.data) > 1e-6:
                inlays.append((color, part))

        # --- publish results ------------------------------------------------------------
        out = bpy.data.collections.new(f"BlendSVG {target.name}")
        bpy.context.scene.collection.children.link(out)

        def publish(obj: bpy.types.Object, name: str, part: str) -> None:
            scratch.keep(obj)
            obj.name = obj.data.name = name
            obj[PART_KEY] = part
            out.objects.link(obj)

        publish(base, f"{target.name} base", "base")
        base.data.materials.clear()
        for m in target.data.materials:
            base.data.materials.append(m)
        for color, part in inlays:
            publish(part, f"{target.name} inlay {color}", "inlay")
            part["blendsvg_inlay_color"] = color
            part.data.materials.clear()
            part.data.materials.append(color_material(color))
    finally:
        scratch.cleanup()

    if hide_target:
        target.hide_set(True)
        stamp.hide_set(True)
        for obj in layers:
            obj.hide_set(True)

    return BlendResult(base, inlays, depth, min_thickness, out, warnings)
