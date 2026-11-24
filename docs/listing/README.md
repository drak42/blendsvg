# Blender Extensions listing

Everything needed to submit BlendSVG to [extensions.blender.org](https://extensions.blender.org/submit/).
These files are not part of the extension zip.

| File | Use |
|---|---|
| `icon.png` | Icon, 256 × 256 |
| `featured.png` | Featured image, 1920 × 1080 |
| `screenshot-1-placed.png` | Preview: stamp placed on a mug, BlendSVG panel open |
| `screenshot-2-blended.png` | Preview: after **Blend**, base and inlays in the outliner |
| `../images/how-it-works.gif` | Preview: exploded view of the parts (if the form takes GIFs) |

The name, tagline, tags, license, copyright, website and permission come from
`blendsvg/blender_manifest.toml`, so the platform fills them in from the zip.

## Description

Paste this into the description field:

```markdown
BlendSVG cuts SVG artwork into the surface of a 3D model as **flush inlays** for
multi-color 3D printing. Each fill color becomes its own part that sits in a matching
pocket, so the print comes out perfectly smooth: no painting in the slicer, no raised
decals.

**Features**

- **Wraps like a sticker:** the artwork bends over curved surfaces (mugs, bottles,
  tubes) and keeps its true size along the surface. A Project mode is there for flat
  faces.
- **Never cuts through:** the wall under the artwork is measured first; the pocket
  always leaves a minimum wall and gets shallower on thin parts instead of breaking
  through.
- **One part per color:** shapes painted later in the SVG hide the ones underneath,
  just as the SVG renders.
- **Slicer-ready:** exports one 3MF with the base and inlays as parts of a single,
  aligned object. In OrcaSlicer each part is already on its own filament. STL export
  is included.

**How to use:** open the sidebar (N) › BlendSVG, pick the model, import an SVG as a
stamp, align it to a face, press **Blend**, then **Export Parts**.

Needs Blender 4.5 or newer. Also runs from the command line for batch work.
User guide: https://github.com/drak42/blendsvg/blob/main/docs/user-guide.md
```

## Submitting

1. Sign in at <https://extensions.blender.org/submit/> with a Blender ID, and read the
   terms of service.
2. Build the zip from a tagged release (or download it from the GitHub release):
   `blender --command extension build --source-dir blendsvg --output-dir dist`
3. Check it: `blender --command extension validate dist/blendsvg-<version>.zip`
4. Upload the zip, then add the description, icon, featured image and previews above.
5. The extension waits in the approval queue until a moderator reviews it.

Before uploading, check on the form:

- **Image sizes and formats.** The sizes above are common defaults; the platform's
  media requirements could not be confirmed ahead of time.
- **Whether GIFs are accepted** as previews; if not, leave the GIF out.
- **Maintainer.** The manifest says `drak42`; add `<email>` if you want to be
  reachable there (`maintainer = "drak42 <you@example.com>"`).
