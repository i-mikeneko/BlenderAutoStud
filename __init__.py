# -*- coding: utf-8 -*-
"""
AutoStud - Blender add-on
=========================
A thin UI wrapper around the BrainrotProto Stud-Style pipeline (v5).

Workflow (one model at a time):
    1. Pick a source GLB  -> recommended preset is filled in automatically.
    2. Tweak the parameters if needed.
    3. "Build ColorMap"   -> runs ONCE per model, freezes the color result.
    4. "Bake Studs & Export" -> re-runnable; adjust the stud scale and re-bake
       as many times as you like. It reuses the saved ColorMap and never
       regenerates it, so the colors you approved never drift.

The heavy lifting lives in process_glb_v5.py; this file only adds the operators,
properties and panel.
"""

bl_info = {
    "name": "AutoStud",
    "author": "i-mikeneko",
    "version": (1, 0, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar (N) > AutoStud",
    "description": "Give a rigged GLB a Roblox stud look and bake it down to a "
                   "ColorMap + NormalMap, one model at a time.",
    "category": "Object",
}

import os

import bpy
from bpy.props import (StringProperty, IntProperty, FloatProperty,
                       BoolProperty, PointerProperty)

from . import process_glb_v5 as P


# ============================================================
# Properties
# ============================================================

def _on_glb_update(self, context):
    """When a new source GLB is picked: fill in its recommended preset (from
    process_glb_v5.KNOWN_PARAMS), default the output folder, and unlock the
    ColorMap stage so it can be rebuilt for the new model."""
    path = bpy.path.abspath(self.glb_path)
    if not path:
        return
    name = os.path.splitext(os.path.basename(path))[0]
    params = P.KNOWN_PARAMS.get(name, {})
    self.object_scale = params.get("object_scale", 4)
    self.color_threshold = params.get("color_threshold", 0.08)
    self.special_girafa = params.get("special_girafa", False)
    self.prefer_light = params.get("prefer_light", False)
    self.enlarge_aggressive = params.get("enlarge_aggressive", False)
    if not self.out_dir:
        self.out_dir = os.path.join(os.path.dirname(path), "_studout")
    # New model -> its ColorMap has not been built in this session yet.
    self.colormap_built = False


class AutoStudProps(bpy.types.PropertyGroup):
    glb_path: StringProperty(
        name="Source GLB",
        description="The rigged .glb to process",
        subtype='FILE_PATH',
        update=_on_glb_update,
    )
    out_dir: StringProperty(
        name="Output Folder",
        description="Where the processed GLB / ColorMap / NormalMap are written",
        subtype='DIR_PATH',
    )

    # --- Color stage parameters (locked once the ColorMap is built) ---
    color_threshold: FloatProperty(
        name="Color Threshold", default=0.08, min=0.0, max=1.0, precision=3,
        description="Color distance for grouping faces. Higher = fewer, "
                    "broader color groups",
    )
    special_girafa: BoolProperty(
        name="Special (per-color island)", default=False,
        description="Treat each color group as one island (skip 3D "
                    "connectivity). For models like Girafa Celeste",
    )
    prefer_light: BoolProperty(
        name="Prefer Light", default=False,
        description="Bias group colors toward brighter tones",
    )
    enlarge_aggressive: BoolProperty(
        name="Enlarge Aggressive", default=False,
        description="Expand more islands to 2x2 grid cells",
    )

    # --- Stud stage parameter (freely re-bakeable) ---
    object_scale: IntProperty(
        name="Stud Scale", default=4, min=1, max=32,
        description="Stud tiling scale for the normal bake. "
                    "Higher = smaller, denser studs",
    )

    # --- State ---
    colormap_built: BoolProperty(default=False)


# ============================================================
# Helpers
# ============================================================

def _resolve_paths(props):
    """Return (glb_filename, src_dir, out_dir) from the UI, or raise ValueError."""
    glb = bpy.path.abspath(props.glb_path)
    out_dir = bpy.path.abspath(props.out_dir)
    if not glb or not os.path.isfile(glb):
        raise ValueError("Pick a valid source GLB file first.")
    if not out_dir:
        raise ValueError("Set an output folder first.")
    return os.path.basename(glb), os.path.dirname(glb), out_dir


def _common_kwargs(props, src_dir):
    return dict(
        color_threshold=props.color_threshold,
        object_scale=props.object_scale,
        special_girafa=props.special_girafa,
        prefer_light=props.prefer_light,
        enlarge_aggressive=props.enlarge_aggressive,
        src_dir=src_dir,
    )


# ============================================================
# Operators
# ============================================================

class AUTOSTUD_OT_build_colormap(bpy.types.Operator):
    bl_idname = "autostud.build_colormap"
    bl_label = "Build ColorMap"
    bl_description = ("Run the color stage once and save the ColorMap PNG. "
                      "This freezes the colors; the stud bake reuses it.")
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.autostud
        try:
            glb, src_dir, out_dir = _resolve_paths(props)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        try:
            r = P.build_colormap(glb, out_dir, **_common_kwargs(props, src_dir))
        except Exception as e:
            self.report({'ERROR'}, "Build ColorMap failed: %s" % e)
            return {'CANCELLED'}
        props.colormap_built = True
        self.report({'INFO'},
                    "ColorMap built: %d islands, %d colors, grid %d (%.1fs)"
                    % (r["islands"], r["colors"], r["grid"], r["sec"]))
        return {'FINISHED'}


class AUTOSTUD_OT_bake_export(bpy.types.Operator):
    bl_idname = "autostud.bake_export"
    bl_label = "Bake Studs & Export"
    bl_description = ("Bake the stud NormalMap at the current Stud Scale and "
                      "export the GLB. Re-runnable; reuses the saved ColorMap.")
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.autostud
        try:
            glb, src_dir, out_dir = _resolve_paths(props)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        # Guard: never let an export run without a ColorMap on disk.
        if not os.path.exists(P.colormap_path(out_dir, glb)):
            self.report({'ERROR'},
                        "No ColorMap found - run 'Build ColorMap' first.")
            return {'CANCELLED'}
        try:
            r = P.bake_and_export(glb, out_dir, **_common_kwargs(props, src_dir))
        except Exception as e:
            self.report({'ERROR'}, "Bake & Export failed: %s" % e)
            return {'CANCELLED'}
        self.report({'INFO'},
                    "Exported %s (scale %d, %.1fs)"
                    % (os.path.basename(r["out"]), r["object_scale"], r["sec"]))
        return {'FINISHED'}


class AUTOSTUD_OT_unlock_colors(bpy.types.Operator):
    bl_idname = "autostud.unlock_colors"
    bl_label = "Rebuild ColorMap"
    bl_description = ("Unlock the color parameters so you can change them and "
                      "build the ColorMap again (this will overwrite it).")
    bl_options = {'REGISTER'}

    def execute(self, context):
        context.scene.autostud.colormap_built = False
        self.report({'INFO'}, "Color parameters unlocked.")
        return {'FINISHED'}


# ============================================================
# Panel
# ============================================================

class AUTOSTUD_PT_panel(bpy.types.Panel):
    bl_label = "AutoStud"
    bl_idname = "AUTOSTUD_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AutoStud"

    def draw(self, context):
        layout = self.layout
        props = context.scene.autostud
        built = props.colormap_built

        col = layout.column()
        col.prop(props, "glb_path")
        col.prop(props, "out_dir")

        # --- Color stage ---
        box = layout.box()
        box.label(text="1. ColorMap (once per model)", icon='COLOR')
        sub = box.column()
        sub.enabled = not built  # lock color params after the ColorMap is built
        sub.prop(props, "color_threshold")
        sub.prop(props, "special_girafa")
        sub.prop(props, "prefer_light")
        sub.prop(props, "enlarge_aggressive")
        if built:
            row = box.row()
            row.label(text="ColorMap built (locked)", icon='CHECKMARK')
            row.operator("autostud.unlock_colors", text="", icon='UNLOCKED')
        else:
            box.operator("autostud.build_colormap", icon='RENDER_STILL')

        # --- Stud stage ---
        box = layout.box()
        box.label(text="2. Studs + Export (re-bakeable)", icon='MOD_NOISE')
        box.prop(props, "object_scale")
        row = box.row()
        row.enabled = built
        row.operator("autostud.bake_export", icon='EXPORT')
        if not built:
            box.label(text="Build the ColorMap first.", icon='INFO')


# ============================================================
# Registration
# ============================================================

_classes = (
    AutoStudProps,
    AUTOSTUD_OT_build_colormap,
    AUTOSTUD_OT_bake_export,
    AUTOSTUD_OT_unlock_colors,
    AUTOSTUD_PT_panel,
)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.autostud = PointerProperty(type=AutoStudProps)


def unregister():
    del bpy.types.Scene.autostud
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
