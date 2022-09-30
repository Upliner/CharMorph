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

from . import fit_calc, hair, utils

logger = logging.getLogger(__name__)
special_groups = {"corrective_smooth", "corrective_smooth_inv", "preserve_volume", "preserve_volume_inv"}


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


def fit_to_bmesh(bm, afd, fitted_diff):
    try:
        morphed = afd.geom.verts + fitted_diff
        afd.obj.data.vertices.foreach_set("co", morphed.reshape(-1))
        bm.from_mesh(afd.obj.data)
    finally:
        afd.obj.data.vertices.foreach_set("co", afd.geom.verts.reshape(-1))
    return morphed.min(axis=0), morphed.max(axis=0)


class EmptyAsset:
    author = ""
    license = ""


class Fitter(hair.HairFitter):
    children: list[fit_calc.AssetFitData] = None
    transfer_calc: fit_calc.FitCalculator = None
    diff_arr: numpy.ndarray = None

    def __init__(self, morpher):
        super().__init__(morpher.core)
        self.morpher = morpher
        self.bind_cache = {}

    def add_mask_from_asset(self, afd: fit_calc.AssetFitData):
        vg_name = mask_name(afd.obj)
        if vg_name not in self.mcore.obj.vertex_groups:
            self._add_single_mask(vg_name, afd)

    def _add_single_mask(self, vg_name, afd: fit_calc.AssetFitData):
        if afd.conf.mask is not None:
            add_mask(self.mcore.obj, vg_name, afd.conf.mask.tolist())
            return

        bbox = afd.geom.bbox
        add_mask(
            self.mcore.obj, vg_name,
            calculate_mask(
                self.get_char_geom(afd), afd.geom.bvh,
                lambda _, co: bbox_match(co, bbox)))

    def recalc_comb_mask(self):
        t = utils.Timer()
        cleanup_masks(self.mcore.obj)

        assets = [afd for afd in self.get_assets() if masking_enabled(afd.obj)]
        if not assets:
            return
        if len(assets) == 1:
            self._add_single_mask("cm_mask_combined", assets[0])
            t.time("comb_mask_single")
            return

        morph_cnt = 0
        morph_afd = None
        mask = set()
        for afd in assets:
            if afd.conf.mask is not None:
                mask.update(afd.conf.mask.tolist())
            if afd.morph:
                morph_cnt += 1
                morph_afd = afd
        if morph_cnt > 1:
            morph_afd = None

        char_geom = self.geom
        if morph_cnt > 0:
            char_geom = fit_calc.geom_morph(char_geom, *(afd.morph for afd in assets if afd.morph is not None))
            diff = char_geom.verts - self.geom.verts

        bboxes = []
        try:
            bm = bmesh.new()
            for afd in assets:
                if morph_cnt > 0 and afd is not morph_afd:
                    cur_diff = diff
                    if afd.morph:
                        cur_diff = afd.morph.apply(cur_diff.copy())
                    fitted_diff = afd.binding.fit(cur_diff)
                    if (fitted_diff ** 2).sum(1).max() > 0.001:
                        bboxes.append(fit_to_bmesh(bm, afd, fitted_diff))
                        continue

                bm.from_mesh(afd.obj.data)
                bboxes.append(obj_bbox(afd.obj))
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
        data = fit_calc.get_mesh(data)
        if data is self.mcore.obj.data:
            return 0
        result = data.get("charmorph_fit_id")
        if not result:
            result = f"{random.getrandbits(64):016x}"
            data["charmorph_fit_id"] = result
        return result

    def get_binding(self, target):
        if not isinstance(target, fit_calc.AssetFitData):
            target = fit_calc.AssetFitData(target)
        fit_id = self._get_fit_id(target)

        result = self.bind_cache.get(fit_id)
        if isinstance(result, fit_calc.FitBinding):
            return result

        t = utils.Timer()
        result = super().get_binding(target)
        t.time("binding " + target.obj.name)
        self.bind_cache[fit_id] = result
        return result

    def get_diff_arr(self, morph=None):
        if self.diff_arr is None:
            self.diff_arr = self.mcore.get_diff()
        return morph.apply(self.diff_arr.copy(), -1) if morph else self.diff_arr


    def _transfer_weights_orig(self, afd: fit_calc.AssetFitData, vgs):
        handler = self.morpher.rig_handler
        if handler:
            self.transfer_weights(afd, handler.conf.weights_npz)
        else:
            self._transfer_weights_obj(afd, vgs)


    def _transfer_weights_obj(self, afd, vgs):
        if afd.obj.data is self.mcore.obj.data:
            raise Exception("Tried to self-transfer weights")
        if self.mcore.alt_topo:
            if self.transfer_calc is None:
                geom = fit_calc.geom_final(self.mcore.obj) if afd.no_refit else fit_calc.geom_mesh(self.mcore.obj.data)
                self.transfer_calc = fit_calc.FitCalculator(geom)
            calc = self.transfer_calc
        else:
            calc = self
        calc.transfer_weights(afd, zip(*utils.vg_weights_to_arrays(self.mcore.obj, lambda name: name in vgs)))

    def _transfer_armature(self, afd: fit_calc.AssetFitData):
        existing = set()
        for mod in afd.obj.modifiers:
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

        if len(vgs) <= len(special_groups):
            return

        t = utils.Timer()
        source = bpy.context.window_manager.charmorph_ui.fitting_weights
        if afd.conf.fold and afd.conf.fold.wmorph:
            afd = self._get_asset_data(afd.obj, fit_calc.geom_morph(self._get_fold_geom(afd), afd.conf.fold.wmorph))

        if source == "ORIG":
            self._transfer_weights_orig(afd, vgs)
        elif source == "OBJ":
            self._transfer_weights_obj(afd, vgs)
        else:
            raise Exception("Unknown weights source: " + source)
        t.time("weights")

        for mod in modifiers:
            newmod = afd.obj.modifiers.new(mod.name, "ARMATURE")
            newmod.object = mod.object
            newmod.use_deform_preserve_volume = mod.use_deform_preserve_volume
            newmod.invert_vertex_group = mod.invert_vertex_group
            newmod.use_bone_envelopes = mod.use_bone_envelopes
            newmod.use_vertex_groups = mod.use_vertex_groups
            newmod.use_multi_modifier = mod.use_multi_modifier
            newmod.vertex_group = mod.vertex_group
            utils.reposition_armature_modifier(afd.obj)

    def transfer_new_armature(self):
        if bpy.context.window_manager.charmorph_ui.fitting_weights == "NONE":
            return
        for afd in self.get_assets():
            self._transfer_armature(afd)
        self.tmp_buf = None
        self.transfer_calc = None

    def _get_target(self, asset):
        return utils.get_target(asset) if asset.data is self.mcore.obj.data else get_fitting_shapekey(asset)

    def fit(self, afd):
        if not afd:
            return
        if not isinstance(afd, fit_calc.AssetFitData):
            afd = self._get_asset_data(afd)
        elif afd.no_refit:
            return
        elif not afd.check_obj():
            logger.warning("Missing fitting object %s, resetting fitter", afd.obj_name)
            self.children = None
            return
        t = utils.Timer()

        verts = afd.binding.fit(self.get_diff_arr(afd.morph))
        verts += afd.geom.verts
        if self.mcore.alt_topo and afd.obj is self.mcore.obj:
            self.mcore.alt_topo_verts = verts
        self._get_target(afd.obj).foreach_set("co", verts.reshape(-1))
        afd.obj.data.update()

        t.time("fit " + afd.obj.name)

    def _fit_new_item(self, asset):
        ui = bpy.context.window_manager.charmorph_ui
        if ui.fitting_use_final:
            asset.data["charmorph_no_refit"] = True
        afd = self._get_asset_data(asset)
        if self.children is not None:
            self.children.append(afd)
        asset.parent = self.mcore.obj

        if afd.morph:
            name = afd.conf.name
            if not name:
                name = asset.name
            self.mcore.add_asset_morph(name, afd.morph)

        if ui.fitting_mask == "SEPR" and masking_enabled(asset):
            self.add_mask_from_asset(afd)

        if ui.fitting_weights != "NONE" and self.mcore.obj.find_armature():
            self._transfer_armature(afd)

        return afd

    def fit_new(self, assets):
        afd_list = [self._fit_new_item(asset) for asset in assets]
        if bpy.context.window_manager.charmorph_ui.fitting_mask == "COMB":
            for asset in assets:
                if masking_enabled(asset):
                    self.recalc_comb_mask()
                    break

        if any(afd.morph is not None for afd in afd_list):
            self.morpher.update()
        else:
            for afd in afd_list:
                self.fit(afd)

        self.transfer_calc = None

    def fit_import(self, lst):
        result = True
        objs = []
        ui = bpy.context.window_manager.charmorph_ui
        for asset in lst:
            obj = utils.import_obj(asset.blend_file, asset.name)
            if obj is None:
                result = False
                continue
            if self.mcore.char.assets.get(asset.name) is asset:
                obj.data["charmorph_asset"] = asset.name
            #if ui.fitting_transforms: # Make apply_transforms after import disablable???
            utils.apply_transforms(obj)
            objs.append(obj)
        self.fit_new(objs)
        if len(lst) == 1:
            ui.fitting_asset = obj
        return result

    def _get_children(self):
        if self.children is None:
            self.children = [
                self._get_asset_data(obj) for obj in self.mcore.obj.children
                if obj.type == "MESH" and 'charmorph_fit_id' in obj.data
            ]
        return self.children

    def get_assets(self):
        try:
            if self.children:
                for child in self.children:
                    if 'charmorph_fit_id' not in child.obj.data:
                        self.children = None
                        break
        except ReferenceError:  # can happen if some of the assets were deleted
            self.children = None

        return self._get_children()

    @utils.lazyproperty
    def alt_topo_afd(self):
        if self.mcore.alt_topo:
            return self._get_asset_data(self.mcore.obj)
        return None

    def refit_all(self):
        self.diff_arr = None
        self.fit(self.alt_topo_afd)
        hair_deform = bpy.context.window_manager.charmorph_ui.hair_deform
        if hair_deform:
            self.fit_obj_hair(self.mcore.obj)
        for afd in self.get_assets():
            self.fit(afd)
            if hair_deform:
                self.fit_obj_hair(afd.obj)

    def remove_cache(self, asset):
        keys = [asset.name]
        if "charmorph_fit_id" in asset.data:
            keys.append(asset.data["charmorph_fit_id"])
        for key in keys:
            for cache in (self.bind_cache, self.geom_cache):
                try:
                    del cache[key]
                except KeyError:
                    pass

    def clear_cache(self):
        self.bind_cache.clear()
        self.geom_cache.clear()
        self.children = None
