# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 3
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####
#
# Copyright (C) 2020 Michael Vigovsky

import os, random, logging, numpy

import bpy, bpy_extras  # pylint: disable=import-error
import mathutils, bmesh # pylint: disable=import-error

from . import library, hair, rigging, utils
from .library import Timer

logger = logging.getLogger(__name__)

dist_thresh = 0.1
epsilon = 1e-30

obj_cache = {}
char_cache = {}
basis_cache = {}

def invalidate_cache():
    obj_cache.clear()
    char_cache.clear()
    basis_cache.clear()

def calc_weights(char, asset, mask):
    t = Timer()

    # dg = bpy.context.view_layer.depsgraph

    char_verts = char.data.vertices
    char_faces = char.data.polygons
    asset_verts = asset.data.vertices
    asset_faces = asset.data.polygons

    # calculate weights based on 16 nearest vertices
    kd_char = utils.kdtree_from_verts(char_verts)
    weights = [{idx: dist**2 for loc, idx, dist in kd_char.find_n(avert.co, 16)} for avert in asset_verts]

    t.time("kdtree")

    # using FromPolygons because objects can have modifiers and there is no way force FromObject to use undeformed mesh
    # calculate weights based on distance from asset vertices to character faces
    # will using bmesh be faster?
    bvh_char = mathutils.bvhtree.BVHTree.FromPolygons([v.co for v in char_verts], [f.vertices for f in char_faces])
    for i, avert in enumerate(asset_verts):
        co = avert.co
        loc, norm, idx, fdist = bvh_char.find_nearest(co)

        fdist = max(fdist, epsilon)

        if not loc or ((co-loc).dot(norm) <= 0 and fdist > dist_thresh):
            continue

        d = weights[i]
        for vi in char_faces[idx].vertices:
            d[vi] = max(d.get(vi, 0), 1/max(((co-char_verts[vi].co).length * fdist), epsilon))

    t.time("bvh direct")

    # calculate weights based on distance from character vertices to assset faces
    bvh_asset = mathutils.bvhtree.BVHTree.FromPolygons([v.co for v in asset_verts], [f.vertices for f in asset_faces])
    #bvh_asset = mathutils.bvhtree.BVHTree.FromObject(asset, dg)
    for i, cvert in enumerate(char_verts):
        co = cvert.co
        loc, norm, idx, fdist = bvh_asset.find_nearest(co, dist_thresh)
        if idx is None:
            continue

        fdist = max(fdist, epsilon)

        verts = asset_faces[idx].vertices
        dists = [max((co-asset_verts[vi].co).length, epsilon) for vi in verts]
        if min(dists)*0.9999 <= fdist:
            continue

        for vi, dist in zip(verts, dists):
            d = weights[vi]
            d[i] = max(d.get(i, 0), 1/(dist*fdist))

    t.time("bvh reverse")

    if mask:
        add_mask_from_asset(char, asset, bvh_asset, bvh_char)
        t.time("mask")

    for i, d in enumerate(weights):
        thresh = max(d.values())/16
        d = {k:v for k, v in d.items() if v > thresh}
        #fnorm = sum((char_verts[vi].normal*v for vi, v in d), mathutils.Vector())
        #fnorm.normalize()
        total = sum(d.values())
        #coeff = (sum(fnorm.dot(char_verts[vi].normal) * v  for vi, v in d)/total)
        #total *= coeff
        weights[i] = (numpy.array(list(d.keys()), numpy.uint), numpy.array(list(d.values())).reshape(-1, 1)/total)

    t.time("normalize")

    return weights

def mask_name(asset):
    return "cm_mask_{}_{}".format(asset.name, asset.data.get("charmorph_fit_id", "xxx")[:3])

def update_bbox(bbox_min, bbox_max, obj):
    for v in obj.bound_box:
        for i in range(3):
            bbox_min[i] = min(bbox_min[i], v[i])
            bbox_max[i] = max(bbox_max[i], v[i])

def add_mask_from_asset(char, asset, bvh_asset, bvh_char):
    vg_name = mask_name(asset)
    if vg_name in char.vertex_groups:
        return
    bbox_min = mathutils.Vector(asset.bound_box[0])
    bbox_max = mathutils.Vector(asset.bound_box[0])
    update_bbox(bbox_min, bbox_max, asset)

    add_mask(char, vg_name, bbox_min, bbox_max, bvh_asset, bvh_char)

def add_mask(char, vg_name, bbox_min, bbox_max, bvh_asset, bvh_char):
    def bbox_match(co):
        for i in range(3):
            if co[i] < bbox_min[i] or co[i] > bbox_max[i]:
                return False
        return True

    bbox_center = (bbox_min+bbox_max)/2

    cube_size = max(abs(v[coord]-bbox_center[coord]) for v in [bbox_min, bbox_max] for coord in range(3))
    cube_vector = mathutils.Vector([cube_size] * 3)

    bcube_min = bbox_center-cube_vector
    bcube_max = bbox_center+cube_vector

    bbox_points = [bcube_min, bbox_center, bcube_max]
    cast_points = [mathutils.Vector((bbox_points[x][0], bbox_points[y][1], bbox_points[z][2])) for x in range(3) for y in range(3) for z in range(3) if x != 1 or y != 1 or z != 1]

    def cast_rays(co, direction, max_dist=1e30):
        nonlocal has_cloth
        _, _, idx, _ = bvh_asset.ray_cast(co, direction, max_dist)
        if idx is None:
            # Vertex is not blocked by cloth. Maybe blocked by the body itself?
            _, _, idx, _ = bvh_char.ray_cast(co+direction*0.00001, direction, max_dist*0.99)
            if idx is None:
                #print(i, co, direction, max_dist, cvert.normal)
                return False # No ray hit
        else:
            has_cloth = True
        return True # Have ray hit


    covered_verts = set()

    for i, cvert in enumerate(char.data.vertices):
        co = cvert.co
        if not bbox_match(co):
            continue

        has_cloth = False
        norm = cvert.normal

        #if vertex is too close to cloth, mark it as covered
        _, _, idx, _ = bvh_asset.find_nearest(co, 0.0005)
        if idx is not None:
            #print(i, co, fhit, fdist, "too close")
            covered_verts.add(i)

        # cast one ray along vertex normal and check is there a clothing nearby
        if not cast_rays(co, norm):
            continue

        # cast rays out of 26 outside points to check whether the vertex is visible from any feasible angle
        for cast_point in cast_points:
            direction = co-cast_point
            max_dist = direction.length
            direction.normalize()
            if cvert.normal.dot(direction) > -0.5:
                continue # skip back faces and very sharp view angles
            if not cast_rays(cast_point, direction, max_dist):
                has_cloth = False
                break

        if has_cloth:
            covered_verts.add(i)

    #vg = char.vertex_groups.new(name = "covered")
    #vg.add(list(covered_verts), 1, 'REPLACE')

    boundary_verts = set()
    for f in char.data.polygons:
        for i in f.vertices:
            if i not in covered_verts:
                boundary_verts.update(f.vertices)

    covered_verts.difference_update(boundary_verts)

    if not covered_verts:
        return
    vg = char.vertex_groups.new(name=vg_name)
    vg.add(list(covered_verts), 1, 'REPLACE')
    for mod in char.modifiers:
        if mod.name == vg_name and mod.type == "MASK":
            break
    else:
        mod = char.modifiers.new(vg_name, "MASK")
    mod.invert_vertex_group = True
    mod.vertex_group = vg.name

def get_obj_weights(char, asset, mask=False):
    if "charmorph_fit_id" not in asset.data:
        asset.data["charmorph_fit_id"] = "{:016x}".format(random.getrandbits(64))

    fit_id = asset.data["charmorph_fit_id"]
    weights = obj_cache.get(fit_id)
    if weights:
        return weights

    weights = calc_weights(char, asset, mask)
    obj_cache[fit_id] = weights
    return weights

def transfer_weights(char, asset, bones):
    t = Timer()
    weights = get_obj_weights(char, asset)
    char_verts = char.data.vertices

    groups = {}

    for i, arrays in enumerate(weights):
        for vi, subweight in zip(arrays[0], arrays[1]):
            for src in char_verts[vi].groups:
                gid = src.group
                group_name = char.vertex_groups[gid].name
                if group_name not in bones:
                    continue
                vg_dst = groups.get(gid)
                if vg_dst is None:
                    if group_name in asset.vertex_groups:
                        asset.vertex_groups.remove(asset.vertex_groups[group_name])
                    vg_dst = asset.vertex_groups.new(name=group_name)
                    groups[gid] = vg_dst
                vg_dst.add([i], src.weight*subweight, 'ADD')

    t.time("weights")

def transfer_armature(char, asset):
    existing = set()
    for mod in asset.modifiers:
        if mod.type == "ARMATURE" and mod.object:
            existing.add(mod.object.name)

    bones = set()
    for mod in char.modifiers:
        if mod.type == "ARMATURE" and mod.object and mod.object.name not in existing:
            newmod = asset.modifiers.new(mod.name, "ARMATURE")
            newmod.object = mod.object
            newmod.use_deform_preserve_volume = mod.use_deform_preserve_volume
            newmod.invert_vertex_group = mod.invert_vertex_group
            newmod.use_bone_envelopes = mod.use_bone_envelopes
            newmod.use_vertex_groups = mod.use_vertex_groups
            rigging.reposition_armature_modifier(asset)
            for bone in mod.object.data.bones:
                if bone.use_deform:
                    bones.add(bone.name)

    transfer_weights(char, asset, bones)


def transfer_new_armature(char):
    for asset in get_assets(char):
        transfer_armature(char, asset)

def get_morphed_shape_key(obj):
    k = obj.data.shape_keys
    if k and k.key_blocks:
        result = k.key_blocks.get("charmorph_final")
        if result:
            return result, False

    # Creating mixed shape key every time causes some minor UI glitches. Any better idea?
    return obj.shape_key_add(from_mix=True), True

def diff_array(obj):
    morphed_shapekey, temporary = get_morphed_shape_key(obj)
    morphed = numpy.empty(len(morphed_shapekey.data)*3)
    morphed_shapekey.data.foreach_get("co", morphed)
    if temporary:
        obj.shape_key_remove(morphed_shapekey)
    basis = basis_cache.get(obj.name)
    if basis is None:
        basis = numpy.empty(len(morphed))
        obj.data.vertices.foreach_get("co", basis)
        basis_cache[obj.name] = basis
    morphed -= basis
    return morphed.reshape(-1, 3)

def do_fit(char, assets):
    t = Timer()

    diff_arr = diff_array(char)
    for asset in assets:
        weights = get_obj_weights(char, asset)
        if not asset.data.shape_keys or not asset.data.shape_keys.key_blocks:
            asset.shape_key_add(name="Basis", from_mix=False)
        asset_fitkey = asset.data.shape_keys.key_blocks.get("charmorph_fitting")
        if not asset_fitkey:
            asset_fitkey = asset.shape_key_add(name="charmorph_fitting", from_mix=False)

        verts = numpy.empty(len(asset_fitkey.data)*3)
        asset.data.vertices.foreach_get("co", verts)
        verts = verts.reshape(-1, 3)
        for i, w in enumerate(weights):
            verts[i] += (diff_arr[w[0]] * w[1]).sum(0)
        asset_fitkey.data.foreach_set("co", verts.reshape(-1))

        asset_fitkey.value = max(asset_fitkey.value, 1)

    t.time("fit")
    if bpy.context.window_manager.charmorph_ui.hair_deform:
        hair.fit_all_hair(char, diff_arr)

def masking_enabled(asset):
    return asset.data.get("charmorph_fit_mask", "true").lower() in ['true', 1, '1', 'y', 'yes']

def recalc_comb_mask(char, new_asset=None):
    t = Timer()
    # Cleanup old masks
    for mod in char.modifiers:
        if mod.name == "cm_mask_combined":
            # We preserve cm_mask_combined modifier to keep its position in case if user moved it
            mod.vertex_group = ""
        elif mod.name.startswith("cm_mask_"):
            char.modifiers.remove(mod)

    for vg in char.vertex_groups:
        if vg.name.startswith("cm_mask_"):
            char.vertex_groups.remove(vg)

    assets = get_assets(char)
    if new_asset:
        assets.append(new_asset)
    assets = [asset for asset in assets if masking_enabled(asset)]
    if not assets:
        return
    try:
        bm = bmesh.new()
        bm.from_mesh(char.data)
        bvh_char = mathutils.bvhtree.BVHTree.FromBMesh(bm)
        bm.clear()
        bbox_min = mathutils.Vector(assets[0].bound_box[0])
        bbox_max = mathutils.Vector(assets[0].bound_box[0])
        for asset in assets:
            bm.from_mesh(asset.data)
            update_bbox(bbox_min, bbox_max, asset)
        bvh_assets = mathutils.bvhtree.BVHTree.FromBMesh(bm)
    finally:
        bm.free()

    add_mask(char, "cm_mask_combined", bbox_min, bbox_max, bvh_assets, bvh_char)
    t.time("comb_mask")

def fit_new(char, asset):
    ui = bpy.context.window_manager.charmorph_ui
    if ui.fitting_transforms:
        utils.apply_transforms(asset)

    if masking_enabled(asset):
        if ui.fitting_mask == "SEPR":
            get_obj_weights(char, asset, True)
        elif ui.fitting_mask == "COMB":
            recalc_comb_mask(char, asset)

    do_fit(char, [asset])
    asset.parent = char
    char_cache.clear()
    if ui.fitting_armature:
        transfer_armature(char, asset)

def get_children(char):
    if char.name in char_cache:
        return char_cache[char.name]

    children = [obj.name for obj in char.children if obj.type == "MESH" and 'charmorph_fit_id' in obj.data]
    char_cache[char.name] = children
    return children

def get_assets(char):
    return [asset for asset in (bpy.data.objects[name] for name in get_children(char)) if asset.type == "MESH" and 'charmorph_fit_id' in asset.data]

def refit_char_assets(char):
    assets = get_assets(char)
    if assets or (bpy.context.window_manager.charmorph_ui.hair_deform and hair.has_hair(char)):
        do_fit(char, assets)

def traverse_collection(c):
    # Some versions of Blender have bugs with LayerCollection.is_visible, so using this visibility check instead
    if c.hide_viewport or c.exclude or c.collection.hide_viewport:
        return
    for obj in c.collection.objects:
        if obj.type == "MESH":
            yield obj
    for child in c.children:
        yield from traverse_collection(child)

def get_visible_meshes(_, context):
    result = [(o.name, o.name, "") for o in traverse_collection(context.layer_collection)]
    if len(result) == 0:
        return [("", "<None>", "")]
    return result

def get_fitting_assets(ui, _):
    obj = bpy.data.objects.get(ui.fitting_char)
    char = library.obj_char(obj)
    return [("char_" + k, k, '') for k in sorted(char.assets.keys())] + [("add_" + k, k, '') for k in sorted(library.additional_assets.keys())]

class UIProps:
    fitting_char: bpy.props.EnumProperty(
        name="Char",
        description="Character for fitting",
        items=get_visible_meshes)
    fitting_asset: bpy.props.EnumProperty(
        name="Local asset",
        description="Asset for fitting",
        items=get_visible_meshes)
    fitting_mask: bpy.props.EnumProperty(
        name="Mask",
        default="COMB",
        items=[
            ("NONE", "No mask", "Don't mask character at all"),
            ("SEPR", "Separate", "Use separate mask vertex groups and modifiers for each asset"),
            ("COMB", "Combined", "Use combined vertex group and modifier for all character assets"),
        ],
        description="Mask parts of character that are invisible under clothing")
    fitting_transforms: bpy.props.BoolProperty(
        name="Apply transforms",
        default=True,
        description="Apply object transforms before fitting")
    fitting_armature: bpy.props.BoolProperty(
        name="Transfer armature",
        default=True,
        description="Transfer character armature modifiers to the asset")
    fitting_library_asset: bpy.props.EnumProperty(
        name="Library asset",
        description="Select asset from library",
        items=get_fitting_assets)
    fitting_library_dir: bpy.props.StringProperty(
        name="Library dir",
        description="Additional library directory",
        update=library.update_fitting_assets,
        subtype='DIR_PATH')

class CHARMORPH_PT_Fitting(bpy.types.Panel):
    bl_label = "Assets"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 7

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" # is it neccesary?

    def draw(self, context):
        ui = context.window_manager.charmorph_ui
        col = self.layout.column(align=True)
        col.prop(ui, "fitting_char")
        col.prop(ui, "fitting_asset")
        self.layout.prop(ui, "fitting_mask")
        col = self.layout.column(align=True)
        col.prop(ui, "fitting_transforms")
        col.prop(ui, "fitting_armature")
        self.layout.separator()
        obj = bpy.data.objects.get(ui.fitting_asset)
        if obj and 'charmorph_fit_id' in obj.data:
            self.layout.operator("charmorph.unfit")
        else:
            self.layout.operator("charmorph.fit_local")
        self.layout.separator()
        self.layout.operator("charmorph.fit_external")
        self.layout.prop(ui, "fitting_library_asset")
        self.layout.operator("charmorph.fit_library")
        self.layout.prop(ui, "fitting_library_dir")
        self.layout.separator()

def mesh_obj(name):
    if not name:
        return None
    obj = bpy.data.objects.get(name)
    if obj and obj.type == "MESH":
        return obj
    return None

def get_char():
    obj = mesh_obj(bpy.context.window_manager.charmorph_ui.fitting_char)
    if not obj or 'charmorph_fit_id' in obj.data:
        return None
    return obj
def get_asset():
    return mesh_obj(bpy.context.window_manager.charmorph_ui.fitting_asset)

class OpFitLocal(bpy.types.Operator):
    bl_idname = "charmorph.fit_local"
    bl_label = "Fit local asset"
    bl_description = "Fit selected local asset to the character"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        if context.mode != "OBJECT":
            return False
        char = get_char()
        if not char:
            return False
        asset = get_asset()
        if not asset or asset == char:
            return False
        return True

    def execute(self, _): #pylint: disable=no-self-use
        fit_new(get_char(), get_asset())
        return {"FINISHED"}

def fitExtPoll(context):
    return context.mode == "OBJECT" and get_char()

def fit_import(context, file, obj):
    char = get_char()
    asset = library.import_obj(file, obj)
    if asset is None:
        return False
    fit_new(char, asset)
    context.window_manager.charmorph_ui.fitting_char = char.name # For some reason combo box value changes after importing, fix it
    return True

class OpFitExternal(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    bl_idname = "charmorph.fit_external"
    bl_label = "Fit from file"
    bl_description = "Import and fit an asset from external .blend file"
    bl_options = {"UNDO"}

    filename_ext = ".blend"
    filter_glob: bpy.props.StringProperty(default="*.blend", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return fitExtPoll(context)

    def execute(self, context):
        name, _ = os.path.splitext(self.filepath)
        if fit_import(context, self.filepath, name):
            return {"FINISHED"}
        self.report({'ERROR'}, "Import failed")
        return {"CANCELLED"}

class OpFitLibrary(bpy.types.Operator):
    bl_idname = "charmorph.fit_library"
    bl_label = "Fit from library"
    bl_description = "Import and fit an asset from library"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return fitExtPoll(context)

    def execute(self, context):
        asset_data = library.fitting_asset_data(context)
        if asset_data is None:
            self.report({'ERROR'}, "Asset is not found")
            return {"CANCELLED"}
        if fit_import(context, *asset_data):
            return {"FINISHED"}
        self.report({'ERROR'}, "Import failed")
        return {"CANCELLED"}

class OpUnfit(bpy.types.Operator):
    bl_idname = "charmorph.unfit"
    bl_label = "Unfit"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        asset = get_asset()
        return context.mode == "OBJECT" and asset and 'charmorph_fit_id' in asset.data

    def execute(self, context): # pylint: disable=no-self-use
        char_cache.clear()
        ui = context.window_manager.charmorph_ui
        asset_name = ui.fitting_asset
        asset = bpy.data.objects[asset_name]

        mask = mask_name(asset)
        for char in [asset.parent, bpy.data.objects.get(ui.fitting_char)]:
            if not char or char == asset or 'charmorph_fit_id' in char.data:
                continue
            found = False
            if mask in char.modifiers:
                char.modifiers.remove(char.modifiers[mask])
                found = True
            if mask in char.vertex_groups:
                char.vertex_groups.remove(char.vertex_groups[mask])
                found = True
            if found:
                break
        if asset.parent:
            asset.parent = asset.parent.parent
        if asset.data.shape_keys and "charmorph_fitting" in asset.data.shape_keys.key_blocks:
            asset.shape_key_remove(asset.data.shape_keys.key_blocks["charmorph_fitting"])
        del asset.data['charmorph_fit_id']

        if "cm_mask_combined" in char.modifiers:
            recalc_comb_mask(char)

        return {"FINISHED"}

classes = [OpFitLocal, OpUnfit, OpFitExternal, OpFitLibrary, CHARMORPH_PT_Fitting]
