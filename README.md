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
   (`Studit_nmap.png`) and bake it to a `NormalMap` (1024×1024 PNG).
9. **Material rebuild** — reconstruct a single Principled BSDF using the
   ColorMap (Base Color) and NormalMap (Normal), re-attach the Armature
   modifier, strip scale F-curves, and pack the textures.
10. **Export** — export back to GLB with animations and skins preserved.

The result is a single, lightweight, two-texture GLB ready for import into
Roblox Studio.

## Requirements

- **Blender 5.0** (the script uses the Blender 5.0 slotted Action / channelbag
  API and the Cycles bake pipeline).
- The **`stud_it`** add-on installed, because the script reads its bundled
  tileable normal texture:

  ```
  %APPDATA%\Blender Foundation\Blender\5.0\scripts\addons\stud_it\textures\Studit_nmap.png
  ```

  If your `stud_it` install lives elsewhere, edit the `STUDIT_NMAP` constant at
  the top of `process_glb_v5.py`.
- The source GLB must contain a built-in **gradient / palette** image (named
  something like `gradient`, `gradient pallete`, `temp_1`, etc.). The script
  uses it to derive per-face colors.

## Usage

The script is meant to be run **inside Blender** (for example via the Python
console, the scripting workspace, or an `execute_blender_code` integration).
Make sure `process_glb_v5.py` is on Blender's Python path (e.g. drop it next to
your `.blend` file or add its folder to `sys.path`).

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
