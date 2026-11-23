"""Operators and the sidebar panel (View3D > Sidebar > BlendSVG)."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from .blend import PART_KEY, BlendError, blend
from .export import safe_name, write_3mf, write_stl
from .place import FACES, place_stamp
from .stamp import find_stamp, import_svg_stamp


def _is_target(_self, obj: bpy.types.Object) -> bool:
    return obj.type == "MESH" and not obj.get(PART_KEY) and find_stamp(obj) is None


class BlendSVGSettings(bpy.types.PropertyGroup):
    target: PointerProperty(
        name="Model",
        type=bpy.types.Object,
        poll=_is_target,
        description="Mesh to cut the artwork into",
    )
    depth: FloatProperty(
        name="Depth (mm)",
        default=0.6,
        min=0.05,
        soft_max=5.0,
        precision=2,
        description="How deep the inlay goes below the surface",
    )
    min_wall: FloatProperty(
        name="Min wall (mm)",
        default=0.8,
        min=0.0,
        soft_max=5.0,
        precision=2,
        description="Material always kept under the inlay, so it never cuts through",
    )
    clamp_depth: BoolProperty(
        name="Reduce depth on thin walls",
        default=True,
        description="Make the pocket shallower instead of failing when the wall is too thin",
    )
    mode: EnumProperty(
        name="Mode",
        default="wrap",
        items=[
            (
                "wrap",
                "Wrap",
                "Wrap the artwork over curves like a paper sticker, keeping its "
                "size along the surface; depth follows the surface normal",
            ),
            (
                "project",
                "Project",
                "Project the artwork straight along the stamp's arrow, like a "
                "projector; stretches on slopes",
            ),
        ],
    )
    face: EnumProperty(
        name="Face",
        default="top",
        items=[(k, k.title(), f"Look at the model's {k} face") for k in FACES],
    )
    merge_colors: BoolProperty(
        name="Single color",
        default=False,
        description="Import all SVG colors as one inlay",
    )


def _settings(context) -> BlendSVGSettings:
    return context.scene.blendsvg


def _active_stamp(context) -> bpy.types.Object | None:
    stamp = find_stamp(context.active_object)
    if stamp is None:
        stamps = {find_stamp(o) for o in context.selected_objects} - {None}
        stamp = next(iter(stamps)) if len(stamps) == 1 else None
    return stamp


def _select_only(context, obj: bpy.types.Object) -> None:
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


class BLENDSVG_OT_import_stamp(bpy.types.Operator, ImportHelper):
    """Import an SVG as a stamp at the 3D cursor (snap the cursor to a surface first)"""

    bl_idname = "blendsvg.import_stamp"
    bl_label = "Import SVG Stamp"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".svg"
    filter_glob: StringProperty(default="*.svg", options={"HIDDEN"})

    def execute(self, context):
        s = _settings(context)
        try:
            info = import_svg_stamp(self.filepath, merge_colors=s.merge_colors)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        info.stamp.matrix_world = context.scene.cursor.matrix
        if s.target is None and context.active_object and _is_target(None, context.active_object):
            s.target = context.active_object
        _select_only(context, info.stamp)
        for w in info.warnings:
            self.report({"WARNING"}, w)
        self.report(
            {"INFO"}, f"Stamp with {len(info.colors)} color(s); arrow points away from the surface"
        )
        return {"FINISHED"}


class BLENDSVG_OT_stamp_to_cursor(bpy.types.Operator):
    """Move the stamp to the 3D cursor and align it with the cursor's rotation"""

    bl_idname = "blendsvg.stamp_to_cursor"
    bl_label = "Stamp to Cursor"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_stamp(context) is not None

    def execute(self, context):
        stamp = _active_stamp(context)
        scale = stamp.matrix_world.to_scale()
        stamp.matrix_world = context.scene.cursor.matrix
        stamp.scale = scale
        return {"FINISHED"}


class BLENDSVG_OT_stamp_to_face(bpy.types.Operator):
    """Centre the stamp on a face of the model, looking straight at it"""

    bl_idname = "blendsvg.stamp_to_face"
    bl_label = "Align to Face"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_stamp(context) is not None and _settings(context).target is not None

    def execute(self, context):
        s = _settings(context)
        stamp = _active_stamp(context)
        size = stamp.matrix_world.to_scale()
        place_stamp(stamp, s.target, s.face, scale=size.x)
        return {"FINISHED"}


class BLENDSVG_OT_blend(bpy.types.Operator):
    """Cut the stamp's artwork into the model as flush inlays (the original is kept, hidden)"""

    bl_idname = "blendsvg.blend"
    bl_label = "Blend"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_stamp(context) is not None and _settings(context).target is not None

    def execute(self, context):
        s = _settings(context)
        stamp = _active_stamp(context)
        try:
            r = blend(
                s.target,
                stamp,
                depth=s.depth,
                min_wall=s.min_wall,
                clamp_depth=s.clamp_depth,
                mode=s.mode,
            )
        except BlendError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        for w in r.warnings:
            self.report({"WARNING"}, w)
        for o in context.selected_objects:
            o.select_set(False)
        for o in r.parts:
            o.select_set(True)
        context.view_layer.objects.active = r.base
        self.report(
            {"INFO"},
            f"Depth {r.depth:.2f} mm, {len(r.inlays)} inlay(s); "
            f"thinnest wall {r.min_thickness:.2f} mm",
        )
        return {"FINISHED"}


def _parts_for_export(context) -> list[bpy.types.Object]:
    parts = [o for o in context.selected_objects if o.get(PART_KEY)]
    if not parts and context.active_object is not None:
        for coll in context.active_object.users_collection:
            parts = [o for o in coll.objects if o.get(PART_KEY)]
            if parts:
                break
    parts.sort(key=lambda o: (o[PART_KEY] != "base", o.name))
    return parts


class BLENDSVG_OT_export(bpy.types.Operator, ExportHelper):
    """Export the selected base and inlays as one multi-part 3MF, or as STL files"""

    bl_idname = "blendsvg.export"
    bl_label = "Export Parts"

    filename_ext = ".3mf"
    filter_glob: StringProperty(default="*.3mf;*.stl", options={"HIDDEN"})
    file_format: EnumProperty(
        name="Format",
        items=[
            ("3MF", "3MF (one object, parts)", "Slicers load it ready for filament assignment"),
            ("STL", "STL files", "One STL per part, named after the file"),
        ],
    )

    @classmethod
    def poll(cls, context):
        return bool(_parts_for_export(context))

    def check(self, context):
        self.filename_ext = ".3mf" if self.file_format == "3MF" else ".stl"
        return super().check(context)

    def execute(self, context):
        parts = _parts_for_export(context)
        path = Path(self.filepath)
        if self.file_format == "3MF":
            write_3mf(path, parts, name=path.stem)
            self.report({"INFO"}, f"Wrote {path.name} with {len(parts)} parts")
        else:
            for o in parts:
                write_stl(path.with_name(f"{path.stem}-{safe_name(o.name)}.stl"), o)
            self.report({"INFO"}, f"Wrote {len(parts)} STL files to {path.parent}")
        return {"FINISHED"}


class BLENDSVG_PT_panel(bpy.types.Panel):
    bl_label = "BlendSVG"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlendSVG"

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        layout.prop(s, "target")

        box = layout.box()
        box.label(text="1. Stamp", icon="IMAGE_DATA")
        row = box.row(align=True)
        row.operator(BLENDSVG_OT_import_stamp.bl_idname, icon="IMPORT")
        row.prop(s, "merge_colors", text="", icon="COLOR")
        row = box.row(align=True)
        row.prop(s, "face", text="")
        row.operator(BLENDSVG_OT_stamp_to_face.bl_idname)
        box.operator(BLENDSVG_OT_stamp_to_cursor.bl_idname, icon="PIVOT_CURSOR")
        stamp = _active_stamp(context)
        if stamp is not None:
            box.label(text=stamp.name, icon="CHECKMARK")
        else:
            box.label(text="Select a stamp to place it", icon="INFO")

        box = layout.box()
        box.label(text="2. Cut", icon="MOD_BOOLEAN")
        box.row().prop(s, "mode", expand=True)
        box.prop(s, "depth")
        box.prop(s, "min_wall")
        box.prop(s, "clamp_depth")
        box.operator(BLENDSVG_OT_blend.bl_idname, icon="CHECKMARK")

        box = layout.box()
        box.label(text="3. Export", icon="EXPORT")
        box.operator(BLENDSVG_OT_export.bl_idname, icon="FILE_3D")


CLASSES = (
    BlendSVGSettings,
    BLENDSVG_OT_import_stamp,
    BLENDSVG_OT_stamp_to_cursor,
    BLENDSVG_OT_stamp_to_face,
    BLENDSVG_OT_blend,
    BLENDSVG_OT_export,
    BLENDSVG_PT_panel,
)


def menu_import(self, _context):
    self.layout.operator(BLENDSVG_OT_import_stamp.bl_idname, text="SVG as BlendSVG Stamp (.svg)")


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.blendsvg = PointerProperty(type=BlendSVGSettings)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)


def unregister() -> None:
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    del bpy.types.Scene.blendsvg
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
