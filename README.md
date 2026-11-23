# blendsvg

Cut SVG artwork into the surface of a 3D model as **flush inlays** for multi-color 3D printing.
It works like a sticker that sits inside the surface instead of on top of it.

- **Wraps like a sticker:** the artwork bends over curved surfaces and keeps its size along the
  surface, like a paper sticker on a bottle. You can also project it straight on.
- **Flush:** each inlay's top face *is* the model's surface, so the result is perfectly smooth,
  even on curved surfaces.
- **Never cuts through:** blendsvg measures the wall under the artwork first. The pocket always
  leaves a minimum wall (default 0.8 mm), and the depth is reduced if the wall is too thin.
- **Multi-color:** each SVG fill color becomes its own inlay part. Shapes painted later hide
  the ones underneath, the same way the SVG renders.
- **Slicer-ready:** the output is a 3MF where the base and the inlays are parts of one object,
  already aligned. Assign a filament to each part in PrusaSlicer, OrcaSlicer or Bambu Studio.
  STL export is also available.

**New here? Start with the [user guide](docs/user-guide.md).**

It runs in [Blender](https://www.blender.org/) (4.5 or newer) as an add-on with a sidebar panel,
or headless from the command line.

## How it works

There are two modes:

- **Wrap** (the default): works like a paper sticker.
  The surface around the point the stamp aims at gets "sticker coordinates", which are the
  distance and direction from that point measured *along the surface* (a discrete
  exponential map). Each point of the artwork is placed where its coordinates match. On
  cylinders and cones (bottles, mugs, tubes) this is exact. On doubly curved surfaces
  (spheres) there is some distortion away from the centre, just as a real sticker would
  wrinkle. The artwork becomes a curved shell reaching `depth` below the surface along the
  local normal, and `inlay = model ∩ shell`.
- **Project** (`--project`, or the switch in the panel): the artwork is projected straight along the
  stamp's arrow, like a projector. It stretches on slopes and cannot reach around a curve,
  and the depth is measured along the projection direction.

In both modes the inlay's top is the model's own surface, so it is flush, and base + inlays
reproduce the original model. Before cutting, rays are cast through ~3000 points of the
artwork to find the thinnest wall underneath. If `wall − depth < min_wall`, the depth is
reduced. Use `--no-clamp` to fail instead.

## Command line

```bash
bin/blendsvg model.stl logo.svg                      # top face, SVG's physical size
bin/blendsvg bottle.stl label.svg --face front --width 60    # wraps around the bottle
bin/blendsvg box.stl logo.svg --project                         # straight projection
bin/blendsvg model.stl logo.svg --face front --width 30 --offset 0 5 --rotate 15
bin/blendsvg model.stl logo.svg --depth 1.0 --min-wall 1.2 --stl -o out/model.3mf
bin/blendsvg --help
```

Writes `model-blend.3mf` next to the model by default. `--stl` also writes `model-base.stl` and
`model-inlay-<color>.stl`. `--save-blend scene.blend` saves the Blender scene so you can adjust
it in the add-on. Set `BLENDER=/path/to/blender` to use a specific Blender build.

Models can be STL, OBJ or PLY in millimetres. `--face` is one of
`top bottom front back left right`, or pass any direction with `--normal X Y Z`.

## Blender add-on

Install: `blender --command extension build --source-dir blendsvg --output-dir dist`, then
in Blender go to *Edit › Preferences › Get Extensions › Install from Disk…* and pick
`dist/blendsvg-0.1.0.zip`. To develop, symlink the `blendsvg/` folder into your extensions
`user_default` directory instead.

Then open *3D View › Sidebar (N) › BlendSVG*:

1. **Model:** pick the mesh to decorate.
2. **Stamp:** optionally snap the 3D cursor to the surface first (Shift + right-drag,
   with the Cursor tool's *Surface Project* on and *Orientation* set to *Geometry*). Then
   click **Import SVG Stamp**.
   The stamp is created at the cursor, and its arrow points away from the surface. Move,
   rotate and scale it like any object, or use **Align to Face** / **Stamp to Cursor**.
3. **Cut:** choose **Wrap** (sticker) or **Project**, set the depth and minimum wall, then
   click **Blend**. In Wrap mode the sticker is centred where the stamp's arrow hits the
   surface, and the stamp's X axis sets the direction of the artwork. The original model is kept
   (hidden). The base and inlays go into a new `BlendSVG <model>` collection.
4. **Export:** with the parts selected, click **Export Parts** to write a 3MF or STL files.

## SVG tips

- Only **filled** shapes are used. Convert strokes and text to paths first
  (Inkscape: *Path › Stroke to Path* and *Path › Object to Path*).
- The default size is the SVG's physical size (`width="40mm"`). Use `--width`/`--height` or
  scale the stamp to change it.
- In Project mode the artwork stretches on steep areas, like a projector image on a slanted
  wall. Use Wrap mode for curved surfaces.
- In Wrap mode the sticker can't run past an open edge of the mesh, or so far around a model
  that it overlaps itself.

## Development

```bash
blender -b --factory-startup -P tests/run_tests.py    # tests run inside Blender
uvx ruff check . && uvx ruff format --check .
```

Layout: `blendsvg/` is the extension package (`stamp.py` SVG → stamp, `place.py` positioning,
`blend.py` measure + cut, `wrap.py` sticker mapping, `export.py` 3MF/STL, `ui.py` panel and operators). `blendsvg_cli.py`
is the headless entry point and `bin/blendsvg` wraps it.
