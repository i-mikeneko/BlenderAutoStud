# -*- coding: utf-8 -*-
"""
BrainrotProto Stud-Style Pipeline v5
=========================================
Conforms to the spec: BrainrotProto_StudStyle_Pipeline_v5.md

Adds a Roblox "stud" look to a rigged GLB and consolidates all textures into
just two maps: a ColorMap and a NormalMap. Animation and skinning are preserved.

Usage (via Blender's execute_blender_code):
    import process_glb_v5 as P
    P.process_glb_v5("Ballerina Cappuccina.glb",
                     out_dir=r"...\\01.GLB\\_studout")

Batch:
    P.run_batch(src_dir, out_dir)
"""

import os
import math
import time
import shutil
import traceback

import bpy
import bmesh
from mathutils import Vector

# ============================================================
# Constants / environment
# ============================================================

# Stud tile material (the path written in the spec is wrong; use the real path).
STUDIT_NMAP = os.path.join(
    os.environ.get("APPDATA", ""),
    r"Blender Foundation\Blender\5.0\scripts\addons\stud_it\textures\Studit_nmap.png",
)

IMG_SIZE = 1024
BAKE_SAMPLES = 32
BAKE_MARGIN = 16


# ============================================================
# [1] Scene cleanup
# ============================================================

def full_scene_clean():
    """Remove all objects, armatures, actions, meshes, materials and images,
    then run orphans_purge three times to free memory."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.armatures, bpy.data.materials,
                 bpy.data.images, bpy.data.actions):
        for blk in list(coll):
            try:
                coll.remove(blk)
            except Exception:
                pass
    for _ in range(3):
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True,
                                       do_recursive=True)


# ============================================================
# Rename the gradient image
# ============================================================

def rename_gradient_image():
    """Rename the built-in palette image to 'Gradient' and return it.
    Absorbs naming variants such as 'gradient', 'gradient pallete',
    'temp_1', 'temp_2', etc."""
    candidates = []
    for img in bpy.data.images:
        if img.name in ("Render Result", "Viewer Node"):
            continue
        low = img.name.lower()
        score = 0
        if "gradient" in low or "pallete" in low or "palette" in low:
            score = 3
        elif low.startswith("temp_") or low.startswith("temp"):
            score = 2
        else:
            score = 1  # other textures (model name, etc.) are the last resort
        candidates.append((score, img))
    if not candidates:
        return None
    # Highest score first; ties resolved by first occurrence.
    candidates.sort(key=lambda t: -t[0])
    grad = candidates[0][1]
    grad.name = "Gradient"
    return grad


# ============================================================
# [2] Mesh repair
# ============================================================

def repair_mesh(obj):
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
    # delete loose (isolated vertices / edges)
    loose_v = [v for v in bm.verts if not v.link_faces]
    if loose_v:
        bmesh.ops.delete(bm, geom=loose_v, context='VERTS')
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bmesh.ops.triangulate(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()
    me.update()

    # UV maps: existing -> UVMap_Original, new UVMap_New (active_render)
    uvs = me.uv_layers
    if len(uvs) > 0:
        uvs[0].name = "UVMap_Original"
    else:
        uvs.new(name="UVMap_Original")
    new_uv = uvs.new(name="UVMap_New")
    for uv in uvs:
        uv.active_render = (uv.name == "UVMap_New")
    uvs.active = new_uv


# ============================================================
# [3] Color sampling & grouping
# ============================================================

def _sample_gradient_colors(obj, grad_img):
    """Sample the Gradient image color at each face's UVMap_Original centroid."""
    me = obj.data
    px = grad_img.pixels[:]  # RGBA flat
    w, h = grad_img.size
    uv_layer = me.uv_layers.get("UVMap_Original")
    if uv_layer is None:
        uv_layer = me.uv_layers[0]

    def sample(u, v):
        x = min(max(int(u * (w - 1)), 0), w - 1)
        y = min(max(int(v * (h - 1)), 0), h - 1)
        idx = (y * w + x) * 4
        return (px[idx], px[idx + 1], px[idx + 2])

    face_cols = []
    me.calc_loop_triangles()
    uvdata = uv_layer.data
    for poly in me.polygons:
        us = vs = 0.0
        cnt = 0
        for li in poly.loop_indices:
            uv = uvdata[li].uv
            us += uv[0]; vs += uv[1]; cnt += 1
        face_cols.append(sample(us / cnt, vs / cnt))
    return face_cols


def _group_colors(face_cols, threshold):
    """Build representative color groups using distance `threshold`."""
    groups = []  # list of [r,g,b]
    face_group = [0] * len(face_cols)
    for fi, c in enumerate(face_cols):
        best = -1
        for gi, gc in enumerate(groups):
            d = math.sqrt(sum((a - b) ** 2 for a, b in zip(c, gc)))
            if d < threshold:
                best = gi
                break
        if best < 0:
            groups.append(list(c))
            best = len(groups) - 1
        face_group[fi] = best
    return groups, face_group


# ============================================================
# [4] Island splitting
# ============================================================

def _build_islands(obj, face_group, groups, special_girafa, prefer_light):
    me = obj.data
    n = len(me.polygons)

    if special_girafa:
        # Treat each same-color group as a single island.
        islands = []
        for gi in range(len(groups)):
            faces = [fi for fi in range(n) if face_group[fi] == gi]
            if faces:
                islands.append({"faces": faces, "gi": gi})
    else:
        # Same color AND 3D-connected (shared edge) form a single island.
        # Build adjacency.
        edge_faces = {}
        for poly in me.polygons:
            for ek in poly.edge_keys:
                edge_faces.setdefault(ek, []).append(poly.index)
        adj = [[] for _ in range(n)]
        for ek, fl in edge_faces.items():
            for a in fl:
                for b in fl:
                    if a != b:
                        adj[a].append(b)
        visited = [False] * n
        islands = []
        for start in range(n):
            if visited[start]:
                continue
            gi = face_group[start]
            stack = [start]
            faces = []
            visited[start] = True
            while stack:
                f = stack.pop()
                faces.append(f)
                for nb in adj[f]:
                    if not visited[nb] and face_group[nb] == gi:
                        visited[nb] = True
                        stack.append(nb)
            islands.append({"faces": faces, "gi": gi})

    # Determine each island's color and volume.
    for isl in islands:
        gi = isl["gi"]
        isl["color"] = tuple(groups[gi])
        # bbox volume
        vs = []
        for fi in isl["faces"]:
            for vi in me.polygons[fi].vertices:
                vs.append(me.vertices[vi].co)
        if vs:
            xs = [v.x for v in vs]; ys = [v.y for v in vs]; zs = [v.z for v in vs]
            dx = max(xs) - min(xs) + 0.001
            dy = max(ys) - min(ys) + 0.001
            dz = max(zs) - min(zs) + 0.001
            isl["volume"] = dx * dy * dz
        else:
            isl["volume"] = 0.001
    return islands


# ============================================================
# [5] sqrt(N) grid placement + [6] 6-axis sub-grid UV
# ============================================================

def _layout_and_uv(obj, islands, enlarge_aggressive):
    me = obj.data
    N = len(islands)
    GRID = max(1, math.ceil(math.sqrt(N)))

    # Sort by volume (largest first).
    order = sorted(range(N), key=lambda i: -islands[i]["volume"])

    # Cell assignment (large islands expand to 2x2).
    occupied = [[False] * GRID for _ in range(GRID)]
    placements = {}  # island_idx -> (col,row,span)

    vols = sorted([islands[i]["volume"] for i in range(N)])
    if vols:
        if enlarge_aggressive:
            thr = vols[max(0, int(len(vols) * 0.25))]
        else:
            thr = vols[-1] * 0.5 if vols else 0

    def find_cell(span):
        for r in range(GRID - span + 1):
            for c in range(GRID - span + 1):
                if all(not occupied[r + dr][c + dc]
                       for dr in range(span) for dc in range(span)):
                    return c, r
        return None

    for isl_i in order:
        want_span = 2 if (islands[isl_i]["volume"] >= thr and GRID >= 2) else 1
        cell = find_cell(want_span) if want_span == 2 else None
        if cell is None:
            cell = find_cell(1)
            want_span = 1
        if cell is None:
            # Grid overflow: cannot expand, force into (0,0) (theoretically rare).
            cell = (0, 0)
        c, r = cell
        for dr in range(want_span):
            for dc in range(want_span):
                occupied[r + dr][c + dc] = True
        placements[isl_i] = (c, r, want_span)

    # Write UVs (UVMap_New).
    uv_layer = me.uv_layers.get("UVMap_New")
    me.uv_layers.active = uv_layer
    uvdata = uv_layer.data

    cell_w = 1.0 / GRID

    for isl_i, isl in enumerate(islands):
        c, r, span = placements[isl_i]
        cell_x0 = c * cell_w
        cell_y0 = r * cell_w
        cw = cell_w * span
        # Sub-grid 3x2 = 6 directions
        sub_w = cw / 3.0
        sub_h = cw / 2.0

        # Precompute the bbox per sub_cell (shared normalization across all
        # vertices of faces facing the same direction).
        # -> shared vertices on adjacent faces get matching UVs -> no UV seam
        #    -> vertex sharing is preserved on export.
        # +X->0, -X->1, +Y->2, -Y->3, +Z->4, -Z->5
        # Projection axes: +/-X -> YZ, +/-Y -> XZ, +/-Z -> XY
        sub_bboxes = {}  # sub_idx -> [pa, pb, amin, amax, bmin, bmax]
        for fi in isl["faces"]:
            poly = me.polygons[fi]
            nrm = poly.normal
            ax = max(range(3), key=lambda k: abs(nrm[k]))
            sign = 1 if nrm[ax] >= 0 else -1
            sub = ax * 2 + (0 if sign > 0 else 1)
            if ax == 0:
                pa, pb = 1, 2
            elif ax == 1:
                pa, pb = 0, 2
            else:
                pa, pb = 0, 1
            for vi in poly.vertices:
                co = me.vertices[vi].co
                if sub not in sub_bboxes:
                    sub_bboxes[sub] = [pa, pb, co[pa], co[pa], co[pb], co[pb]]
                else:
                    b = sub_bboxes[sub]
                    if co[pa] < b[2]: b[2] = co[pa]
                    if co[pa] > b[3]: b[3] = co[pa]
                    if co[pb] < b[4]: b[4] = co[pb]
                    if co[pb] > b[5]: b[5] = co[pb]

        # Write each face's UV (normalized over the whole sub_cell bbox).
        for fi in isl["faces"]:
            poly = me.polygons[fi]
            nrm = poly.normal
            ax = max(range(3), key=lambda k: abs(nrm[k]))
            sign = 1 if nrm[ax] >= 0 else -1
            sub = ax * 2 + (0 if sign > 0 else 1)
            sub_col = sub % 3
            sub_row = sub // 3
            sx0 = cell_x0 + sub_col * sub_w
            sy0 = cell_y0 + sub_row * sub_h

            pa, pb, amin, amax, bmin, bmax = sub_bboxes[sub]
            da = (amax - amin) or 1e-6
            db = (bmax - bmin) or 1e-6
            for li, vi in zip(poly.loop_indices, poly.vertices):
                co = me.vertices[vi].co
                un = (co[pa] - amin) / da
                vn = (co[pb] - bmin) / db
                uvdata[li].uv = (sx0 + un * sub_w, sy0 + vn * sub_h)

    return GRID, placements, cell_w


# ============================================================
# [7] ColorMap generation
# ============================================================

def _make_colormap(obj, islands, placements, GRID, model_name, out_dir):
    """v2: float cell boundary calculation + average color fill for unused cells
    to eliminate black residual pixels at cell edges."""
    img = bpy.data.images.new(model_name + "_ColorMap", IMG_SIZE, IMG_SIZE, alpha=True)
    img.colorspace_settings.name = 'sRGB'
    # Fill background with average island color (prevents black pixel artifacts)
    if islands:
        avg = [
            sum(i["color"][0] for i in islands) / len(islands),
            sum(i["color"][1] for i in islands) / len(islands),
            sum(i["color"][2] for i in islands) / len(islands),
        ]
    else:
        avg = [0.5, 0.5, 0.5]
    px = [avg[0], avg[1], avg[2], 1.0] * (IMG_SIZE * IMG_SIZE)
    cell_pxf = IMG_SIZE / GRID  # float for precise boundaries

    def fill_cell(c, r, span, col):
        x0 = int(c * cell_pxf)
        y0 = int(r * cell_pxf)
        x1 = int((c + span) * cell_pxf)
        y1 = int((r + span) * cell_pxf)
        if (c + span) == GRID: x1 = IMG_SIZE
        if (r + span) == GRID: y1 = IMG_SIZE
        for y in range(y0, y1):
            for x in range(x0, x1):
                i = (y * IMG_SIZE + x) * 4
                px[i] = col[0]; px[i+1] = col[1]; px[i+2] = col[2]; px[i+3] = 1.0

    for isl_i, isl in enumerate(islands):
        c, r, span = placements[isl_i]
        fill_cell(c, r, span, isl["color"])

    img.pixels.foreach_set(px)
    img.update()
    img.filepath_raw = os.path.join(out_dir, model_name + "_ColorMap.png")
    img.file_format = 'PNG'
    img.save()
    return img


# ============================================================
# [8] Stud NormalMap bake
# ============================================================

def _bake_normalmap(obj, model_name, out_dir, object_scale):
    if not os.path.exists(STUDIT_NMAP):
        raise FileNotFoundError("Studit_nmap.png not found: " + STUDIT_NMAP)

    stud_img = bpy.data.images.load(STUDIT_NMAP, check_existing=True)
    stud_img.colorspace_settings.name = 'Non-Color'

    # Temporary material (overrides every slot).
    tmp_mat = bpy.data.materials.new(model_name + "_BAKE")
    tmp_mat.use_nodes = True
    nt = tmp_mat.node_tree
    nt.nodes.clear()
    texcoord = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.inputs['Scale'].default_value = (object_scale, object_scale, object_scale)
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = stud_img
    tex.extension = 'REPEAT'
    tex.projection = 'BOX'
    tex.projection_blend = 0.2
    emit = nt.nodes.new("ShaderNodeEmission")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(texcoord.outputs['Object'], mapping.inputs['Vector'])
    nt.links.new(mapping.outputs['Vector'], tex.inputs['Vector'])
    nt.links.new(tex.outputs['Color'], emit.inputs['Color'])
    nt.links.new(emit.outputs['Emission'], out.inputs['Surface'])

    # Bake target image node.
    bake_img = bpy.data.images.new(model_name + "_NormalMap", IMG_SIZE, IMG_SIZE)
    bake_img.colorspace_settings.name = 'Non-Color'
    target_node = nt.nodes.new("ShaderNodeTexImage")
    target_node.image = bake_img
    nt.nodes.active = target_node
    target_node.select = True

    # Override every slot with this material.
    orig_mats = [s.material for s in obj.material_slots]
    if not obj.material_slots:
        obj.data.materials.append(tmp_mat)
    else:
        for s in obj.material_slots:
            s.material = tmp_mat

    # Make UVMap_New both active and active_render.
    me = obj.data
    for uv in me.uv_layers:
        uv.active_render = (uv.name == "UVMap_New")
    me.uv_layers.active = me.uv_layers.get("UVMap_New")

    # Bake.
    scn = bpy.context.scene
    scn.render.engine = 'CYCLES'
    scn.cycles.samples = BAKE_SAMPLES
    obj.hide_set(False)
    obj.hide_viewport = False
    obj.hide_render = False
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.bake(type='EMIT', margin=BAKE_MARGIN, use_clear=False)

    bake_img.filepath_raw = os.path.join(out_dir, model_name + "_NormalMap.png")
    bake_img.file_format = 'PNG'
    bake_img.save()

    return bake_img, orig_mats, tmp_mat


# ============================================================
# [9] Rebuild the Principled BSDF
# ============================================================

def _rebuild_material(obj, model_name, color_img, normal_img,
                      orig_mats, tmp_mat, special_girafa):
    # Restore the original materials (Girafa: slot 0 only).
    if special_girafa and orig_mats:
        for i, s in enumerate(obj.material_slots):
            s.material = orig_mats[0] if i == 0 else (orig_mats[i] if i < len(orig_mats) else orig_mats[0])
    else:
        for i, s in enumerate(obj.material_slots):
            if i < len(orig_mats) and orig_mats[i] is not None:
                s.material = orig_mats[i]

    # Set slot 0 to model_name and rebuild it.
    if not obj.material_slots:
        mat = bpy.data.materials.new(model_name)
        obj.data.materials.append(mat)
    else:
        mat = obj.material_slots[0].material
        if mat is None:
            mat = bpy.data.materials.new(model_name)
            obj.material_slots[0].material = mat
    mat.name = model_name
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    uvnode = nt.nodes.new("ShaderNodeUVMap")
    uvnode.uv_map = "UVMap_New"
    col_tex = nt.nodes.new("ShaderNodeTexImage")
    col_tex.image = color_img
    color_img.colorspace_settings.name = 'sRGB'
    nrm_tex = nt.nodes.new("ShaderNodeTexImage")
    nrm_tex.image = normal_img
    normal_img.colorspace_settings.name = 'Non-Color'
    nrm_map = nt.nodes.new("ShaderNodeNormalMap")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    out = nt.nodes.new("ShaderNodeOutputMaterial")

    nt.links.new(uvnode.outputs['UV'], col_tex.inputs['Vector'])
    nt.links.new(uvnode.outputs['UV'], nrm_tex.inputs['Vector'])
    nt.links.new(col_tex.outputs['Color'], bsdf.inputs['Base Color'])
    nt.links.new(nrm_tex.outputs['Color'], nrm_map.inputs['Color'])
    nt.links.new(nrm_map.outputs['Normal'], bsdf.inputs['Normal'])
    nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])

    # Post-processing: delete UVMap_Original, rename UVMap_New -> "UVMap".
    me = obj.data
    orig = me.uv_layers.get("UVMap_Original")
    if orig:
        me.uv_layers.remove(orig)
    newuv = me.uv_layers.get("UVMap_New")
    if newuv:
        newuv.name = "UVMap"
    for uv in me.uv_layers:
        uv.active_render = True
    uvnode.uv_map = "UVMap"

    # Re-check the Armature modifier.
    arm = next((m for m in obj.modifiers if m.type == 'ARMATURE'), None)
    if arm is None:
        arm_obj = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
        if arm_obj:
            m = obj.modifiers.new("Armature", 'ARMATURE')
            m.object = arm_obj

    # Strip scale fcurves (Blender 5.0 slotted Action structure).
    _strip_scale_fcurves()

    # Pack the textures into the GLB.
    color_img.pack()
    normal_img.pack()


def _strip_scale_fcurves():
    for a in bpy.data.actions:
        # New structure (layers / strips / channelbag)
        try:
            for layer in a.layers:
                for strip in layer.strips:
                    for slot in a.slots:
                        cb = strip.channelbag(slot)
                        if cb is None:
                            continue
                        rm = [fc for fc in cb.fcurves if 'scale' in fc.data_path]
                        for fc in rm:
                            cb.fcurves.remove(fc)
        except Exception:
            pass
        # Fallback for the legacy structure.
        try:
            rm = [fc for fc in a.fcurves if 'scale' in fc.data_path]
            for fc in rm:
                a.fcurves.remove(fc)
        except Exception:
            pass


# ============================================================
# [10] Export (the most critical bug fix)
# ============================================================

def _export_glb(mesh_obj, glb_path):
    arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
    # *** The single most important point for preserving animation ***
    if arm:
        arm.hide_set(False)
        arm.hide_viewport = False
    mesh_obj.hide_set(False)
    mesh_obj.hide_viewport = False

    bpy.ops.object.select_all(action='DESELECT')
    mesh_obj.select_set(True)
    if arm:
        arm.select_set(True)
    bpy.context.view_layer.objects.active = mesh_obj

    bpy.ops.export_scene.gltf(
        filepath=glb_path, export_format='GLB', use_selection=True,
        export_apply=False, export_animations=True, export_skins=True,
        export_yup=True, export_image_format='AUTO',
    )


# ============================================================
# Mesh selection (handle multiple MESH objects)
# ============================================================

def _select_main_mesh():
    """Pick the rigged (vertex groups or Armature modifier), visible MESH with
    the most polygons as the main target, and delete the other unneeded meshes."""
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    if not meshes:
        return None
    if len(meshes) == 1:
        return meshes[0]

    def score(o):
        rigged = (len(o.vertex_groups) > 0) or any(m.type == 'ARMATURE' for m in o.modifiers)
        return (1 if rigged else 0, 1 if o.visible_get() else 0, len(o.data.polygons))

    meshes.sort(key=score, reverse=True)
    main = meshes[0]
    for o in meshes[1:]:
        bpy.data.objects.remove(o, do_unlink=True)
    return main


# ============================================================
# Shared stage: import -> repair -> color group -> island layout
# ============================================================

def _prepare_layout(glb_filename, out_dir, color_threshold, object_scale,
                    special_girafa, prefer_light, enlarge_aggressive, src_dir):
    """Run steps [1]-[6] (clean, import, repair, color grouping, island
    layout + UV) deterministically and return the resulting state.

    This is the part that MUST stay identical between the ColorMap build and
    every later stud bake, so the saved ColorMap keeps matching the UV layout.
    Always re-imports from the pristine source GLB, so it never feeds a
    processed output back into itself.
    """
    if src_dir is None:
        src_dir = os.path.dirname(out_dir.rstrip("\\/"))
    src_path = os.path.join(src_dir, glb_filename)
    model_name = os.path.splitext(glb_filename)[0]
    os.makedirs(out_dir, exist_ok=True)

    # [1] Clean + import
    full_scene_clean()
    bpy.ops.import_scene.gltf(filepath=src_path)

    grad = rename_gradient_image()
    if grad is None:
        raise RuntimeError("Gradient image not found")

    mesh_obj = _select_main_mesh()
    if mesh_obj is None:
        raise RuntimeError("No MESH object")
    bpy.context.view_layer.objects.active = mesh_obj

    # [2] Mesh repair
    repair_mesh(mesh_obj)

    # [3] Color sampling & grouping
    face_cols = _sample_gradient_colors(mesh_obj, grad)
    groups, face_group = _group_colors(face_cols, color_threshold)
    if prefer_light:
        # Bias each group's representative color toward the brightest color
        # (simplified: just keep the average color as-is for now).
        pass

    # [4] Island splitting
    islands = _build_islands(mesh_obj, face_group, groups,
                             special_girafa, prefer_light)

    # [5][6] Placement + UV
    GRID, placements, cell_w = _layout_and_uv(mesh_obj, islands, enlarge_aggressive)

    return {
        "model_name": model_name, "out_dir": out_dir,
        "mesh_obj": mesh_obj, "islands": islands, "groups": groups,
        "placements": placements, "GRID": GRID,
    }


def colormap_path(out_dir, glb_filename):
    """Return the canonical ColorMap PNG path for a model."""
    model_name = os.path.splitext(glb_filename)[0]
    return os.path.join(out_dir, model_name + "_ColorMap.png")


# ============================================================
# Stage 1: build the ColorMap (run ONCE per model)
# ============================================================

def build_colormap(glb_filename, out_dir,
                   color_threshold=0.08,
                   object_scale=4,
                   special_girafa=False,
                   prefer_light=False,
                   enlarge_aggressive=False,
                   src_dir=None):
    """Run steps [1]-[7] and save only the ColorMap PNG.

    Call this once per model. The resulting PNG is the frozen color result;
    bake_and_export() reuses it verbatim and never regenerates it.
    """
    t0 = time.time()
    st = _prepare_layout(glb_filename, out_dir, color_threshold, object_scale,
                         special_girafa, prefer_light, enlarge_aggressive, src_dir)
    # [7] ColorMap
    _make_colormap(st["mesh_obj"], st["islands"], st["placements"],
                   st["GRID"], st["model_name"], st["out_dir"])
    return {
        "model": st["model_name"],
        "islands": len(st["islands"]),
        "colors": len(st["groups"]),
        "grid": st["GRID"],
        "sec": round(time.time() - t0, 1),
        "colormap": colormap_path(st["out_dir"], glb_filename),
    }


# ============================================================
# Stage 2: bake studs + export (re-runnable, reuses the ColorMap)
# ============================================================

def bake_and_export(glb_filename, out_dir,
                    color_threshold=0.08,
                    object_scale=4,
                    special_girafa=False,
                    prefer_light=False,
                    enlarge_aggressive=False,
                    src_dir=None):
    """Re-bake the stud NormalMap at the current object_scale and export the GLB.

    Loads the EXISTING ColorMap PNG instead of regenerating it, so it can be
    re-run any number of times (e.g. to tune object_scale) without disturbing
    the approved colors. The color-related parameters MUST match those used by
    build_colormap so the recomputed UV layout still matches the saved ColorMap.
    """
    t0 = time.time()
    st = _prepare_layout(glb_filename, out_dir, color_threshold, object_scale,
                         special_girafa, prefer_light, enlarge_aggressive, src_dir)

    cpath = colormap_path(st["out_dir"], glb_filename)
    if not os.path.exists(cpath):
        raise FileNotFoundError(
            "ColorMap not found - run build_colormap() first: " + cpath)
    color_img = bpy.data.images.load(cpath, check_existing=True)
    color_img.colorspace_settings.name = 'sRGB'

    # [8] NormalMap bake
    normal_img, orig_mats, tmp_mat = _bake_normalmap(
        st["mesh_obj"], st["model_name"], st["out_dir"], object_scale)

    # [9] Rebuild
    _rebuild_material(st["mesh_obj"], st["model_name"], color_img, normal_img,
                      orig_mats, tmp_mat, special_girafa)

    # [10] Export
    out_glb = os.path.join(st["out_dir"], glb_filename)
    _export_glb(st["mesh_obj"], out_glb)

    return {
        "model": st["model_name"],
        "grid": st["GRID"],
        "object_scale": object_scale,
        "sec": round(time.time() - t0, 1),
        "out": out_glb,
    }


# ============================================================
# Main (one-shot: ColorMap + NormalMap + export in a single call)
# ============================================================

def process_glb_v5(glb_filename, out_dir,
                   color_threshold=0.08,
                   object_scale=4,
                   special_girafa=False,
                   prefer_light=False,
                   enlarge_aggressive=False,
                   src_dir=None):
    """Process a single model and output the GLB / ColorMap / NormalMap to out_dir."""
    t0 = time.time()
    st = _prepare_layout(glb_filename, out_dir, color_threshold, object_scale,
                         special_girafa, prefer_light, enlarge_aggressive, src_dir)

    # [7] ColorMap
    color_img = _make_colormap(st["mesh_obj"], st["islands"], st["placements"],
                               st["GRID"], st["model_name"], st["out_dir"])

    # [8] NormalMap bake
    normal_img, orig_mats, tmp_mat = _bake_normalmap(
        st["mesh_obj"], st["model_name"], st["out_dir"], object_scale)

    # [9] Rebuild
    _rebuild_material(st["mesh_obj"], st["model_name"], color_img, normal_img,
                      orig_mats, tmp_mat, special_girafa)

    # [10] Export
    out_glb = os.path.join(st["out_dir"], glb_filename)
    _export_glb(st["mesh_obj"], out_glb)

    return {
        "model": st["model_name"],
        "islands": len(st["islands"]),
        "colors": len(st["groups"]),
        "grid": st["GRID"],
        "sec": round(time.time() - t0, 1),
        "out": out_glb,
    }


# Quick parameter reference (special cases from the spec).
KNOWN_PARAMS = {
    "La Vaca Saturno Saturnita": dict(object_scale=8, color_threshold=0.18,
                                      prefer_light=True, enlarge_aggressive=True),
    "Girafa Celeste": dict(object_scale=4, color_threshold=0.18, special_girafa=True),
    "Boss-3": dict(object_scale=3),
    "Dragon Cannelloni": dict(object_scale=3),
    "Ballerina Cappuccina": dict(object_scale=5),
    "La Grande Combinasion": dict(object_scale=5),
    "tungtungSahur": dict(object_scale=7),
    "Liri liri larila": dict(object_scale=7),
}


def run_batch(src_dir, out_dir, only=None, skip_existing=True):
    """Process every GLB in src_dir. Pass only=[...] to limit the targets."""
    import glob as _glob
    os.makedirs(out_dir, exist_ok=True)
    glbs = sorted(_glob.glob(os.path.join(src_dir, "*.glb")))
    glbs = [g for g in glbs if "_backup_originals" not in g
            and not os.path.basename(g).startswith("_renametest")]
    log = []
    for i, path in enumerate(glbs):
        fn = os.path.basename(path)
        name = os.path.splitext(fn)[0]
        if only and name not in only:
            continue
        out_glb = os.path.join(out_dir, fn)
        if skip_existing and os.path.exists(out_glb):
            log.append({"model": name, "status": "skip(exists)"})
            continue
        params = KNOWN_PARAMS.get(name, {})
        try:
            r = process_glb_v5(fn, out_dir, src_dir=src_dir, **params)
            r["status"] = "ok"
            log.append(r)
            print(f"[{i+1}/{len(glbs)}] OK {name} "
                  f"islands={r['islands']} colors={r['colors']} grid={r['grid']} {r['sec']}s")
        except Exception as e:
            log.append({"model": name, "status": "ERROR", "err": str(e)[:200]})
            print(f"[{i+1}/{len(glbs)}] ERROR {name}: {e}")
            traceback.print_exc()
    return log
