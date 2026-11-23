# blendsvg

Cut SVG artwork into the surface of a 3D model as **flush inlays** for multi-color 3D printing.
It works like a sticker that sits inside the surface instead of on top of it.

- **Flush:** each inlay's top face *is* the model's surface, so the result is perfectly smooth,
  even on curved surfaces.
- **Never cuts through:** blendsvg measures the wall under the artwork first. The pocket always
  leaves a minimum wall (default 0.8 mm), and the depth is reduced if the wall is too thin.
- **Multi-color:** each SVG fill color becomes its own inlay part. Shapes painted later hide
  the ones underneath, the same way the SVG renders.
- **Slicer-ready:** the output is a 3MF where the base and the inlays are parts of one object,
  already aligned. Assign a filament to each part in PrusaSlicer, OrcaSlicer or Bambu Studio.
  STL export is also available.

It runs in [Blender](https://www.blender.org/) (4.5 or newer) as an add-on with a sidebar panel,
or headless from the command line.

## How it works

The artwork is projected along a direction (like a projector) onto the first surface it hits.
Under that footprint, a pocket `depth` deep (measured along the projection direction) is removed
from the model, and the removed material becomes the inlays:

```
pocket = (model ∩ artwork prism) − (model shifted inward by depth)
base   = model − pocket
inlay  = pocket ∩ color prism        (one per SVG fill color)
```

Before cutting, rays are cast through ~3000 points of the artwork to find the thinnest wall
underneath. If `wall − depth < min_wall`, the depth is reduced. Use `--no-clamp` to fail
instead.

## Command line

```bash
bin/blendsvg model.stl logo.svg                      # top face, SVG's physical size
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
2. **Stamp:** optionally snap the 3D cursor to the surface first (Shift + right-drag, with
   the cursor tool's orientation set to *Surface Project*). Then click **Import SVG Stamp**.
   The stamp is created at the cursor, and its arrow points away from the surface. Move,
   rotate and scale it like any object, or use **Align to Face** / **Stamp to Cursor**.
3. **Cut:** set the depth and minimum wall, then click **Blend**. The original model is kept
   (hidden). The base and inlays go into a new `BlendSVG <model>` collection.
4. **Export:** with the parts selected, click **Export Parts** to write a 3MF or STL files.

## SVG tips

- Only **filled** shapes are used. Convert strokes and text to paths first
  (Inkscape: *Path › Stroke to Path* and *Path › Object to Path*).
- The default size is the SVG's physical size (`width="40mm"`). Use `--width`/`--height` or
  scale the stamp to change it.
- On steep or strongly curved areas the projected artwork stretches, like a projector image
  on a slanted wall.

## Development

```bash
blender -b --factory-startup -P tests/run_tests.py    # tests run inside Blender
uvx ruff check . && uvx ruff format --check .
```

Layout: `blendsvg/` is the extension package (`stamp.py` SVG → stamp, `place.py` positioning,
`blend.py` measure + cut, `export.py` 3MF/STL, `ui.py` panel and operators). `blendsvg_cli.py`
is the headless entry point and `bin/blendsvg` wraps it.
