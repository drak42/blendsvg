"""Small mesh helpers built on Blender data (work headless, no operator context needed)."""

from __future__ import annotations

import bmesh
import bpy
from mathutils import Matrix


class Scratch:
    """A temporary collection for working objects; ``cleanup`` deletes all but kept ones."""

    def __init__(self) -> None:
        self.collection = bpy.data.collections.new("blendsvg-scratch")
        bpy.context.scene.collection.children.link(self.collection)
        self._objects: list[bpy.types.Object] = []
        self._kept: set[int] = set()

    def object(self, mesh: bpy.types.Mesh, name: str = "work") -> bpy.types.Object:
        obj = bpy.data.objects.new(name, mesh)
        self.collection.objects.link(obj)
        self._objects.append(obj)
        return obj

    def keep(self, obj: bpy.types.Object) -> None:
        self._kept.add(obj.as_pointer())

    def cleanup(self) -> None:
        for obj in self._objects:
            try:
                ptr = obj.as_pointer()
            except ReferenceError:
                continue
            if ptr in self._kept:
                if self.collection in obj.users_collection:
                    self.collection.objects.unlink(obj)
                continue
            mesh = obj.data
            bpy.data.objects.remove(obj)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        bpy.data.collections.remove(self.collection)


def apply_transform(obj: bpy.types.Object) -> None:
    obj.data.transform(obj.matrix_basis)
    obj.matrix_basis = Matrix.Identity(4)


def boolean(
    target: bpy.types.Object, cutter: bpy.types.Object, operation: str, solver: str = "MANIFOLD"
) -> None:
    """Apply ``target <operation> cutter`` to target's mesh in place."""
    mod = target.modifiers.new("blendsvg", "BOOLEAN")
    mod.operation = operation
    mod.object = cutter
    mod.solver = solver
    if solver == "EXACT":
        mod.use_self = False
        mod.use_hole_tolerant = True
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    new = bpy.data.meshes.new_from_object(target.evaluated_get(dg))
    target.modifiers.remove(mod)
    old = target.data
    target.data = new
    if old.users == 0:
        bpy.data.meshes.remove(old)


def is_closed_manifold(mesh: bpy.types.Mesh) -> bool:
    bm = bmesh.new()
    bm.from_mesh(mesh)
    ok = bool(bm.edges) and all(e.is_manifold for e in bm.edges)
    bm.free()
    return ok


def clean_copy(mesh: bpy.types.Mesh, matrix: Matrix, merge: float = 1e-5) -> bpy.types.Mesh:
    """World-space copy with duplicate vertices merged (STL files store every triangle apart)."""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.transform(matrix)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=merge)
    bmesh.ops.triangulate(bm, faces=bm.faces)
    me = bpy.data.meshes.new("model")
    bm.to_mesh(me)
    bm.free()
    return me


def volume(mesh: bpy.types.Mesh) -> float:
    bm = bmesh.new()
    bm.from_mesh(mesh)
    v = abs(bm.calc_volume()) if bm.faces else 0.0
    bm.free()
    return v
