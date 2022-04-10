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
# Copyright (C) 2020-2022 Michael Vigovsky

import os, random, logging

import bpy, bpy_extras  # pylint: disable=import-error
import mathutils, bmesh # pylint: disable=import-error

from .lib import charlib, fit_calc, rigging, utils
from . import morphing, hair

logger = logging.getLogger(__name__)

fitter = None

special_groups = {"corrective_smooth", "corrective_smooth_inv", "preserve_volume", "preserve_volume_inv"}

def masking_enabled(asset):
    return utils.is_true(asset.data.get("charmorph_fit_mask", True))

def mask_name(asset):
    return f"cm_mask_{asset.name}_{asset.data.get('charmorph_fit_id', 'xxx')[:3]}"

def update_bbox(bbox_min, bbox_max, obj):
    for v in obj.bound_box:
        for i in range(3):
            bbox_min[i] = min(bbox_min[i], v[i])
            bbox_max[i] = max(bbox_max[i], v[i])

def get_fitter(target):
    global fitter
    if isinstance(target, morphing.Morpher):
        morpher = target
        obj = target.obj
    elif isinstance(target, bpy.types.Object):
        morpher = morphing.morpher if morphing.morpher and morphing.morpher.obj == target else None
        obj = target
    else:
        raise Exception("Fitter: invalid target")
    if not fitter or fitter.morpher != morpher or fitter.obj != obj:
        fitter = Fitter(morpher, obj)

    return fitter

def get_fitting_shapekey(obj):
    if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
        obj.shape_key_add(name="Basis", from_mix=False)
    sk = obj.data.shape_keys.key_blocks.get("charmorph_fitting")
    if not sk:
        sk = obj.shape_key_add(name="charmorph_fitting", from_mix=False)
    if sk.value < 0.75:
        sk.value = 1
    return sk.data

class Fitter:
    children: list = None
    _lock_cm = False
    transfer_calc: fit_calc.ObjFitCalculator = None

    def __init__(self, morpher, obj):
        self.obj = obj
        self.morpher = morpher
        self.weights_cache = {}
        if self.morpher is None:
            self.calc = fit_calc.ObjFitCalculator(obj, morphing.get_basis)
            self.char = charlib.obj_char(obj)
        else:
            self.calc = fit_calc.MorpherFitCalculator(morpher, morphing.get_basis)
            self.char = self.calc.char

    def add_mask_from_asset(self, asset):
        vg_name = mask_name(asset)
        if vg_name in self.obj.vertex_groups:
            return
        bbox_min = mathutils.Vector(asset.bound_box[0])
        bbox_max = mathutils.Vector(asset.bound_box[0])
        update_bbox(bbox_min, bbox_max, asset)

        self.add_mask(self.calc.get_bvh(asset), vg_name, bbox_min, bbox_max)

    def add_mask(self, bvh_asset, vg_name, bbox_min, bbox_max):
        def bbox_match(co):
            for i in range(3):
                if co[i] < bbox_min[i] or co[i] > bbox_max[i]:
                    return False
            return True

        bvh_char = self.calc.get_bvh(self.obj)

        bbox_min2 = bbox_min
        bbox_max2 = bbox_max

        update_bbox(bbox_min2, bbox_max2, self.obj)

        bbox_center = (bbox_min2+bbox_max2)/2

        cube_size = max(abs(v[coord]-bbox_center[coord]) for v in [bbox_min2, bbox_max2] for coord in range(3))*2
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
                _, _, idx, _ = bvh_char.ray_cast(co, direction, max_dist*0.99)
                if idx is None:
                    #print(i, co, direction, max_dist, cvert.normal)
                    return False # No ray hit
            else:
                has_cloth = True
            return True # Have ray hit


        covered_verts = set()

        for i, cvert in enumerate(morphing.get_basis(self.obj)):
            co = mathutils.Vector(cvert)
            if not bbox_match(co):
                continue

            has_cloth = False
            cnt = 0

            #if vertex is too close to cloth, mark it as covered
            _, _, idx, _ = bvh_asset.find_nearest(co, 0.001)
            if idx is not None:
                #print(i, co, fhit, fdist, "too close")
                covered_verts.add(i)
                continue

            # cast one ray along vertex normal and check is there a clothing nearby
            # TODO: get new normals source
            #if not cast_rays(co, norm):
            #    continue

            # cast rays out of 26 outside points to check whether the vertex is visible from any feasible angle
            for cast_point in cast_points:
                direction = co-cast_point
                max_dist = direction.length
                direction.normalize()
                #if norm.dot(direction) > -0.5:
                #    continue # skip back faces and very sharp view angles
                if not cast_rays(cast_point, direction, max_dist):
                    cnt += 1
                    if cnt == 2:
                        has_cloth = False
                        break

            if has_cloth:
                covered_verts.add(i)

        #vg = char.vertex_groups.new(name = "covered")
        #vg.add(list(covered_verts), 1, 'REPLACE')

        boundary_verts = set()
        for f in self.obj.data.polygons:
            for i in f.vertices:
                if i not in covered_verts:
                    boundary_verts.update(f.vertices)

        covered_verts.difference_update(boundary_verts)

        if not covered_verts:
            return
        vg = self.obj.vertex_groups.new(name=vg_name)
        vg.add(list(covered_verts), 1, 'REPLACE')
        for mod in self.obj.modifiers:
            if mod.name == vg_name and mod.type == "MASK":
                break
        else:
            mod = self.obj.modifiers.new(vg_name, "MASK")
        mod.invert_vertex_group = True
        mod.vertex_group = vg.name

    def _get_fit_id(self, asset):
        if asset is self.obj:
            return 0
        asset = asset.data
        if "charmorph_fit_id" not in asset:
            asset["charmorph_fit_id"] = f"{random.getrandbits(64):016x}"
        return asset["charmorph_fit_id"]

    def get_weights(self, asset):
        fit_id = self._get_fit_id(asset)

        result = self.weights_cache.get(fit_id)
        if result is not None:
            return result

        result = self.calc.get_weights(asset)
        self.weights_cache[fit_id] = result
        return result

    def _transfer_weights_orig(self, asset):
        self.calc.transfer_weights(asset, rigging.char_weights_npz(self.obj, self.char))

    def _transfer_weights_obj(self, asset, vgs):
        if asset is self.obj:
            raise Exception("Tried to self-transfer weights")
        if self.calc.alt_topo:
            if self.transfer_calc is None:
                self.transfer_calc = fit_calc.ObjFitCalculator(self.obj, morphing.get_basis, self.calc)
            calc = self.transfer_calc
        else:
            calc = self.calc
        calc.transfer_weights(asset, zip(*rigging.vg_weights_to_arrays(self.obj, lambda name: name in vgs)))

    def transfer_armature(self, asset):
        existing = set()
        for mod in asset.modifiers:
            if mod.type == "ARMATURE" and mod.object:
                existing.add(mod.object.name)

        vgs = special_groups.copy()

        modifiers = []

        for mod in self.obj.modifiers:
            if mod.type == "ARMATURE" and mod.object:
                if mod.object.name not in existing:
                    modifiers.append(mod)
                for bone in mod.object.data.bones:
                    if bone.use_deform:
                        vgs.add(bone.name)

        t = utils.Timer()
        source = bpy.context.window_manager.charmorph_ui.fitting_weights
        if source == "ORIG":
            self._transfer_weights_orig(asset)
        elif source == "OBJ":
            self._transfer_weights_obj(asset, vgs)
        else:
            raise Exception("Unknown weights source: " + source)
        t.time("weights")

        for mod in modifiers:
            newmod = asset.modifiers.new(mod.name, "ARMATURE")
            newmod.object = mod.object
            newmod.use_deform_preserve_volume = mod.use_deform_preserve_volume
            newmod.invert_vertex_group = mod.invert_vertex_group
            newmod.use_bone_envelopes = mod.use_bone_envelopes
            newmod.use_vertex_groups = mod.use_vertex_groups
            newmod.use_multi_modifier = mod.use_multi_modifier
            newmod.vertex_group = mod.vertex_group
            rigging.reposition_armature_modifier(asset)

    def transfer_new_armature(self):
        for asset in self.get_assets():
            self.transfer_armature(asset)
        self.calc.tmp_buf = None
        self.transfer_calc = None

    def diff_array(self):
        if hasattr(self.morpher, "get_diff"):
            return self.morpher.get_diff()
        morphed = utils.get_morphed_numpy(self.obj)
        morphed -= self.calc.verts
        return morphed

    def get_target(self, asset):
        return morphing.get_target(asset) if asset is self.obj else get_fitting_shapekey(asset)

    def do_fit(self, assets, fit_hair = False):
        t = utils.Timer()

        diff_arr = self.diff_array()
        for asset in assets:
            verts = fit_calc.calc_fit(diff_arr, *self.get_weights(asset))
            verts += self.calc.get_verts(asset)
            self.get_target(asset).foreach_set("co", verts.reshape(-1))
            asset.data.update()

        t.time("fit")
        if fit_hair and bpy.context.window_manager.charmorph_ui.hair_deform:
            hair.fit_all_hair(self.obj, diff_arr)

    def recalc_comb_mask(self):
        t = utils.Timer()
        # Cleanup old masks
        for mod in self.obj.modifiers:
            if mod.name == "cm_mask_combined":
                # We preserve cm_mask_combined modifier to keep its position in case if user moved it
                mod.vertex_group = ""
            elif mod.name.startswith("cm_mask_"):
                self.obj.modifiers.remove(mod)

        for vg in self.obj.vertex_groups:
            if vg.name.startswith("cm_mask_"):
                self.obj.vertex_groups.remove(vg)

        assets = [asset for asset in self.get_assets() if masking_enabled(asset)]
        if not assets:
            return
        bbox_min = mathutils.Vector(assets[0].bound_box[0])
        bbox_max = mathutils.Vector(assets[0].bound_box[0])
        if len(assets) == 1:
            bvh_assets = self.calc.get_bvh(assets[0])
            update_bbox(bbox_min, bbox_max, assets[0])
        else:
            try:
                bm = bmesh.new()
                for asset in assets:
                    bm.from_mesh(asset.data)
                    update_bbox(bbox_min, bbox_max, asset)
                bvh_assets = mathutils.bvhtree.BVHTree.FromBMesh(bm)
            finally:
                bm.free()

        self.add_mask(bvh_assets, "cm_mask_combined", bbox_min, bbox_max)
        t.time("comb_mask")

    def lock_comb_mask(self):
        self._lock_cm = True

    def unlock_comb_mask(self):
        self._lock_cm = False
        if bpy.context.window_manager.charmorph_ui.fitting_mask == "COMB":
            self.recalc_comb_mask()

    def fit_new(self, asset):
        ui = bpy.context.window_manager.charmorph_ui
        if ui.fitting_transforms:
            utils.apply_transforms(asset)

        self.do_fit([asset])

        if self.children is None:
            self.get_children()
        self.children.append(asset)
        asset.parent = self.obj

        if masking_enabled(asset):
            if ui.fitting_mask == "SEPR":
                self.add_mask_from_asset(asset)
            elif ui.fitting_mask == "COMB" and not self._lock_cm:
                self.recalc_comb_mask()

        if ui.fitting_weights != "NONE":
            self.transfer_armature(asset)

    def get_children(self):
        if self.children is None:
            self.children = [obj for obj in self.obj.children if obj.type == "MESH" and 'charmorph_fit_id' in obj.data and obj.visible_get()]
        return self.children

    def _get_assets(self):
        return [asset for asset in self.get_children() if asset.type == "MESH" and 'charmorph_fit_id' in asset.data]

    def get_assets(self):
        try:
            return self._get_assets()
        except ReferenceError: # can happen if some of the assets was deleted
            self.children = None
            return self._get_assets()

    def refit_all(self):
        assets = self.get_assets()
        if self.calc.alt_topo:
            assets.append(self.obj)
        if assets or (bpy.context.window_manager.charmorph_ui.hair_deform and hair.has_hair(self.obj)):
            self.do_fit(assets, True)


def get_fitting_assets(ui, _):
    char = charlib.obj_char(ui.fitting_char)
    return [("char_" + k, k, '') for k in sorted(char.assets.keys())] + [("add_" + k, k, '') for k in sorted(charlib.additional_assets.keys())]

class UIProps:
    fitting_char: bpy.props.PointerProperty(
        name="Char",
        description="Character for fitting",
        type=bpy.types.Object,
        poll=lambda ui, obj: utils.visible_mesh_poll(ui, obj) and ("charmorph_fit_id" not in obj.data or 'cm_alt_topo' in obj.data))
    fitting_asset: bpy.props.PointerProperty(
        name="Local asset",
        description="Asset for fitting",
        type=bpy.types.Object,
        poll=lambda ui, obj: utils.visible_mesh_poll(ui, obj) and ("charmorph_template" not in obj.data))
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
    fitting_weights: bpy.props.EnumProperty(
        name="Weights",
        default="ORIG",
        items= [
            ("NONE", "None", "Don't transfer weights and armature modifiers to the asset"),
            ("ORIG", "Original", "Use original weights from character library"),
            ("OBJ", "Object", "Use weights directly from object (use it if you manually weight-painted the character before fitting the asset)"),
        ],
        description="Select source for armature deform weights")
    fitting_weights_ovr: bpy.props.BoolProperty(
        name="Weights overwrite",
        default=False,
        description="Overwrite existing asset weights")
    fitting_library_asset: bpy.props.EnumProperty(
        name="Library asset",
        description="Select asset from library",
        items=get_fitting_assets)
    fitting_library_dir: bpy.props.StringProperty(
        name="Library dir",
        description="Additional library directory",
        update=charlib.update_fitting_assets,
        subtype='DIR_PATH')

def get_char(context):
    obj = mesh_obj(context.window_manager.charmorph_ui.fitting_char)
    if not obj or ('charmorph_fit_id' in obj.data and 'cm_alt_topo' not in obj.data):
        return None
    return obj

def get_asset(context):
    return mesh_obj(context.window_manager.charmorph_ui.fitting_asset)

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
        col.prop(ui, "fitting_weights")
        col.prop(ui, "fitting_weights_ovr")
        col.prop(ui, "fitting_transforms")
        self.layout.separator()
        if ui.fitting_asset and 'charmorph_fit_id' in ui.fitting_asset.data:
            self.layout.operator("charmorph.unfit")
        else:
            self.layout.operator("charmorph.fit_local")
        self.layout.separator()
        self.layout.operator("charmorph.fit_external")
        self.layout.prop(ui, "fitting_library_asset")
        self.layout.operator("charmorph.fit_library")
        self.layout.prop(ui, "fitting_library_dir")
        self.layout.separator()

def mesh_obj(obj):
    if obj and obj.type == "MESH":
        return obj
    return None

class OpFitLocal(bpy.types.Operator):
    bl_idname = "charmorph.fit_local"
    bl_label = "Fit local asset"
    bl_description = "Fit selected local asset to the character"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        if context.mode != "OBJECT":
            return False
        char = get_char(context)
        if not char:
            return False
        asset = get_asset(context)
        if not asset or asset == char:
            return False
        return True

    def execute(self, context): #pylint: disable=no-self-use
        get_fitter(get_char(context)).fit_new(get_asset(context))
        return {"FINISHED"}

def fitExtPoll(context):
    return context.mode == "OBJECT" and get_char(context)

def fit_import(char, lst):
    if len(lst) == 0:
        return True
    f = get_fitter(char)
    f.lock_comb_mask()
    for file, obj in lst:
        asset = utils.import_obj(file, obj)
        if asset is None:
            return False
        f.fit_new(asset)
    f.unlock_comb_mask()
    ui = bpy.context.window_manager.charmorph_ui
    ui.fitting_char = char # For some reason combo box value changes after importing, fix it
    if len(lst) == 1:
        ui.fitting_asset = asset
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
        if fit_import(get_char(context), ((self.filepath, name),)):
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
        asset_data = charlib.fitting_asset_data(context)
        if asset_data is None:
            self.report({'ERROR'}, "Asset is not found")
            return {"CANCELLED"}
        if fit_import(get_char(context), (asset_data,)):
            return {"FINISHED"}
        self.report({'ERROR'}, "Import failed")
        return {"CANCELLED"}

class OpUnfit(bpy.types.Operator):
    bl_idname = "charmorph.unfit"
    bl_label = "Unfit"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        asset = get_asset(context)
        return context.mode == "OBJECT" and asset and 'charmorph_fit_id' in asset.data

    def execute(self, context): # pylint: disable=no-self-use
        ui = context.window_manager.charmorph_ui
        asset = get_asset(context)

        del asset.data['charmorph_fit_id']
        mask = mask_name(asset)
        for char in [asset.parent, ui.fitting_char]:
            if not char or char == asset or 'charmorph_fit_id' in char.data:
                continue
            if mask in char.modifiers:
                char.modifiers.remove(char.modifiers[mask])
            if mask in char.vertex_groups:
                char.vertex_groups.remove(char.vertex_groups[mask])
            if "cm_mask_combined" in char.modifiers:
                f = get_fitter(char)
                f.children = None
                f.recalc_comb_mask()
        if asset.parent:
            asset.parent = asset.parent.parent
            if asset.parent and asset.parent.type == "ARMATURE":
                asset.parent = asset.parent.parent
        if asset.data.shape_keys and "charmorph_fitting" in asset.data.shape_keys.key_blocks:
            asset.shape_key_remove(asset.data.shape_keys.key_blocks["charmorph_fitting"])

        return {"FINISHED"}

classes = [OpFitLocal, OpUnfit, OpFitExternal, OpFitLibrary, CHARMORPH_PT_Fitting]
