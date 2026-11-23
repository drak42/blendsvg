"""Position a stamp in front of a model face (used by the CLI and the "Align to face" button)."""

from __future__ import annotations

import math
from collections.abc import Sequence

import bpy
from mathutils import Matrix, Vector

FACES: dict[str, tuple[float, float, float]] = {
    "top": (0, 0, 1),
    "bottom": (0, 0, -1),
    "front": (0, -1, 0),
    "back": (0, 1, 0),
    "left": (-1, 0, 0),
    "right": (1, 0, 0),
}


def view_frame(normal: Sequence[float]) -> Matrix:
    """3x3 rotation with columns right, up, out for looking straight at a face with this normal.

    Side faces keep world Z as "up"; top/bottom faces keep world Y as "up".
    """
    w = Vector(normal)
    if w.length < 1e-9:
        raise ValueError("face normal must not be zero")
    w.normalize()
    up_hint = Vector((0, 1, 0)) if abs(w.z) > 0.9 else Vector((0, 0, 1))
    u = up_hint.cross(w).normalized()
    v = w.cross(u)
    return Matrix((u, v, w)).transposed()


def world_bounds(obj: bpy.types.Object) -> tuple[Vector, Vector]:
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def place_stamp(
    stamp: bpy.types.Object,
    target: bpy.types.Object,
    normal: Sequence[float] | str = "top",
    *,
    at: Sequence[float] | None = None,
    offset: Sequence[float] = (0.0, 0.0),
    width: float | None = None,
    height: float | None = None,
    scale: float = 1.0,
    rotate: float = 0.0,
) -> None:
    """Put the stamp just in front of ``target``'s face, looking straight at it.

    ``at`` is a world point the artwork is centred on (default: the model's bounding-box
    centre); ``offset`` shifts it right/up in mm as seen looking at the face; ``rotate`` turns
    it counter-clockwise in degrees. Size defaults to the SVG's physical size.
    """
    if isinstance(normal, str):
        normal = FACES[normal]
    rot = view_frame(normal)
    u, v, w = rot.col[0], rot.col[1], rot.col[2]

    lo, hi = world_bounds(target)
    centre = Vector(at) if at is not None else (lo + hi) / 2
    corners = [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    front = max(c.dot(w) for c in corners)
    location = centre - w * centre.dot(w) + w * (front + 1.0)
    location += u * offset[0] + v * offset[1]

    size_w, size_h = stamp.get("blendsvg_size", (1.0, 1.0))
    if width is not None:
        s = width / size_w
    elif height is not None:
        s = height / size_h
    else:
        s = 1.0
    s *= scale

    stamp.matrix_world = (
        Matrix.Translation(location)
        @ rot.to_4x4()
        @ Matrix.Rotation(math.radians(rotate), 4, "Z")
        @ Matrix.Scale(s, 4)
    )
