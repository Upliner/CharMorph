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

import bpy, bmesh, mathutils  # pylint: disable=import-error

from . import fit_calc, utils

logger = logging.getLogger(__name__)


def masking_enabled(asset):
    return utils.is_true(asset.data.get("charmorph_fit_mask", True))


def mask_name(asset):
    return f"cm_mask_{asset.name}_{asset.data.get('charmorph_fit_id', 'xxx')[:3]}"


def get_fitting_shapekey(obj):
    if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
        obj.shape_key_add(name="Basis", from_mix=False)
    sk = obj.data.shape_keys.key_blocks.get("charmorph_fitting")
    if not sk:
        sk = obj.shape_key_add(name="charmorph_fitting", from_mix=False)
    if sk.value < 0.75:
        sk.value = 1
    return sk.data


def cleanup_masks(obj):
    for mod in obj.modifiers:
        if mod.name == "cm_mask_combined":
            # We preserve cm_mask_combined modifier to keep its position in case if user moved it
            mod.vertex_group = ""
        elif mod.name.startswith("cm_mask_"):
            obj.modifiers.remove(mod)

    for vg in obj.vertex_groups:
        if vg.name.startswith("cm_mask_"):
            obj.vertex_groups.remove(vg)


def shrink_vertex_set(vset: set, faces):
    boundary_verts = set()
    for f in faces:
        for i in f:
            if i not in vset:
                boundary_verts.update(f)

    vset.difference_update(boundary_verts)


def get_cast_points(bmin: numpy.ndarray, bmax: numpy.ndarray):
    center = (bmin + bmax) / 2
    size = (bmax - bmin).max()

    points = numpy.vstack((center - size, center, center + size))
    return [
        mathutils.Vector((points[x][0], points[y][1], points[z][2]))
        for x in range(3) for y in range(3) for z in range(3)
        if x != 1 or y != 1 or z != 1
    ]


def calculate_mask(char_geom: fit_calc.Geometry, bvh_asset, match_func=lambda _idx, _co: True):
    cast_points = get_cast_points(*char_geom.bbox)
    bvh_char = char_geom.bvh

    def cast_rays(co, direction, max_dist=1e30):
        nonlocal has_cloth
        idx = bvh_asset.ray_cast(co, direction, max_dist)[2]
        if idx is None:
            # Vertex is not blocked by cloth. Maybe blocked by the body itself?
            idx = bvh_char.ray_cast(co, direction, max_dist * 0.99)[2]
            if idx is None:
                return False  # No ray hit
        else:
            has_cloth = True
        return True  # Have ray hit

    result = set()
    for i, co in enumerate(char_geom.verts):
        if not match_func(i, co):
            continue
        co = mathutils.Vector(co)
        has_cloth = False
        cnt = 0

        # if vertex is too close to cloth, mark it as covered
        idx = bvh_asset.find_nearest(co, 0.001)[2]
        if idx is not None:
            result.add(i)
            continue

        for cast_point in cast_points:
            direction = co - cast_point
            max_dist = direction.length
            if not cast_rays(cast_point, direction, max_dist):
                cnt += 1
                if cnt == 2:
                    has_cloth = False
                    break

        if has_cloth:
            result.add(i)

    shrink_vertex_set(result, char_geom.faces)
    return result


def add_mask(obj, vg_name, verts):
    if not verts:
        return
    vg = obj.vertex_groups.new(name=vg_name)
    vg.add(list(verts), 1, 'REPLACE')
    for mod in obj.modifiers:
        if mod.name == vg_name and mod.type == "MASK":
            break
    else:
        mod = obj.modifiers.new(vg_name, "MASK")
    mod.invert_vertex_group = True
    mod.vertex_group = vg.name


def obj_bbox(obj):
    bbox_min = mathutils.Vector((obj.bound_box[0]))
    bbox_max = mathutils.Vector((obj.bound_box[0]))
    for v in obj.bound_box[1:]:
        for i in range(3):
            bbox_min[i] = min(bbox_min[i], v[i])
            bbox_max[i] = max(bbox_max[i], v[i])
    return bbox_min, bbox_max


def bbox_match(co, bbox):
    for i in range(3):
        if co[i] < bbox[0][i] or co[i] > bbox[1][i]:
            return False
    return True


special_groups = {"corrective_smooth", "corrective_smooth_inv", "preserve_volume", "preserve_volume_inv"}


class EmptyAsset:
    author = ""
    license = ""


class Fitter(fit_calc.MorpherFitCalculator):
    children: list = None
    transfer_calc: fit_calc.FitCalculator = None
    diff_arr: numpy.ndarray = None

    def __init__(self, mcore, morpher=None):
        super().__init__(mcore)
        self.morpher = morpher
        self.weights_cache = {}

    def add_mask_from_asset(self, asset):
        vg_name = mask_name(asset)
        if vg_name not in self.mcore.obj.vertex_groups:
            self._add_single_mask(vg_name, asset)

    def _add_single_mask(self, vg_name, asset):
        custom_mask = self._get_asset_conf(asset).mask
        if custom_mask is not None:
            add_mask(self.mcore.obj, vg_name, custom_mask.tolist())
            return

        asset_geom = self.get_asset_geom(asset)
        bbox = asset_geom.bbox
        add_mask(
            self.mcore.obj, vg_name,
            calculate_mask(
                self.get_char_geom(asset), asset_geom.bvh,
                lambda _, co: bbox_match(co, bbox)))

    def fit_to_bmesh(self, bm, asset, fitted_diff):
        geom = self.get_asset_geom(asset)
        try:
            morphed = geom.verts + fitted_diff
            asset.data.vertices.foreach_set("co", morphed.reshape(-1))
            bm.from_mesh(asset.data)
        finally:
            asset.data.vertices.foreach_set("co", geom.verts.reshape(-1))
        return morphed.min(axis=0), morphed.max(axis=0)

    def recalc_comb_mask(self):
        t = utils.Timer()
        cleanup_masks(self.mcore.obj)

        assets = [asset for asset in self.get_assets() if masking_enabled(asset)]
        if not assets:
            return
        if len(assets) == 1:
            self._add_single_mask("cm_mask_combined", assets[0])
            t.time("comb_mask_single")
            return

        asset_morphs = []
        morph_cnt = 0
        morph_asset = None
        mask = set()
        for asset in assets:
            asset_mask = self._get_asset_conf(asset).mask
            if asset_mask is not None:
                mask.update(asset_mask.tolist())
            morph = self._get_asset_morph(asset)
            asset_morphs.append(morph)
            if morph:
                morph_cnt += 1
                morph_asset = asset
        if morph_cnt > 1:
            morph_asset = None

        char_geom = self.geom
        if morph_cnt > 0:
            char_geom = fit_calc.geom_morph(char_geom, *(morph for morph in asset_morphs if morph is not None))
            diff = char_geom.verts - self.geom.verts

        bboxes = []
        try:
            bm = bmesh.new()
            for asset, morph in zip(assets, asset_morphs):
                if morph_cnt > 0 and asset is not morph_asset:
                    cur_diff = diff
                    if morph:
                        cur_diff = morph.apply(cur_diff.copy())
                    fitted_diff = fit_calc.calc_fit(cur_diff, *self.get_weights(asset))
                    if (fitted_diff ** 2).sum(1).max() > 0.001:
                        bboxes.append(self.fit_to_bmesh(bm, asset, fitted_diff))
                        continue

                bm.from_mesh(asset.data)
                bboxes.append(obj_bbox(asset))
            bvh_assets = mathutils.bvhtree.BVHTree.FromBMesh(bm)
        finally:
            bm.free()

        t.time("mask_bvh")

        def check_func(idx, co):
            if idx in mask:
                return False
            for box in bboxes:
                if bbox_match(co, box):
                    return True
            return False

        mask.update(calculate_mask(char_geom, bvh_assets, check_func))
        add_mask(self.mcore.obj, "cm_mask_combined", mask)
        t.time("comb_mask")

    def _get_fit_id(self, data):
        if isinstance(data, bpy.types.Object):
            data = data.data
        if data is self.mcore.obj.data:
            return 0
        result = data.get("charmorph_fit_id")
        if not result:
            result = f"{random.getrandbits(64):016x}"
            data["charmorph_fit_id"] = result
        return result

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

    # use separate get_diff function to support hair fitting for posed characters
    def get_diff_hair(self):
        char = self.mcore.obj
        if not char.find_armature():
            return self.get_diff_arr(None)

        restore_modifiers = utils.disable_modifiers(char)
        echar = char.evaluated_get(bpy.context.evaluated_depsgraph_get())
        try:
            deformed = echar.to_mesh()
            basis = self.mcore.get_basis_alt_topo()
            if len(deformed.vertices) != len(basis):
                logger.error("Can't fit posed hair: vertex count mismatch")
                return self.get_diff_arr(None)
            result = numpy.empty(len(basis) * 3)
            deformed.vertices.foreach_get("co", result)
            result = result.reshape(-1, 3)
            result -= basis
            return result
        finally:
            echar.to_mesh_clear()
            for m in restore_modifiers:
                m.show_viewport = True

    def fit_hair(self, obj, idx):
        t = utils.Timer()
        psys = obj.particle_systems[idx]
        cnts, data, weights = self.get_hair_data(psys)
        if cnts is None or data is None or not weights:
            return False

        morphed = numpy.empty((len(data) + 1, 3))
        morphed[1:] = fit_calc.calc_fit(self.get_diff_hair(), *weights)
        morphed[1:] += data

        obj.particle_systems.active_index = idx

        t.time("hair_fit_calc")

        restore_modifiers = utils.disable_modifiers(obj, lambda m: m.type == "SHRINKWRAP")
        try:
            utils.set_hair_points(obj, cnts, morphed)
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
        self.transfer_weights(asset, utils.char_weights_npz(self.mcore.obj, self.mcore.char))

    def _transfer_weights_obj(self, asset, vgs):
        if asset.data is self.mcore.obj.data:
            raise Exception("Tried to self-transfer weights")
        if self.mcore.alt_topo:
            if self.transfer_calc is None:
                self.transfer_calc = fit_calc.FitCalculator(fit_calc.geom_mesh(self.mcore.obj.data), self)
            calc = self.transfer_calc
        else:
            calc = self
        calc.transfer_weights(asset, zip(*utils.vg_weights_to_arrays(self.mcore.obj, lambda name: name in vgs)))

    def _transfer_armature(self, asset):
        existing = set()
        for mod in asset.modifiers:
            if mod.type == "ARMATURE" and mod.object:
                existing.add(mod.object.name)

        vgs = special_groups.copy()

        modifiers = []

        for mod in self.mcore.obj.modifiers:
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
            utils.reposition_armature_modifier(asset)

    def transfer_new_armature(self):
        for asset in self.get_assets():
            self._transfer_armature(asset)
        self.tmp_buf = None
        self.transfer_calc = None

    def get_target(self, asset):
        return utils.get_target(asset) if asset.data is self.mcore.obj.data else get_fitting_shapekey(asset)

    def fit(self, asset, morph=False):
        t = utils.Timer()

        if morph is False:
            morph = self._get_asset_morph(asset)
        verts = self.calc_fit(self.get_weights(asset), morph)
        verts += self.get_asset_geom(asset).verts
        self.get_target(asset).foreach_set("co", verts.reshape(-1))
        asset.data.update()

        t.time("fit " + asset.name)

    def _fit_new_item(self, asset):
        ui = bpy.context.window_manager.charmorph_ui
        if ui.fitting_transforms:
            utils.apply_transforms(asset)

        if self.children is None:
            self.get_children()
        self.children.append(asset)
        asset.parent = self.mcore.obj

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
        if len(lst) == 1:
            ui.fitting_asset = obj
        return result

    def get_children(self):
        if self.children is None:
            self.children = [
                obj for obj in self.mcore.obj.children
                if obj.type == "MESH" and 'charmorph_fit_id' in obj.data and obj.visible_get()
            ]
        return self.children

    def _get_assets(self):
        return [
            asset for asset in self.get_children()
            if asset.type == "MESH" and 'charmorph_fit_id' in asset.data
        ]

    def get_assets(self):
        try:
            return self._get_assets()
        except ReferenceError:  # can happen if some of the assets were deleted
            self.children = None
            return self._get_assets()

    def refit_all(self):
        self.diff_arr = None
        if self.mcore.alt_topo:
            self.fit(self.mcore.obj)
        hair_deform = bpy.context.window_manager.charmorph_ui.hair_deform
        if hair_deform:
            self.fit_obj_hair(self.mcore.obj)
        for asset in self.get_assets():
            self.fit(asset)
            if hair_deform:
                self.fit_obj_hair(asset)

    def remove_cache(self, asset):
        keys = [asset.name]
        if "charmorph_fit_id" in asset.data:
            keys.append(asset.data["charmorph_fit_id"])
        for key in keys:
            for cache in (self.weights_cache, self.geom_cache):
                try:
                    del cache[key]
                except KeyError:
                    pass
