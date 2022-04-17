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

from . import morphers, rigging, utils

logger = logging.getLogger(__name__)

dist_thresh = 0.1
epsilon = 1e-30

def weights_convert(weights, cut=True):
    positions = numpy.empty((len(weights)), dtype=numpy.uint32)
    pos = 0
    idx = []
    wresult = []
    thresh=0
    for i, d in enumerate(weights):
        if cut:
            thresh = max(d.values())/32
        positions[i] = pos
        for k,v in d.items():
            if v >= thresh:
                idx.append(k)
                wresult.append(v)
                pos += 1
    idx = numpy.array(idx, dtype=numpy.uint32)
    wresult = numpy.array(wresult)
    return positions, idx, wresult

def weights_normalize(positions, wresult):
    cnt = numpy.empty((len(positions)), dtype=numpy.uint32)
    cnt[:-1] = positions[1:]
    cnt[:-1] -= positions[:-1]
    cnt[-1]=len(wresult)-positions[-1]
    wresult /= numpy.add.reduceat(wresult, positions).repeat(cnt)

# calculate weights based on nearest vertices
def calc_weights_kd(kd, verts, _epsilon, n):
    return [{idx: 1/(max(dist**2, _epsilon)) for _, idx, dist in kd.find_n(v, n)} for v in verts]

def calc_fit(arr, positions, idx, weights):
    return numpy.add.reduceat(arr[idx] * weights, positions)

class FitCalculator:
    verts: numpy.ndarray
    faces: list
    subset_faces: list
    subset_bvh: mathutils.bvhtree.BVHTree

    tmp_buf: numpy.ndarray = None
    alt_topo = False

    def __init__(self, obj, get_basis, parent=None):
        self.obj = obj
        self.get_basis = get_basis
        if parent is not None:
            self.verts_cache = parent.verts_cache
            self.bvh_cache = parent.bvh_cache
        else:
            self.verts_cache = {}
            self.bvh_cache = {}

    def subset_verts_cnt(self):
        return len(self.verts)
    def subset_verts_enum(self):
        return enumerate(self.verts)

    @utils.lazyproperty
    def subset_kd(self):
        return utils.kdtree_from_verts_enum(self.subset_verts_enum(), self.subset_verts_cnt())

    @utils.lazyproperty
    def bvh(self):
        return mathutils.bvhtree.BVHTree.FromPolygons(self.verts, self.faces)

    def _get_cached(self, cache, data, func_self, func_calc):
        if isinstance(data, bpy.types.Object):
            data = data.data
        key = 0 if data is self.obj.data else data.get("charmorph_fit_id", data.name)
        result = cache.get(key)
        if result is None:
            if key == 0 and not self.alt_topo:
                result = func_self()
            else:
                result = func_calc(data)
            cache[key] = result
        return result

    def get_verts(self, data):
        return self._get_cached(self.verts_cache, data, lambda: self.verts, self.get_basis)

    def get_bvh(self, data):
        return self._get_cached(self.bvh_cache, data, lambda: self.bvh, lambda mesh:
            mathutils.bvhtree.BVHTree.FromPolygons(self.get_verts(mesh), [f.vertices for f in mesh.polygons]))

    # These functions are performance-critical so disable pylint too-many-locals error for them

    # calculate weights based on distance from asset vertices to character faces
    def _calc_weights_direct(self, weights, asset_verts): # pylint: disable=too-many-locals
        verts = self.verts
        faces = self.subset_faces
        bvh = self.subset_bvh
        for i, v in enumerate(asset_verts):
            loc, _, idx, fdist = bvh.find_nearest(v, dist_thresh)
            if loc is None:
                continue
            face = faces[idx]
            d = weights[i]
            fdist = max(fdist ** 2, epsilon)
            for vi, bw in zip(face, mathutils.interpolate.poly_3d_calc(verts[face].tolist(), loc)):
                d[vi] = max(d.get(vi, 0), bw/fdist)

    # calculate weights based on distance from character vertices to assset faces
    def _calc_weights_reverse(self, weights, asset): # pylint: disable=too-many-locals
        verts = self.get_verts(asset)
        faces = asset.data.polygons
        bvh = self.get_bvh(asset)
        for i, cvert in self.subset_verts_enum():
            loc, _, idx, fdist = bvh.find_nearest(cvert, dist_thresh)
            if idx is None:
                continue
            face = faces[idx].vertices
            fdist = max(fdist ** 2, 1e-15) # using lower epsilon to avoid some artifacts
            for vi, bw in zip(face, mathutils.interpolate.poly_3d_calc([verts[i] for i in face], loc)):
                d = weights[vi]
                d[i] = max(d.get(i, 0), bw/fdist)

    def _calc_weights_internal(self, verts, stage3=None):
        t = utils.Timer()
        weights = calc_weights_kd(self.subset_kd, verts, epsilon, 16)
        t.time("kdtree")
        self._calc_weights_direct(weights, verts)
        t.time("bvh direct")
        if stage3:
            stage3(weights, t)
        positions, idx, wresult = weights_convert(weights)
        t.time("convert")
        weights_normalize(positions, wresult)
        t.time("normalize")
        return positions, idx, wresult.reshape(-1,1)

    def get_weights(self, asset):
        def stage3(weights, t):
            self._calc_weights_reverse(weights, asset)
            t.time("bvh reverse")
        return self._calc_weights_internal(self.get_verts(asset), stage3)

    def calc_weights_hair(self, arr):
        return self._calc_weights_internal(arr)

    def _transfer_weights_iter_arrays(self, asset, vg_data):
        if self.tmp_buf is None:
            self.tmp_buf = numpy.empty((len(self.verts)))
        positions, fit_idx, fit_weights = self.get_weights(asset)
        fit_weights = fit_weights.reshape(-1) # While fitting we reduce 2D arrays, but for vertex groups we need 1 dimension
        for name, vg_idx, vg_weights in rigging.vg_read(vg_data):
            self.tmp_buf.fill(0)
            self.tmp_buf.put(vg_idx, vg_weights)
            yield name, calc_fit(self.tmp_buf, positions, fit_idx, fit_weights)

    def transfer_weights_get(self, asset, vg_data, cutoff=1e-4):
        for name, weights in self._transfer_weights_iter_arrays(asset, vg_data):
            idx = (weights > cutoff).nonzero()[0]
            if len(idx) > 0:
                yield name, idx, weights[idx]

    def transfer_weights(self, asset, vg_data):
        rigging.import_vg(asset, self.transfer_weights_get(asset, vg_data),
            bpy.context.window_manager.charmorph_ui.fitting_weights_ovr)

def obj_faces(obj):
    return [f.vertices for f in obj.data.polygons]

class ObjFitCalculator(FitCalculator):
    @utils.lazyproperty
    def verts(self):
        return self.get_basis(self.obj)

    @utils.lazyproperty
    def faces(self):
        return obj_faces(self.obj)

    @utils.lazyproperty
    def subset_faces(self):
        return self.faces

    @utils.lazyproperty
    def subset_bvh(self):
        return self.bvh

class MorpherFitCalculator(FitCalculator):
    def __init__(self, morpher: morphers.Morpher, get_basis):
        super().__init__(morpher.obj, get_basis)
        self.morpher = morpher
        self.char = morpher.char
        self.subset = self.char.fitting_subset
        self.alt_topo = self.morpher.alt_topo

    @utils.lazyproperty
    def verts(self):
        return self.morpher.full_basis

    @utils.lazyproperty
    def faces(self):
        return self.char.faces if self.char.faces is not None else obj_faces(self.obj)

    def subset_verts_cnt(self):
        return len(self.verts) if self.subset is None else len(self.subset["verts"])
    def subset_verts_enum(self):
        return enumerate(self.verts) if self.subset is None else ((i, self.verts[i]) for i in self.subset["verts"])

    @utils.lazyproperty
    def subset_faces(self):
        if self.subset is None:
            return self.faces
        faces = self.faces
        return [faces[i] for i in self.subset["faces"]]

    @utils.lazyproperty
    def subset_bvh(self):
        if self.subset is None:
            return self.bvh
        return mathutils.bvhtree.BVHTree.FromPolygons(self.verts, self.subset_faces)

repsilon = 1e-5

class RiggerFitCalculator(MorpherFitCalculator):
    def __init__(self, morpher):
        super().__init__(morpher, lambda data: morphers.get_basis(data, morpher))
        self.subset = None

    # when transferring joints to another geometry, we need to make sure
    # that every original vertex will be mapped to new topology
    def _calc_weights_reverse(self, weights, asset):
        verts = self.get_verts(asset)
        bvh = self.get_bvh(asset)
        for i, cvert in enumerate(self.verts):
            loc, _, idx, fdist = bvh.find_nearest(cvert)
            if idx is None:
                continue
            face = asset.data.polygons[idx].vertices
            fdist = max(fdist ** 2, repsilon)
            for vi, bw in zip(face, mathutils.interpolate.poly_3d_calc([verts[i] for i in face], loc)):
                d = weights[vi]
                d[i] = d.get(i, 0) + bw/fdist

    def _calc_weights_kd_reverse(self, weights, verts):
        kd = utils.kdtree_from_verts(verts)
        for i, vert in enumerate(self.verts):
            for _, vi, dist in kd.find_n(vert, 4):
                d = weights[vi]
                d[i] = d.get(i, 0) + 1/max(dist**2, repsilon)

    def get_weights(self, asset):
        t = utils.Timer()
        verts = self.get_verts(asset)
        # calculate weights based on nearest vertices
        weights = calc_weights_kd(utils.kdtree_from_verts_enum(((idx, vert) for idx, vert in enumerate(self.verts)), len(self.verts)),
             verts, repsilon, 16)
        self._calc_weights_kd_reverse(weights, verts)
        self._calc_weights_reverse(weights, asset)
        result = weights_convert(weights, False)
        t.time("rigger calc time")
        return result

class ReverseFitCalculator(MorpherFitCalculator):
    def __init__(self, morpher):
        super().__init__(morpher, utils.get_basis_numpy)
        self.alt_topo = True

    @utils.lazyproperty
    def verts(self):
        return self.morpher.get_final()

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

class Fitter(MorpherFitCalculator):
    children: list = None
    _lock_cm = False
    transfer_calc: ObjFitCalculator = None
    diff_arr: numpy.ndarray = None

    def __init__(self, morpher):
        super().__init__(morpher, lambda data: morphers.get_basis(data, morpher))
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

    def get_diff_arr(self):
        if self.diff_arr is None:
            self.diff_arr = self.morpher.get_diff()
        return self.diff_arr

    def calc_fit(self, weights_tuple, morph = None):
        diff_arr = self.get_diff_arr()
        if morph is not None:
            diff_arr = diff_arr.copy()
            diff_arr[morph[0]] -= morph[1]
        return calc_fit(diff_arr, *weights_tuple)

    def get_hair_data(self, psys):
        if not psys.is_edited:
            return None
        fit_id = psys.settings.get("charmorph_fit_id")
        if fit_id:
            data = self.weights_cache.get(fit_id)
            if data:
                return data

        z = self.char.get_np(f"hairstyles/{psys.settings.get('charmorph_hairstyle','')}.npz")
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
        self.transfer_weights(asset, rigging.char_weights_npz(self.obj, self.char))

    def _transfer_weights_obj(self, asset, vgs):
        if asset is self.obj:
            raise Exception("Tried to self-transfer weights")
        if self.alt_topo:
            if self.transfer_calc is None:
                self.transfer_calc = ObjFitCalculator(self.obj, self.get_basis, self)
            calc = self.transfer_calc
        else:
            calc = self
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
        self.tmp_buf = None
        self.transfer_calc = None

    def get_target(self, asset):
        return utils.get_target(asset) if asset is self.obj else get_fitting_shapekey(asset)

    def fit(self, asset):
        t = utils.Timer()

        self.char.assets.get(asset.data.get("charmorph_asset"))
        verts = self.calc_fit(self.get_weights(asset), )
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

        self.fit(asset)

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
