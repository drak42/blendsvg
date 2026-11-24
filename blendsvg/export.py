"""Write blend results as a multi-part 3MF or as STL files (no operator context needed)."""

from __future__ import annotations

import re
import struct
import zipfile
from collections.abc import Iterable
from pathlib import Path
from xml.sax.saxutils import quoteattr

import bpy

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="config" ContentType="application/xml"/>
</Types>
"""
_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel0"
  Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


def _triangles(
    obj: bpy.types.Object,
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    try:
        mw = obj.matrix_world
        verts = [tuple(mw @ v.co) for v in me.vertices]
        me.calc_loop_triangles()
        tris = [tuple(t.vertices) for t in me.loop_triangles]
    finally:
        ev.to_mesh_clear()
    return verts, tris


def _color_of(obj: bpy.types.Object) -> str:
    color = obj.get("blendsvg_inlay_color")
    if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        return color.upper()
    return "#B0B0B0"


def write_3mf(
    path: str | Path, objects: Iterable[bpy.types.Object], name: str = "blendsvg"
) -> Path:
    """One build object whose components are the given parts (base + inlays).

    Slicers load it as a single object with one part per mesh, already aligned. Part N
    (base first) is set to filament N in OrcaSlicer's part settings, so each color prints
    from its own filament slot without assigning them by hand.
    """
    path = Path(path)
    objects = list(objects)
    parts = []
    materials = []
    for i, obj in enumerate(objects):
        verts, tris = _triangles(obj)
        vx = "".join(f'<vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>' for x, y, z in verts)
        tx = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tris)
        parts.append(
            f'<object id="{i + 2}" type="model" name={quoteattr(obj.name)} pid="1" pindex="{i}">'
            f"<mesh><vertices>{vx}</vertices><triangles>{tx}</triangles></mesh></object>"
        )
        materials.append(f'<base name={quoteattr(obj.name)} displaycolor="{_color_of(obj)}"/>')
    group_id = len(objects) + 2
    components = "".join(f'<component objectid="{i + 2}"/>' for i in range(len(objects)))
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '<metadata name="Application">blendsvg</metadata><resources>'
        f'<basematerials id="1">{"".join(materials)}</basematerials>'
        f"{''.join(parts)}"
        f'<object id="{group_id}" type="model" name={quoteattr(name)}>'
        f"<components>{components}</components></object>"
        f'</resources><build><item objectid="{group_id}"/></build></model>'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/model_settings.config", _model_settings(group_id, name, objects))
    return path


def _model_settings(group_id: int, name: str, objects: list[bpy.types.Object]) -> str:
    """OrcaSlicer's per-part settings: part ids are the component object ids."""

    def meta(key: str, value: str) -> str:
        return f'<metadata key="{key}" value={quoteattr(value)}/>'

    parts = "".join(
        f'<part id="{i + 2}" subtype="normal_part">'
        + meta("name", obj.name)
        + meta("extruder", str(i + 1))
        + "</part>"
        for i, obj in enumerate(objects)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<config>'
        f'<object id="{group_id}">{meta("name", name)}{meta("extruder", "1")}{parts}</object>'
        "</config>\n"
    )


def write_stl(path: str | Path, obj: bpy.types.Object) -> Path:
    """Binary STL of one object in world coordinates."""
    path = Path(path)
    verts, tris = _triangles(obj)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(b"blendsvg".ljust(80, b"\0"))
        f.write(struct.pack("<I", len(tris)))
        for a, b, c in tris:
            p, q, r = verts[a], verts[b], verts[c]
            u = (q[0] - p[0], q[1] - p[1], q[2] - p[2])
            v = (r[0] - p[0], r[1] - p[1], r[2] - p[2])
            nx, ny, nz = (
                u[1] * v[2] - u[2] * v[1],
                u[2] * v[0] - u[0] * v[2],
                u[0] * v[1] - u[1] * v[0],
            )
            length = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
            f.write(struct.pack("<12fH", nx / length, ny / length, nz / length, *p, *q, *r, 0))
    return path


def safe_name(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", text).strip("_") or "part"
