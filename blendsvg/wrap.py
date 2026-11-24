"""Wrap artwork onto a curved surface like a sticker (discrete exponential map).

The surface around the anchor point is given "sticker coordinates": for each surface point,
its distance and direction from the anchor measured *along the surface* (geodesic polar
coordinates, computed with the discrete exponential map of Schmidt et al. 2006). Each point of
the artwork is then placed where those coordinates match, so lengths along the surface are
preserved like a paper sticker. On cylinders and cones this is exact; on doubly curved
surfaces (spheres) some distortion away from the anchor is unavoidable, as with a real sticker.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform, delaunay_2d_cdt


class WrapError(Exception):
    pass


@dataclass
class WrappedLayer:
    """One color's artwork triangulated in 2D and mapped onto the surface."""

    color: str
    tris: list[tuple[int, int, int]]  # CCW as seen from outside the surface
    points: list[Vector]  # surface positions, world space
    normals: list[Vector]  # outward surface normals at those points
    boundary: list[tuple[int, int]]  # outline edges, oriented with the inside on the left


# --- 2D artwork triangulation --------------------------------------------------------------


def _artwork_2d(obj: bpy.types.Object, sx: float, sy: float):
    """Top faces of a stamp slab as 2D triangles in millimetres (stamp-local x/y times scale)."""
    me = obj.data
    me.calc_loop_triangles()
    verts = [(v.co.x * sx, v.co.y * sy) for v in me.vertices]
    tris = [tuple(t.vertices) for t in me.loop_triangles if t.normal.z > 0.5]
    flip = sx * sy < 0
    if flip:
        tris = [(a, c, b) for a, b, c in tris]
    return verts, tris


def _boundary_edges(tris) -> list[tuple[int, int]]:
    count: dict[tuple[int, int], int] = {}
    directed: dict[tuple[int, int], tuple[int, int]] = {}
    for a, b, c in tris:
        for e in ((a, b), (b, c), (c, a)):
            key = (min(e), max(e))
            count[key] = count.get(key, 0) + 1
            directed[key] = e
    return [directed[k] for k, n in count.items() if n == 1]


def triangulate_fine(verts2d, tris, spacing: float):
    """Re-triangulate a 2D region with edges no longer than about ``spacing``.

    Returns (points, triangles, boundary edges). Interior grid points keep the triangles small so
    the mapped artwork follows the curved surface closely.
    """
    boundary = _boundary_edges(tris)
    pts: list[tuple[float, float]] = []
    index: dict[tuple[float, float], int] = {}

    def add(p) -> int:
        key = (round(p[0], 7), round(p[1], 7))
        if key not in index:
            index[key] = len(pts)
            pts.append(key)
        return index[key]

    edges = []
    for a, b in boundary:
        pa, pb = verts2d[a], verts2d[b]
        n = max(1, math.ceil(math.dist(pa, pb) / spacing))
        ids = [
            add((pa[0] + (pb[0] - pa[0]) * i / n, pa[1] + (pb[1] - pa[1]) * i / n))
            for i in range(n + 1)
        ]
        edges.extend(zip(ids, ids[1:], strict=False))

    # inside test against the original triangles
    inside_bvh = BVHTree.FromPolygons([Vector((x, y, 0)) for x, y in verts2d], tris)

    def inside(x: float, y: float) -> bool:
        hit = inside_bvh.ray_cast(Vector((x, y, 1)), Vector((0, 0, -1)), 2)
        return hit[0] is not None

    def touching(x: float, y: float) -> bool:
        # Hairline cracks between neighbouring colors (float rounding in the 2D booleans)
        # must count as inside, or the combined outline gets slits.
        hit = inside_bvh.find_nearest(Vector((x, y, 0)), 1e-3)
        return hit[0] is not None

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    min_gap = spacing * 0.35
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for p in pts:
        buckets.setdefault((int(p[0] // spacing), int(p[1] // spacing)), []).append(p)

    def clear_of_outline(x: float, y: float) -> bool:
        bx, by = int(x // spacing), int(y // spacing)
        for i in (bx - 1, bx, bx + 1):
            for j in (by - 1, by, by + 1):
                for ex, ey in buckets.get((i, j), ()):
                    if abs(x - ex) < min_gap and abs(y - ey) < min_gap:
                        return False
        return True

    gx = min(xs) + spacing / 2
    while gx < max(xs):
        gy = min(ys) + spacing / 2
        while gy < max(ys):
            if inside(gx, gy) and clear_of_outline(gx, gy):
                add((gx, gy))
            gy += spacing
        gx += spacing

    out_v, _, out_f, *_ = delaunay_2d_cdt(pts, edges, [], 0, 1e-9, False)
    out_pts = [(v[0], v[1]) for v in out_v]
    out_tris = []
    for f in out_f:
        a, b, c = f
        pa, pb, pc = out_pts[a], out_pts[b], out_pts[c]
        area2 = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pc[0] - pa[0]) * (pb[1] - pa[1])
        if abs(area2) < 1e-14:
            continue
        cx, cy = (pa[0] + pb[0] + pc[0]) / 3, (pa[1] + pb[1] + pc[1]) / 3
        if inside(cx, cy) or touching(cx, cy):
            out_tris.append((a, b, c) if area2 > 0 else (a, c, b))
    return out_pts, out_tris, _boundary_edges(out_tris)


_FLAT_COS = math.cos(math.radians(0.5))


_edge_len = bmesh.types.BMEdge.calc_length


def _longest_edge(face):
    return max(face.edges, key=_edge_len)


def _bisect(face, m, mids: set) -> None:
    """Cut a triangle whose longest edge was split at ``m``, Rivara style.

    ``m`` is joined to the opposite corner, then each half's other midpoint (if its edge
    was split too) is joined to ``m``, so every cut runs from a midpoint across the
    triangle and shapes stay bounded.
    """
    vs = list(face.verts)
    i = vs.index(m)
    beside = (vs[i - 1], vs[(i + 1) % len(vs)])
    c = next(v for v in vs if v not in mids and v not in beside)
    new, _ = bmesh.utils.face_split(face, m, c)
    for half in (face, new):
        if len(half.verts) > 3:
            x = next(v for v in half.verts if v in mids and v != m)
            bmesh.utils.face_split(half, x, m)


def _segment_distance(p: Vector, edge) -> float:
    a, b = edge.verts[0].co, edge.verts[1].co
    ab = b - a
    t = 0.0 if ab.length_squared == 0 else max(0.0, min(1.0, (p - a).dot(ab) / ab.length_squared))
    return (a + ab * t - p).length


# --- discrete exponential map ----------------------------------------------------------------


class SurfaceMap:
    """Sticker coordinates around an anchor on a (locally refined) copy of the model surface."""

    def __init__(
        self,
        model_mesh: bpy.types.Mesh,
        anchor: Vector,
        normal_hint: Vector,
        x_axis: Vector,
        radius: float,
        spacing: float,
    ):
        bm = bmesh.new()
        bm.from_mesh(model_mesh)
        bmesh.ops.triangulate(bm, faces=bm.faces)
        self._refine(bm, anchor, radius, spacing)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        self.bm = bm

        tree = BVHTree.FromBMesh(bm)
        loc, face_normal, face_index, _ = tree.find_nearest(anchor)
        if loc is None:
            raise WrapError("could not find the anchor point on the model")
        n0 = face_normal if face_normal.dot(normal_hint) >= 0 else -face_normal
        e1 = x_axis - n0 * x_axis.dot(n0)
        if e1.length < 1e-6:
            raise WrapError("the stamp's X axis is perpendicular to the surface")
        e1.normalize()
        self.uv, self.frames = self._exp_map(bm, bm.faces[face_index], loc, n0, e1, radius)

        # 2D lookup structure over faces whose three corners all have coordinates
        self.faces = [f for f in bm.faces if all(v.index in self.uv for v in f.verts)]
        self.uv_tree = BVHTree.FromPolygons(
            [Vector((*self.uv.get(v.index, (1e9, 1e9)), 0.0)) for v in bm.verts],
            [[v.index for v in f.verts] for f in self.faces],
        )

    @staticmethod
    def _refine(bm, anchor: Vector, radius: float, spacing: float) -> None:
        """Longest-edge bisection near the anchor until edges are at most ``spacing``.

        Splitting only each triangle's longest edge keeps triangles well shaped; splitting
        every long edge turns the long thin triangles of CAD meshes into piles of slivers.
        """
        reach = radius + 2 * spacing

        def long_edges(faces):
            out = set()
            for f in faces:
                if f.is_valid:
                    e = _longest_edge(f)
                    if _edge_len(e) > spacing and _segment_distance(anchor, e) < reach:
                        out.add(e)
            return out

        edges = long_edges(bm.faces)
        for _ in range(60):
            if not edges:
                return
            # A triangle next to a split edge must have its own longest edge split too, or
            # its cut would run from a short side and leave a sliver (Rivara's LEPP).
            longest = {}
            todo = list(edges)
            while todo:
                for f in todo.pop().link_faces:
                    if f not in longest:
                        e = longest[f] = _longest_edge(f)
                        if e not in edges:
                            edges.add(e)
                            todo.append(e)
            # bmesh.ops.subdivide_edges invalidates every vertex reference, so split edge by
            # edge with bmesh.utils, which keeps the faces recorded here valid.
            mid_of = {e: bmesh.utils.edge_split(e, e.verts[0], 0.5)[1] for e in edges}
            mids = set(mid_of.values())
            for f, e in longest.items():
                _bisect(f, mid_of[e], mids)
            changed = {f for v in mids for f in v.link_faces}
            # Bisection keeps the shape of the triangles it starts from, so the slivers of a
            # CAD mesh stay slivers; flipping edges between coplanar faces repairs them
            # without moving the surface.
            for f in changed:
                f.normal_update()
            flat = [
                e
                for e in {e for f in changed for e in f.edges}
                if len(e.link_faces) == 2
                and e.link_faces[0].normal.dot(e.link_faces[1].normal) > _FLAT_COS
            ]
            if flat:
                bmesh.ops.beautify_fill(bm, faces=list(changed), edges=flat)
            # Only faces that changed this round can still have a long edge.
            edges = long_edges({f for v in mids for f in v.link_faces})

    @staticmethod
    def _exp_map(bm, seed_face, p0: Vector, n0: Vector, e1: Vector, radius: float):
        uv: dict[int, tuple[float, float]] = {}
        frames: dict[int, tuple[Vector, Vector, Vector]] = {}  # e1, e2, normal
        dist: dict[int, float] = {}
        heap: list[tuple[float, int]] = []
        e2 = n0.cross(e1)

        for v in seed_face.verts:
            d = v.co - p0
            t = d - n0 * d.dot(n0)
            if t.length > 1e-12:
                t *= d.length / t.length
            uv[v.index] = (t.dot(e1), t.dot(e2))
            n = v.normal.copy()
            rot = n0.rotation_difference(n)
            f1 = rot @ e1
            frames[v.index] = (f1, n.cross(f1), n)
            dist[v.index] = d.length
        for v in seed_face.verts:
            for e in v.link_edges:
                w = e.other_vert(v)
                if w.index not in uv:
                    nd = dist[v.index] + e.calc_length()
                    if nd < dist.get(w.index, math.inf):
                        dist[w.index] = nd
                        heapq.heappush(heap, (nd, w.index))

        verts = bm.verts
        while heap:
            d, i = heapq.heappop(heap)
            if i in uv or d > dist.get(i, math.inf) + 1e-12:
                continue
            q = verts[i]
            total_w = 0.0
            su = sv = 0.0
            best = None
            for e in q.link_edges:
                p = e.other_vert(q)
                if p.index not in uv:
                    continue
                f1, f2, n = frames[p.index]
                vec = q.co - p.co
                t = vec - n * vec.dot(n)
                if t.length > 1e-12:
                    t *= vec.length / t.length
                w = 1.0 / max(vec.length_squared, 1e-12)
                pu, pv = uv[p.index]
                su += w * (pu + t.dot(f1))
                sv += w * (pv + t.dot(f2))
                total_w += w
                if best is None or vec.length < best[0]:
                    best = (vec.length, p.index)
            if total_w == 0.0:
                continue
            uv[i] = (su / total_w, sv / total_w)
            f1, _, n_p = frames[best[1]]
            n_q = q.normal.copy()
            g1 = n_p.rotation_difference(n_q) @ f1
            g1 -= n_q * g1.dot(n_q)
            g1.normalize()
            frames[i] = (g1, n_q.cross(g1), n_q)
            if math.hypot(*uv[i]) > radius:
                continue  # outside the sticker's reach: keep it, but don't grow past it
            for e in q.link_edges:
                w = e.other_vert(q)
                if w.index not in uv:
                    nd = d + e.calc_length()
                    if nd < dist.get(w.index, math.inf):
                        dist[w.index] = nd
                        heapq.heappush(heap, (nd, w.index))
        return uv, frames

    def locate(self, u: float, v: float) -> tuple[Vector, Vector] | None:
        """Surface point and smooth outward normal for sticker coordinates (u, v)."""
        loc, _, idx, _ = self.uv_tree.ray_cast(Vector((u, v, 1.0)), Vector((0, 0, -1)), 2.0)
        if loc is None:
            return None
        face = self.faces[idx]
        a, b, c = face.verts
        ua, ub, uc = (Vector((*self.uv[x.index], 0.0)) for x in (a, b, c))
        q = Vector((u, v, 0.0))
        point = barycentric_transform(q, ua, ub, uc, a.co, b.co, c.co)
        # barycentric weights for the normal
        wa = barycentric_transform(
            q, ua, ub, uc, Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
        )
        normal = a.normal * wa.x + b.normal * wa.y + c.normal * wa.z
        if normal.length < 1e-9:
            normal = face.normal.copy()
        return point, normal.normalized()

    def free(self) -> None:
        self.bm.free()


def map_layers(model_mesh, stamp, layers, color_key: str, bvh, s_max: float):
    """Map every stamp color layer, and their combined outline, onto the model surface.

    Returns (color layers, whole-sticker outline layer, spacing in mm).
    """
    mw = stamp.matrix_world
    sx, sy = mw.col[0].xyz.length, mw.col[1].xyz.length
    n = (mw.to_3x3() @ Vector((0, 0, 1))).normalized()
    x_axis = mw.col[0].xyz.normalized()

    arts = []
    extent = 0.0
    for obj in layers:
        local_scale = obj.matrix_local.to_scale()
        verts2d, tris = _artwork_2d(obj, sx * local_scale.x, sy * local_scale.y)
        if not tris:
            continue
        arts.append((obj[color_key], verts2d, tris))
        extent = max(extent, max(math.hypot(x, y) for x, y in verts2d))
    if not arts:
        raise WrapError("the stamp has no artwork")

    size = (
        max(
            max(abs(x) for _, vs, _ in arts for x, _ in vs),
            max(abs(y) for _, vs, _ in arts for _, y in vs),
        )
        * 2
    )
    spacing = min(max(size / 80.0, 0.25), 1.5)

    origin = mw.translation
    start = origin + n * (s_max + 1.0 - origin.dot(n))
    hit, _, _, _ = bvh.ray_cast(start, -n, s_max * 4 + 1e6)
    if hit is None:
        raise WrapError(
            "the stamp's centre does not point at the model; move the stamp in front of the "
            "surface (its arrow points away from the surface)"
        )
    surf = SurfaceMap(model_mesh, hit, n, x_axis, extent * 1.15 + 3 * spacing, spacing)
    try:
        # all colors together: the outline of the whole sticker, used to cut the base
        all_verts: list[tuple[float, float]] = []
        all_tris: list[tuple[int, int, int]] = []
        for _, verts2d, tris in arts:
            off = len(all_verts)
            all_verts.extend(verts2d)
            all_tris.extend((a + off, b + off, c + off) for a, b, c in tris)

        def mapped(color, verts2d, tris) -> WrappedLayer:
            pts2d, ftris, boundary = triangulate_fine(verts2d, tris, spacing)
            points, normals = [], []
            for u, v in pts2d:
                res = surf.locate(u, v)
                if res is None:
                    raise WrapError(
                        "part of the artwork runs off the surface it is wrapped onto (past an "
                        "open edge or too far around the model); make it smaller or move the stamp"
                    )
                points.append(res[0])
                normals.append(res[1])
            return WrappedLayer(color, ftris, points, normals, boundary)

        layers_out = [mapped(color, verts2d, tris) for color, verts2d, tris in arts]
        outline = layers_out[0] if len(arts) == 1 else mapped("all", all_verts, all_tris)
        return layers_out, outline, spacing
    finally:
        surf.free()


def build_shell(layer: WrappedLayer, lift: float, depth: float) -> bpy.types.Mesh:
    """Closed shell from ``lift`` above to ``depth`` below the surface, along the normals."""
    bm = bmesh.new()
    top = [bm.verts.new(p + nrm * lift) for p, nrm in zip(layer.points, layer.normals, strict=True)]
    bot = [
        bm.verts.new(p - nrm * depth) for p, nrm in zip(layer.points, layer.normals, strict=True)
    ]
    for a, b, c in layer.tris:
        bm.faces.new((top[a], top[b], top[c]))
        bm.faces.new((bot[c], bot[b], bot[a]))
    for a, b in layer.boundary:
        bm.faces.new((top[a], bot[a], bot[b], top[b]))
    used = {i for t in layer.tris for i in t}
    for i, (vt, vb) in enumerate(zip(top, bot, strict=True)):
        if i not in used:
            bm.verts.remove(vt)
            bm.verts.remove(vb)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("shell")
    bm.to_mesh(me)
    bm.free()
    return me
