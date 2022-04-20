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

import random, logging, numpy

import bpy, bmesh, mathutils # pylint: disable=import-error

from . import fit_calc, rigging, utils

logger = logging.getLogger(__name__)

def masking_enabled(asset):
    return utils.is_true(asset.data.get("charmorph_fit_mask", True))

def update_bbox(bbox_min, bbox_max, obj):
    for v in obj.bound_box:
        for i in range(3):
            bbox_min[i] = min(bbox_min[i], v[i])
            bbox_max[i] = max(bbox_max[i], v[i])

def get_fitting_shapekey(obj):
    if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
        obj.shape_key_add(name="Basis", from_mix=False)
    sk = obj.data.shape_keys.key_blocks.get("charmorph_fitting")
    if not sk:
        sk = obj.shape_key_add(name="charmorph_fitting", from_mix=False)
    if sk.value < 0.75:
        sk.value = 1
    return sk.data

def mask_name(asset):
    return f"cm_mask_{asset.name}_{asset.data.get('charmorph_fit_id', 'xxx')[:3]}"

special_groups = {"corrective_smooth", "corrective_smooth_inv", "preserve_volume", "preserve_volume_inv"}

class EmptyAsset:
    author = ""
    license = ""
    morph = None

class Fitter(fit_calc.MorpherFitCalculator):
    children: list = None
    transfer_calc: fit_calc.ObjFitCalculator = None
    diff_arr: numpy.ndarray = None

    def __init__(self, mcore, morpher=None):
        super().__init__(mcore)
        self.morpher = morpher
        self.weights_cache = {}

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

        for i, cvert in enumerate(self.get_basis(self.obj)):
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

        result = super().get_weights(asset)
        self.weights_cache[fit_id] = result
        return result

    def get_diff_arr(self, morph):
        if self.diff_arr is None:
            self.diff_arr = self.mcore.get_diff()
        return morph.apply(self.diff_arr.copy(), -1) if morph else self.diff_arr

    def calc_fit(self, weights_tuple, morph=None):
        return fit_calc.calc_fit(self.get_diff_arr(morph), *weights_tuple)

    def get_hair_data(self, psys):
        if not psys.is_edited:
            return None
        fit_id = psys.settings.get("charmorph_fit_id")
        if fit_id:
            data = self.weights_cache.get(fit_id)
            if data:
                return data

        z = self.mcore.char.get_np(f"hairstyles/{psys.settings.get('charmorph_hairstyle','')}.npz")
        if z is None:
            logger.error("Hairstyle npz file is not found")
            return None

        cnts = z["cnt"]
        data = z["data"].astype(dtype=numpy.float64, casting="same_kind")

        if len(cnts) != len(psys.particles):
            logger.error("Mismatch between current hairsyle and .npz!")
            return None

        weights = self.calc_weights_hair(data)
        self.weights_cache[fit_id] = (cnts, data, weights)
        return cnts, data, weights

    def fit_hair(self, obj, idx):
        t = utils.Timer()
        psys = obj.particle_systems[idx]
        cnts, data, weights = self.get_hair_data(psys)
        if cnts is None or data is None or not weights:
            return False

        morphed = numpy.empty((len(data)+1, 3))
        morphed[1:] = self.calc_fit(weights)
        morphed[1:] += data

        obj.particle_systems.active_index = idx

        t.time("hair_fit_calc")

        restore_modifiers = utils.disable_modifiers(obj, lambda m: m.type=="SHRINKWRAP")
        try:
            utils.set_hair_points(obj, cnts, morphed)
        except Exception as e:
            logger.error(str(e))
            self.weights_cache.clear()
        finally:
            for m in restore_modifiers:
                m.show_viewport = True

        t.time("hair_fit_set")
        return True

    def fit_obj_hair(self, obj):
        has_fit = False
        for i in range(len(obj.particle_systems)):
            has_fit |= self.fit_hair(obj, i)
        return has_fit

    def _transfer_weights_orig(self, asset):
        self.transfer_weights(asset, rigging.char_weights_npz(self.obj, self.mcore.char))

    def _transfer_weights_obj(self, asset, vgs):
        if asset is self.obj:
            raise Exception("Tried to self-transfer weights")
        if self.alt_topo:
            if self.transfer_calc is None:
                self.transfer_calc = fit_calc.ObjFitCalculator(self.obj, self.get_basis, self)
            calc = self.transfer_calc
        else:
            calc = self
        calc.transfer_weights(asset, zip(*rigging.vg_weights_to_arrays(self.obj, lambda name: name in vgs)))

    def _transfer_armature(self, asset):
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
            self._transfer_armature(asset)
        self.tmp_buf = None
        self.transfer_calc = None

    def get_target(self, asset):
        return utils.get_target(asset) if asset is self.obj else get_fitting_shapekey(asset)

    def fit(self, asset, morph=False):
        t = utils.Timer()

        if morph is False:
            morph = self._get_asset_morph(asset)
        verts = self.calc_fit(self.get_weights(asset), morph)
        verts += self.get_verts(asset)
        self.get_target(asset).foreach_set("co", verts.reshape(-1))
        asset.data.update()

        t.time("fit")

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

    def _fit_new_item(self, asset):
        ui = bpy.context.window_manager.charmorph_ui
        if ui.fitting_transforms:
            utils.apply_transforms(asset)

        if self.children is None:
            self.get_children()
        self.children.append(asset)
        asset.parent = self.obj

        if ui.fitting_weights != "NONE":
            self._transfer_armature(asset)

    def fit_new(self, assets):
        ui = bpy.context.window_manager.charmorph_ui
        comb_mask = False
        has_morphs = False
        for asset in assets:
            self._fit_new_item(asset)
            c = self._get_asset_conf(asset)
            if c and c.morph:
                self.mcore.add_asset_morph(c.name, c.morph)
                has_morphs = True
            if masking_enabled(asset):
                if ui.fitting_mask == "SEPR":
                    self.add_mask_from_asset(asset)
                elif ui.fitting_mask == "COMB":
                    comb_mask = True

        if comb_mask:
            self.recalc_comb_mask()

        if has_morphs:
            self.update_char()
        else:
            for asset in assets:
                self.fit(asset, None)

        self.transfer_calc = None

    def update_char(self):
        if self.morpher:
            self.morpher.update()
        else:
            self.mcore.update()
            self.refit_all()

    def fit_import(self, lst):
        result = True
        objs = []
        for asset in lst:
            obj = utils.import_obj(asset.blend_file, asset.name)
            if obj is None:
                result = False
                continue
            if self.mcore.char.assets.get(asset.name) is asset:
                obj.data["charmorph_asset"] = asset.name
            objs.append(obj)
        self.fit_new(objs)
        ui = bpy.context.window_manager.charmorph_ui
        # TODO: find the reason
        #ui.fitting_char = self.mcore.obj # For some reason combo box value changes after importing, fix it
        if len(lst) == 1:
            ui.fitting_asset = obj
        return result

    def get_children(self):
        if self.children is None:
            self.children = [obj for obj in self.obj.children if obj.type == "MESH" and 'charmorph_fit_id' in obj.data and obj.visible_get()]
        return self.children

    def _get_assets(self):
        return [asset for asset in self.get_children() if asset.type == "MESH" and 'charmorph_fit_id' in asset.data]

    def get_assets(self):
        try:
            return self._get_assets()
        except ReferenceError: # can happen if some of the assets were deleted
            self.children = None
            return self._get_assets()

    def refit_all(self):
        self.diff_arr = None
        if self.alt_topo:
            self.fit(self.obj)
        hair_deform = bpy.context.window_manager.charmorph_ui.hair_deform
        if hair_deform:
            self.fit_obj_hair(self.obj)
        for asset in self.get_assets():
            self.fit(asset)
            if hair_deform:
                self.fit_obj_hair(asset)
