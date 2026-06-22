# BlenderAutoStud

A Blender automation script that gives a rigged **GLB** model a Roblox-style
"stud" look, then consolidates every texture into just two maps — a
**ColorMap** and a **NormalMap** — while fully preserving animation and
skinning.

It is designed to batch-process the "Brainrot"-style character GLBs used in a
Roblox Studio pipeline (`BrainrotProto Stud-Style Pipeline v5`).

## What it does

For each input GLB, `process_glb_v5.py` runs the following pipeline:

1. **Scene cleanup** — wipes the current Blender scene and purges orphan data.
2. **Import** the source GLB and detect its built-in gradient/palette image.
3. **Mesh repair** — merge doubles, delete loose geometry, recalculate normals,
   triangulate, and set up two UV maps (`UVMap_Original`, `UVMap_New`).
4. **Color sampling & grouping** — sample the gradient color at each face and
   cluster faces into representative color groups.
5. **Island splitting** — group same-colored, 3D-connected faces into islands.
6. **Layout + UV** — pack islands into a `sqrt(N)` grid, with a 6-axis
   (box-projection) sub-grid per cell. Shared vertices keep matching UVs so no
   seams appear and vertex sharing survives export.
7. **ColorMap** — render a flat per-island color texture (1024×1024 PNG).
8. **NormalMap bake** — box-project the tileable stud normal texture
   (bundled `Stud.png`) and bake it to a `NormalMap` (1024×1024 PNG).
9. **Material rebuild** — reconstruct a single Principled BSDF using the
   ColorMap (Base Color) and NormalMap (Normal), re-attach the Armature
   modifier, strip scale F-curves, and pack the textures.
10. **Export** — export back to GLB with animations and skins preserved.

The result is a single, lightweight, two-texture GLB ready for import into
Roblox Studio.

## Requirements

- **Blender 5.0** (the script uses the Blender 5.0 slotted Action / channelbag
  API and the Cycles bake pipeline).
- The tileable **stud normal map**. A ready-to-use `Stud.png` ships with this
  repo and is used automatically (it must sit next to `process_glb_v5.py`), so
  the **`stud_it` add-on is not required**. If `Stud.png` is missing, the script
  falls back to the stud_it add-on's texture at
  `%APPDATA%\Blender Foundation\Blender\5.0\scripts\addons\stud_it\textures\Studit_nmap.png`.
  To use a different stud texture, just replace `Stud.png`.
- The source GLB must contain a built-in **gradient / palette** image (named
  something like `gradient`, `gradient pallete`, `temp_1`, etc.). The script
  uses it to derive per-face colors.

## Install as a Blender add-on (recommended)

The repository is also a Blender add-on (`__init__.py` + `process_glb_v5.py`).

1. Download/clone this repo and zip the folder (so the zip contains
   `__init__.py` and `process_glb_v5.py` at its top level).
2. In Blender: **Edit > Preferences > Add-ons > Install...**, pick the zip, and
   enable **AutoStud**.
3. Open the **N-panel** in the 3D Viewport and switch to the **AutoStud** tab.

### Preview on the open model (no files)

Use this to *see* how the studs land on a model before committing to anything:

1. Import / open a model in the scene (with its gradient palette) and select its
   mesh.
2. Adjust *Color Threshold*, *Special*, and *Stud Scale*.
3. Press **Preview on Open Model**.

The add-on copies the selected mesh, processes the copy, and switches the
viewport to **Material Preview** so you immediately see the generated flat
ColorMap **plus** the baked studs applied to the model. The processing always
runs through the material rebuild, so the preview shows the per-island ColorMap
— *not* the original gradient texture (otherwise the colors would look
scrambled). The original mesh is kept (hidden) for before/after comparison.
Tweak *Stud Scale* and press the button again to re-preview from the clean
original. This mode does not write any files.

When you are happy with the preview, set **Export To** and press **Export
Preview as GLB** — it saves the model you just previewed (ColorMap + studs +
animation baked in) straight to disk. **No Source GLB file selection is needed**;
it exports the open model directly.

### Export from a GLB file (one model at a time)

The export half of the panel deliberately splits the work into two stages so the
color result is computed **only once**:

The panel is built around reviewing one model at a time, and deliberately
splits the work into two stages so the color result is computed **only once**:

1. **Pick a Source GLB.** The recommended preset for that model (from
   `KNOWN_PARAMS`) is filled in automatically, and the output folder defaults to
   a `_studout` subfolder next to the GLB.
2. **1. Build ColorMap** — runs the color/island/UV stage once and saves
   `<model>_ColorMap.png`. After this the color parameters are **locked** so they
   can't drift. (Use **Rebuild ColorMap** to unlock and redo them.)
3. **Adjust *Stud Scale*** and press **2. Bake Studs & Export** as many times as
   you like. This re-bakes the stud NormalMap and exports the GLB while
   **reusing the saved ColorMap** — it never regenerates it, so the colors you
   approved stay frozen. Both stages always re-import the pristine source GLB,
   so re-running never corrupts the result.

## Run as a script (advanced)

The core can also be driven directly **inside Blender** (Python console,
scripting workspace, or an `execute_blender_code` integration). Make sure
`process_glb_v5.py` is on Blender's Python path (e.g. drop it next to your
`.blend` file or add its folder to `sys.path`).

The module exposes three entry points:

- `build_colormap(glb, out_dir, ...)` — stage 1 only (save the ColorMap).
- `bake_and_export(glb, out_dir, ...)` — stage 2 only (re-bake studs + export,
  reusing the existing ColorMap).
- `process_glb_v5(glb, out_dir, ...)` — one-shot: both stages in a single call.

### Process a single model

```python
import process_glb_v5 as P

P.process_glb_v5(
    "Ballerina Cappuccina.glb",
    out_dir=r"C:\path\to\01.GLB\_studout",
)
```

`process_glb_v5()` writes three files into `out_dir`:

- `<model>.glb` — the processed model
- `<model>_ColorMap.png`
- `<model>_NormalMap.png`

It returns a dict summarizing the run, e.g.:

```python
{"model": "Ballerina Cappuccina", "islands": 42, "colors": 9,
 "grid": 7, "sec": 12.3, "out": r"...\_studout\Ballerina Cappuccina.glb"}
```

### Batch-process a folder

```python
import process_glb_v5 as P

P.run_batch(
    src_dir=r"C:\path\to\01.GLB",
    out_dir=r"C:\path\to\01.GLB\_studout",
)
```

`run_batch()` processes every `*.glb` in `src_dir` (skipping backups and files
already present in `out_dir`). Pass `only=["Boss-3", "Girafa Celeste"]` to limit
the targets, or `skip_existing=False` to reprocess everything.

## Parameters

`process_glb_v5()` accepts these tuning parameters:

| Parameter            | Default | Description |
|----------------------|---------|-------------|
| `color_threshold`    | `0.08`  | Color distance for grouping faces. Higher = fewer, broader color groups. |
| `object_scale`       | `4`     | Stud tiling scale for the normal bake. Higher = smaller, denser studs. |
| `special_girafa`     | `False` | Treat each color group as one island (skip 3D-connectivity), for models like *Girafa Celeste*. |
| `prefer_light`       | `False` | Reserved flag to bias group colors toward brighter tones. |
| `enlarge_aggressive` | `False` | Expand more islands to 2×2 grid cells (uses a lower volume threshold). |
| `src_dir`            | `None`  | Source folder; defaults to the parent of `out_dir`. |

Per-model presets are defined in the `KNOWN_PARAMS` table inside the script and
applied automatically by `run_batch()`.

## License

No license has been specified yet. Add one before reusing this code in your own
projects.
