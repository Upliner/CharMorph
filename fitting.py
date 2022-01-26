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

import os, random, logging, numpy

import bpy, bpy_extras  # pylint: disable=import-error
import mathutils, bmesh # pylint: disable=import-error

from . import library, morphing, hair, rigging, utils

logger = logging.getLogger(__name__)

dist_thresh = 0.1
epsilon = utils.epsilon

fitter = None

special_groups = frozenset(["corrective_smooth", "corrective_smooth_inv", "preserve_volume", "preserve_volume_inv"])

def masking_enabled(asset):
    return utils.is_true(asset.data.get("charmorph_fit_mask", True))

def mask_name(asset):
    return "cm_mask_{}_{}".format(asset.name, asset.data.get("charmorph_fit_id", "xxx")[:3])

def update_bbox(bbox_min, bbox_max, obj):
    for v in obj.bound_box:
        for i in range(3):
            bbox_min[i] = min(bbox_min[i], v[i])
            bbox_max[i] = max(bbox_max[i], v[i])

def intersect_faces(bvh, faces, co, dist):
    arr = bvh.find_nearest_range(co, dist)
    if len(arr) > 5 or len(arr) == 0:
        return None, None
    if len(arr) > 1:
        verts = set(faces[arr[0][2]])
        fdist = arr[0][3]
        for _, _, idx2, fdist2 in arr[1:]:
            verts.intersection_update(faces[idx2])
            fdist = min(fdist, fdist2)
        fdist = max(fdist, epsilon)
    else:
        arr = arr[0]
        verts = faces[arr[2]]
        fdist = arr[3]
    return verts, max(fdist, epsilon)

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
    def __init__(self, morpher, obj):
        self.morpher = morpher
        self.obj = obj
        self.char = library.obj_char(obj)

        self.bvh_cache = {}
        self.weights_cache = {}
        self.children = None
        self._lock_cm = False

    def alt_topo(self):
        return bool(self.morpher) and self.morpher.alt_topo

    @utils.lazyprop
    def char_verts(self):
        return self.morpher.get_basis() if self.morpher else library.get_basis(self.obj)

    @utils.lazyprop
    def char_faces(self):
        return self.char.faces if self.char.faces is not None else [f.vertices for f in self.obj.data.polygons]

    @utils.lazyprop
    def orig_char_bvh(self):
        return mathutils.bvhtree.BVHTree.FromPolygons(self.char_verts, self.char_faces)

    def get_bvh(self, data):
        key = 0 if data == self.obj else None
        if isinstance(data, bpy.types.Object):
            data = data.data
        if key is None:
            key = data.get("charmorph_fit_id", data.name)
        result = self.bvh_cache.get(data.name)
        if not result:
            if key == 0 and not self.alt_topo():
                result = self.orig_char_bvh
            else:
                result = mathutils.bvhtree.BVHTree.FromPolygons(utils.get_basis_numpy(data), [f.vertices for f in data.polygons])
            self.bvh_cache[key] = result
        return result

    def calc_weights(self, asset):
        t = utils.Timer()

        char_verts = self.char_verts

        asset_verts = asset.data.vertices
        asset_faces = [f.vertices for f in asset.data.polygons] #FIXME: performance

        subset = self.char.fitting_subset
        if subset is None:
            verts_enum = list(enumerate(char_verts))
            kd_verts_cnt = len(char_verts)
        else:
            verts_enum = [(i, char_verts[i]) for i in subset["verts"]]
            verts_set = set(subset["verts"])
            kd_verts_cnt = len(verts_enum)

        t.time("subset")

        # calculate weights based on 32 nearest vertices
        kd_char = utils.kdtree_from_verts_enum(verts_enum, kd_verts_cnt)
        weights = [{idx: 1/(max(dist, epsilon)**2) for _, idx, dist in kd_char.find_n(avert.co, 32)} for avert in asset_verts]

        t.time("kdtree")

        face_list = self.char_faces
        if subset is None:
            bvh_char = self.orig_char_bvh
        else:
            face_list = [face_list[i] for i in subset["faces"]]
            bvh_char = mathutils.bvhtree.BVHTree.FromPolygons(char_verts, face_list)

        # calculate weights based on distance from asset vertices to character faces
        for i, avert in enumerate(asset_verts):
            co = avert.co
            loc, norm, idx, fdist = bvh_char.find_nearest(co)

            if loc is None or ((co-loc).dot(norm) <= 0 and fdist > dist_thresh):
                continue

            verts, fdist = intersect_faces(bvh_char, face_list, co, max(fdist, epsilon) * 1.125)
            if verts is None:
                continue

            d = weights[i]
            for vi in verts:
                d[vi] = max(d.get(vi, 0), 1/max((co-mathutils.Vector(char_verts[vi])).length * fdist, epsilon))

        t.time("bvh direct")

        # calculate weights based on distance from character vertices to assset faces
        bvh_asset = self.get_bvh(asset)
        #bvh_asset = mathutils.bvhtree.BVHTree.FromObject(asset, dg)
        for i, cvert in verts_enum:
            if subset and i not in verts_set:
                continue
            co = mathutils.Vector(cvert)
            loc, norm, idx, fdist = bvh_asset.find_nearest(co, dist_thresh)
            if idx is None:
                continue

            fdist = max(fdist, epsilon)

            verts, fdist = intersect_faces(bvh_asset, asset_faces, co, max(fdist, epsilon) * 1.001)
            if verts is None:
                continue

            for vi in verts:
                d = weights[vi]
                d[i] = max(d.get(i, 0), 1/max((co-asset_verts[vi].co).length*fdist, epsilon))

        t.time("bvh reverse")

        positions = numpy.empty((len(weights)), dtype=numpy.uint32)

        pos = 0

        for i, d in enumerate(weights):
            thresh = max(d.values())/16
            for k,v in list(d.items()):
                if v < thresh:
                    del d[k]
            positions[i] = pos
            pos += len(d)

        t.time("cut")

        idx = numpy.empty((pos), dtype=numpy.uint32)
        wresult = numpy.empty((pos))

        pos=0
        for d in weights:
            pos1 = pos
            for k,v in d.items():
                idx[pos] = k
                wresult[pos] = v
                pos += 1
            w  = wresult[pos1:pos]
            w /= w.sum()

            #idx[pos:pos+len(d)] = list(d.keys())
            #w = wresult[pos:pos+len(d)]
            #w[:] = list(d.values())
            #pos += len(d)


        t.time("normalize")

        return (positions, idx, wresult.reshape(-1,1))

    def add_mask_from_asset(self, asset):
        vg_name = mask_name(asset)
        if vg_name in self.obj.vertex_groups:
            return
        bbox_min = mathutils.Vector(asset.bound_box[0])
        bbox_max = mathutils.Vector(asset.bound_box[0])
        update_bbox(bbox_min, bbox_max, asset)

        self.add_mask(self.get_bvh(asset), vg_name, bbox_min, bbox_max)

    def add_mask(self, bvh_asset, vg_name, bbox_min, bbox_max):
        def bbox_match(co):
            for i in range(3):
                if co[i] < bbox_min[i] or co[i] > bbox_max[i]:
                    return False
            return True

        bvh_char = self.get_bvh(self.obj)

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

        for i, cvert in enumerate(utils.get_basis_verts(self.obj)):
            co = cvert.co
            if not bbox_match(co):
                continue

            has_cloth = False
            norm = self.obj.data.vertices[i].normal

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
                if norm.dot(direction) > -0.5:
                    continue # skip back faces and very sharp view angles
                if not cast_rays(cast_point, direction, max_dist):
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

    def get_obj_weights(self, asset):
        if "charmorph_fit_id" not in asset.data:
            asset.data["charmorph_fit_id"] = "{:016x}".format(random.getrandbits(64))

        fit_id = asset.data["charmorph_fit_id"]
        weights = self.weights_cache.get(fit_id)
        if weights is not None:
            return weights

        weights = self.calc_weights(asset)
        self.weights_cache[fit_id] = weights
        return weights

    def transfer_weights(self, asset, bones):
        if not bones:
            return
        t = utils.Timer()
        positions, idx, weights = self.get_obj_weights(asset)
        char_verts = self.obj.data.vertices

        groups = {}

        i = 0
        for ptr in range(len(idx)):
            while i < len(positions)-1 and ptr >= positions[i+1]:
                i += 1
            for src in char_verts[idx[ptr]].groups:
                gid = src.group
                group_name = self.obj.vertex_groups[gid].name
                if group_name not in bones and group_name not in special_groups:
                    continue
                vg_dst = groups.get(gid)
                if vg_dst is None:
                    if group_name in asset.vertex_groups:
                        asset.vertex_groups.remove(asset.vertex_groups[group_name])
                    vg_dst = asset.vertex_groups.new(name=group_name)
                    groups[gid] = vg_dst
                vg_dst.add([i], src.weight*weights[ptr][0], 'ADD')

        t.time("weights")

    def transfer_armature(self, asset):
        existing = set()
        for mod in asset.modifiers:
            if mod.type == "ARMATURE" and mod.object:
                existing.add(mod.object.name)

        bones = set()

        modifiers = []

        for mod in self.obj.modifiers:
            if mod.type == "ARMATURE" and mod.object and mod.object.name not in existing:
                modifiers.append(mod)
                for bone in mod.object.data.bones:
                    if bone.use_deform:
                        bones.add(bone.name)

        self.transfer_weights(asset, bones)

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

    def get_morphed_shape_key(self):
        k = self.obj.data.shape_keys
        if k and k.key_blocks:
            result = k.key_blocks.get("charmorph_final")
            if result:
                return result, False

        # Creating mixed shape key every time causes some minor UI glitches. Any better idea?
        return self.obj.shape_key_add(from_mix=True), True

    def diff_array(self):
        if hasattr(self.morpher, "get_diff"):
            return self.morpher.get_diff()
        morphed_shapekey, temporary = self.get_morphed_shape_key()
        morphed = numpy.empty(len(morphed_shapekey.data)*3)
        morphed_shapekey.data.foreach_get("co", morphed)
        if temporary:
            self.obj.shape_key_remove(morphed_shapekey)
        morphed = morphed.reshape(-1, 3)
        morphed -= self.char_verts
        return morphed

    def get_target(self, asset):
        return morphing.get_target(asset) if asset == self.obj else get_fitting_shapekey(asset)

    def do_fit(self, assets, fit_hair = False):
        t = utils.Timer()

        diff_arr = self.diff_array()
        for asset in assets:
            weights = self.get_obj_weights(asset)

            verts = utils.get_basis_numpy(asset)
            verts += numpy.add.reduceat(diff_arr[weights[1]] * weights[2], weights[0])
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
            bvh_assets = self.get_bvh(assets[0])
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

        if self.children is None:
            self.get_children()
        self.children.append(asset)

        if masking_enabled(asset):
            if ui.fitting_mask == "SEPR":
                self.add_mask_from_asset(asset)
            elif ui.fitting_mask == "COMB" and not self._lock_cm:
                self.recalc_comb_mask()

        self.do_fit([asset])
        asset.parent = self.obj
        if ui.fitting_armature:
            self.transfer_armature(asset)

    def get_children(self):
        if self.children is None:
            self.children = [obj for obj in self.obj.children if obj.type == "MESH" and 'charmorph_fit_id' in obj.data]
        return self.children

    def get_assets(self):
        return [asset for asset in self.get_children() if asset.type == "MESH" and 'charmorph_fit_id' in asset.data]

    def refit_all(self):
        assets = self.get_assets()
        if self.alt_topo():
            assets.append(self.obj)
        if assets or (bpy.context.window_manager.charmorph_ui.hair_deform and hair.has_hair(self.obj)):
            self.do_fit(assets, True)


def get_fitting_assets(ui, _):
    char = library.obj_char(ui.fitting_char)
    return [("char_" + k, k, '') for k in sorted(char.assets.keys())] + [("add_" + k, k, '') for k in sorted(library.additional_assets.keys())]

class UIProps:
    fitting_char: bpy.props.PointerProperty(
        name="Char",
        description="Character for fitting",
        type=bpy.types.Object)
    fitting_asset: bpy.props.PointerProperty(
        name="Local asset",
        description="Asset for fitting",
        type=bpy.types.Object)
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
    fitting_library_asset: bpy.props.EnumProperty(
        name="Library asset",
        description="Select asset from library",
        items=get_fitting_assets)
    fitting_library_dir: bpy.props.StringProperty(
        name="Library dir",
        description="Additional library directory",
        update=library.update_fitting_assets,
        subtype='DIR_PATH')

def get_char(context):
    obj = mesh_obj(context.window_manager.charmorph_ui.fitting_char)
    if not obj or 'charmorph_fit_id' in obj.data:
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
        col.prop(ui, "fitting_transforms")
        self.layout.separator()
        obj = get_asset(context)
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
        return
    f = get_fitter(char)
    f.lock_comb_mask()
    for file, obj in lst:
        asset = library.import_obj(file, obj)
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
        asset_data = library.fitting_asset_data(context)
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
        asset_name = ui.fitting_asset
        asset = bpy.data.objects[asset_name]

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
        if asset.data.shape_keys and "charmorph_fitting" in asset.data.shape_keys.key_blocks:
            asset.shape_key_remove(asset.data.shape_keys.key_blocks["charmorph_fitting"])
        del asset.data['charmorph_fit_id']


        return {"FINISHED"}

classes = [OpFitLocal, OpUnfit, OpFitExternal, OpFitLibrary, CHARMORPH_PT_Fitting]
