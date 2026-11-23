"""Headless blendsvg: blender -b --factory-startup -P blendsvg_cli.py -- MODEL SVG [options]

Prefer the bin/blendsvg wrapper, which runs this for you.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blendsvg.blend import BlendError, blend  # noqa: E402
from blendsvg.export import safe_name, write_3mf, write_stl  # noqa: E402
from blendsvg.place import FACES, place_stamp  # noqa: E402
from blendsvg.stamp import import_svg_stamp  # noqa: E402

IMPORTERS = {
    ".stl": lambda p: bpy.ops.wm.stl_import(filepath=p),
    ".obj": lambda p: bpy.ops.wm.obj_import(filepath=p),
    ".ply": lambda p: bpy.ops.wm.ply_import(filepath=p),
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="blendsvg",
        description="Cut SVG artwork into a model's surface as flush multi-color inlays.",
    )
    ap.add_argument("model", type=Path, help="STL, OBJ or PLY model (millimetres)")
    ap.add_argument("svg", type=Path, help="SVG artwork; each fill color becomes one inlay part")
    ap.add_argument(
        "-o", "--output", type=Path, help="output .3mf (default: MODEL-blend.3mf next to the model)"
    )
    ap.add_argument(
        "--stl", action="store_true", help="also write MODEL-base.stl and MODEL-inlay-<color>.stl"
    )
    ap.add_argument(
        "--save-blend",
        type=Path,
        metavar="FILE.blend",
        help="also save the Blender scene, to inspect or tweak in the add-on",
    )

    g = ap.add_argument_group("placement")
    g.add_argument(
        "--face", choices=sorted(FACES), default="top", help="model face to decorate (default: top)"
    )
    g.add_argument(
        "--normal",
        type=float,
        nargs=3,
        metavar=("X", "Y", "Z"),
        help="custom outward face direction instead of --face",
    )
    g.add_argument(
        "--at",
        type=float,
        nargs=3,
        metavar=("X", "Y", "Z"),
        help="world point to centre the artwork on (default: model centre)",
    )
    g.add_argument(
        "--offset",
        type=float,
        nargs=2,
        default=(0.0, 0.0),
        metavar=("RIGHT", "UP"),
        help="shift in mm as seen looking at the face",
    )
    size = g.add_mutually_exclusive_group()
    size.add_argument("--width", type=float, help="artwork width in mm (keeps aspect ratio)")
    size.add_argument("--height", type=float, help="artwork height in mm (keeps aspect ratio)")
    g.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="extra scale factor (default size: the SVG's physical size)",
    )
    g.add_argument("--rotate", type=float, default=0.0, help="counter-clockwise degrees")

    c = ap.add_argument_group("cut")
    c.add_argument(
        "--wrap",
        action="store_true",
        help="wrap the artwork over curves like a sticker (keeps its size along the surface) "
        "instead of projecting it straight on",
    )
    c.add_argument("--depth", type=float, default=0.6, help="inlay depth in mm (default 0.6)")
    c.add_argument(
        "--min-wall",
        type=float,
        default=0.8,
        help="material to keep under the inlay, mm (default 0.8)",
    )
    c.add_argument(
        "--no-clamp",
        action="store_true",
        help="fail instead of reducing the depth when the wall is too thin",
    )
    c.add_argument(
        "--single-color", action="store_true", help="merge all SVG colors into one inlay"
    )
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    for f in (args.model, args.svg):
        if not f.is_file():
            print(f"blendsvg: error: {f} not found", file=sys.stderr)
            return 2
    importer = IMPORTERS.get(args.model.suffix.lower())
    if importer is None:
        print(f"blendsvg: error: unsupported model type {args.model.suffix}", file=sys.stderr)
        return 2

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    before = set(bpy.data.objects)
    importer(str(args.model.resolve()))
    meshes = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    if not meshes:
        print(f"blendsvg: error: no mesh found in {args.model}", file=sys.stderr)
        return 1
    target = meshes[0]
    if len(meshes) > 1:
        print(f"blendsvg: warning: {args.model} has {len(meshes)} meshes; using {target.name}")

    try:
        info = import_svg_stamp(args.svg.resolve(), merge_colors=args.single_color)
        place_stamp(
            info.stamp,
            target,
            args.normal or args.face,
            at=args.at,
            offset=args.offset,
            width=args.width,
            height=args.height,
            scale=args.scale,
            rotate=args.rotate,
        )
        bpy.context.view_layer.update()
        result = blend(
            target,
            info.stamp,
            depth=args.depth,
            min_wall=args.min_wall,
            clamp_depth=not args.no_clamp,
            mode="wrap" if args.wrap else "project",
        )
    except (BlendError, RuntimeError) as e:
        print(f"blendsvg: error: {e}", file=sys.stderr)
        return 1

    for w in info.warnings + result.warnings:
        print(f"blendsvg: warning: {w}")

    stem = args.model.with_suffix("")
    out = args.output or stem.with_name(f"{stem.name}-blend.3mf")
    write_3mf(out, result.parts, name=args.model.stem)
    written = [out]
    if args.stl:
        written.append(write_stl(stem.with_name(f"{stem.name}-base.stl"), result.base))
        for color, part in result.inlays:
            name = f"{stem.name}-inlay-{safe_name(color.lstrip('#'))}.stl"
            written.append(write_stl(stem.with_name(name), part))
    if args.save_blend:
        bpy.ops.wm.save_as_mainfile(filepath=str(args.save_blend.resolve()))
        written.append(args.save_blend)

    print(
        f"blendsvg: depth {result.depth:.2f} mm, thinnest wall under artwork "
        f"{result.min_thickness:.2f} mm, {len(result.inlays)} inlay color(s): "
        + ", ".join(c for c, _ in result.inlays)
    )
    for p in written:
        print(f"blendsvg: wrote {p}")
    return 0


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    code = main(argv)
    sys.stdout.flush()
    sys.exit(code)
