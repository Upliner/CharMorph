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

import logging, numpy

import bpy, mathutils  # pylint: disable=import-error

from . import charlib, morphs, utils

logger = logging.getLogger(__name__)

dist_thresh = 0.125
epsilon = 1e-30
epsilon2 = 1e-15


class FitBinding(tuple):
    __slots__ = ()

    def __new__(cls, *args):
        return super().__new__(cls, args)

    def fit(self, arr: numpy.ndarray, is_weights=False):
        for pos, idx, weights in self:
            if is_weights:
                weights = weights.reshape(-1)
            arr = numpy.add.reduceat(arr[idx] * weights, pos)
        return arr


def _binding_convert(bind_dict, cut=True):
    positions = numpy.empty((len(bind_dict)), dtype=numpy.uint32)
    idx = []
    weights = []
    thresh = 0
    for i, d in enumerate(bind_dict):
        if cut:
            thresh = max(d.values()) / 32
        positions[i] = len(idx)
        for k, v in d.items():
            if v >= thresh:
                idx.append(k)
                weights.append(v)
    idx = numpy.array(idx, dtype=numpy.uint32)
    weights = numpy.array(weights)
    return positions, idx, weights


def _binding_normalize(positions, wresult):
    cnt = numpy.empty((len(positions)), dtype=numpy.uint32)
    cnt[:-1] = positions[1:]
    cnt[:-1] -= positions[:-1]
    cnt[-1] = len(wresult) - positions[-1]
    wresult /= numpy.add.reduceat(wresult, positions).repeat(cnt)


# calculate binding based on distance from character vertices to assset faces
def _calc_binding_reverse(bind_dict, char_geom, asset_geom, reduce_func=max):
    verts = asset_geom.verts
    faces = asset_geom.faces
    bvh = asset_geom.bvh
    for i, cvert in char_geom.verts_enum():
        loc, _, idx, fdist = bvh.find_nearest(cvert.tolist(), dist_thresh)
        if idx is None:
            continue
        face = faces[idx]
        fdist = (1 - fdist / dist_thresh) / max(fdist, 1e-15)  # using lower epsilon to avoid some artifacts
        for vi, bw in zip(face, mathutils.interpolate.poly_3d_calc([verts[i] for i in face], loc)):
            d = bind_dict[vi]
            d[i] = reduce_func(d.get(i, 0), bw * fdist)


# calculate binding based on nearest vertices
def _calc_binding_kd(kd, verts, _epsilon, n):
    result = []
    for v in verts:
        pdata = kd.find_n(v, n)
        maxdist = max([p[2] for p in pdata])
        result.append({idx: (1 - (dist / maxdist)) / (max(dist, _epsilon)) for _, idx, dist in pdata})
    return result


class Geometry:
    def __init__(self, verts: numpy.ndarray, faces: list):
        self.verts = verts
        self.faces = faces

    def copy(self):
        return Geometry(self.verts, self.faces)

    def verts_cnt(self):
        return len(self.verts)

    def verts_enum(self):
        return enumerate(self.verts)

    def verts_filter_set(self, _vset):
        pass

    @utils.lazyproperty
    def kd(self):
        return utils.kdtree_from_verts_enum(self.verts_enum(), self.verts_cnt())

    @utils.lazyproperty
    def bvh(self):
        return mathutils.bvhtree.BVHTree.FromPolygons(self.verts, self.faces)

    @utils.lazyproperty
    def bbox(self):
        return self.verts.min(axis=0), self.verts.max(axis=0)


def mesh_faces(mesh):
    return [f.vertices for f in mesh.polygons]


def geom_mesh(mesh):
    return Geometry(charlib.get_basis(mesh, None, False), mesh_faces(mesh))


class SubsetGeometry(Geometry):
    def __init__(self, verts, faces, subset):
        super().__init__(verts, faces)
        self.subset = subset

    def copy(self):
        return SubsetGeometry(self.verts, self.faces, self.subset)

    def verts_cnt(self):
        return len(self.subset)

    def verts_enum(self):
        return ((i, self.verts[i]) for i in self.subset)

    def verts_filter_set(self, vset: set):
        vset.intersection_update(self.subset)


def morpher_faces(mcore):
    faces = mcore.char.faces
    return faces if faces is not None else mesh_faces(mcore.obj.data)


def geom_morpher(mcore):
    return Geometry(mcore.full_basis, morpher_faces(mcore))


def geom_morpher_final(mcore):
    return Geometry(mcore.get_final(), morpher_faces(mcore))


def geom_shapekey(mesh, sk):
    return Geometry(utils.verts_to_numpy(sk.data), mesh_faces(mesh))


def geom_subset(geom, subset):
    return SubsetGeometry(geom.verts, [geom.faces[i] for i in subset["faces"]], subset["verts"])


def geom_morph(geom: Geometry, *morph_list):
    result = geom.copy()
    result.verts = result.verts.copy()
    for morph in morph_list:
        morph.apply(result.verts)
    return result


class Binder:
    bindings: list[dict[int, float]]

    def __init__(self, char_geom: Geometry, asset_verts: numpy.ndarray):
        self.char_geom = char_geom
        self.asset_verts = asset_verts
        self.bindings = []
        self.dists_asset = []
        self.revset = set()

    # calculate binding based on distance from asset vertices to character faces
    def calc_binding_direct(self):
        verts = self.char_geom.verts
        faces = self.char_geom.faces
        bvh = self.char_geom.bvh
        for v in self.asset_verts:
            loc, _, idx, fdist = bvh.find_nearest(v.tolist())
            if loc is None:
                continue
            face = faces[idx]
            self.revset.update(face)
            self.dists_asset.append(fdist)
            fdist = 1 / max(fdist ** 2, epsilon)
            self.bindings.append({vi: bw*fdist
                for vi, bw in zip(face, mathutils.interpolate.poly_3d_calc(verts[face].tolist(), loc))})

    def calc_binding_kd(self):
        kd = self.char_geom.kd
        for v, fdist, binding in zip(self.asset_verts, self.dists_asset, self.bindings):
            if fdist > epsilon2:
                fdist = min(fdist * 1.5, fdist + dist_thresh)
                for _, idx, dist in kd.find_range(v, fdist):
                    self.revset.add(idx)
                    binding[idx] = max(binding.get(idx, 0), 1 / max(dist ** 2, epsilon))

    def calc_binding_reverse(self, asset_geom):
        self.char_geom.verts_filter_set(self.revset)
        dthresh = min(self.dists_asset)
        if dthresh < epsilon2:
            return
        cverts = self.char_geom.verts
        verts = asset_geom.verts
        faces = asset_geom.faces
        bvh = asset_geom.bvh
        for i in self.revset:
            loc, _, idx, fdist = bvh.find_nearest(cverts[i].tolist(), dthresh)
            if idx is None:
                continue
            face = faces[idx]
            coeff = (1 - fdist / dthresh) / max(fdist ** 2, epsilon2)
            for vi, bw in zip(face, mathutils.interpolate.poly_3d_calc(verts[face].tolist(), loc)):
                if self.dists_asset[vi] > fdist:
                    d = self.bindings[vi]
                    d[i] = max(d.get(i, 0), bw * coeff)


class AssetFitData(utils.ObjTracker):
    conf: charlib.Asset
    morph: morphs.Morph
    geom: Geometry
    binding: FitBinding

    def __init__(self, obj, geom=None):
        super().__init__(obj)
        self.conf = charlib.Asset
        self.morph = None
        if not geom:
            geom = geom_mesh(obj.data)
        self.geom = geom


def get_mesh(data):
    if isinstance(data, AssetFitData):
        data = data.obj
    if isinstance(data, bpy.types.Object):
        return data.data
    return data


class FitCalculator:
    tmp_buf: numpy.ndarray = None
    geom_cache: dict[str, Geometry]

    def __init__(self, geom: Geometry, parent: "FitCalculator" = None):
        self.geom = geom
        self.geom_cache = {} if parent is None else parent.geom_cache

    def get_char_geom(self, _):
        return self.geom

    def _cache_get(self, key, get_func):
        result = self.geom_cache.get(key)
        if result is None:
            result = get_func()
            self.geom_cache[key] = result
        return result

    def _get_asset_geom(self, data) -> Geometry:
        data = get_mesh(data)
        return self._cache_get("obj_" + data.get("charmorph_fit_id", data.name), lambda: geom_mesh(data))

    def _get_fold_geom(self, afd: AssetFitData) -> Geometry:
        def get_func():
            fold = afd.conf.fold
            return Geometry(fold.verts, fold.faces)
        return self._cache_get("fold_" + afd.conf.dirpath, get_func)

    def _add_asset_data(self, _asset):
        pass

    def _get_asset_data(self, obj):
        afd = AssetFitData(obj, self._get_asset_geom(obj))
        self._add_asset_data(afd)
        afd.binding = self.get_binding(afd)
        return afd

    def _calc_binding_internal(self, asset_verts, afd=None, asset_geom=None):
        t = utils.Timer()
        b = Binder(self.get_char_geom(afd), asset_verts)
        b.calc_binding_direct()
        t.time("bvh direct")
        b.calc_binding_kd()
        t.time("kd")
        if asset_geom:
            b.calc_binding_reverse(asset_geom)
            t.time("bvh reverse")
        positions, idx, wresult = _binding_convert(b.bindings)
        _binding_normalize(positions, wresult)
        t.time("finalize")
        return positions, idx, wresult.reshape(-1, 1)

    def get_binding(self, target) -> FitBinding:
        if not isinstance(target, AssetFitData):
            target = AssetFitData(target)
        fold = target.conf.fold
        geom = target.geom if fold is None else self._get_fold_geom(target)
        binding = self._calc_binding_internal(geom.verts, target, geom)
        return FitBinding(binding) if fold is None else FitBinding(
            binding, (fold.pos, fold.idx, fold.weights))

    def calc_binding_hair(self, arr):
        return FitBinding(self._calc_binding_internal(arr))

    def _transfer_weights_iter_arrays(self, binding: FitBinding, vg_data):
        if self.tmp_buf is None:
            self.tmp_buf = numpy.empty(len(self.geom.verts))
        for name, idx, weights in utils.vg_read(vg_data):
            self.tmp_buf.fill(0)
            self.tmp_buf.put(idx, weights)
            yield name, binding.fit(self.tmp_buf, True)

    def _transfer_weights_get(self, binding, vg_data, cutoff=1e-4):
        for name, weights in self._transfer_weights_iter_arrays(binding, vg_data):
            idx = (weights > cutoff).nonzero()[0]
            if len(idx) > 0:
                yield name, idx, weights[idx]

    def transfer_weights(self, target, vg_data):
        if not isinstance(target, AssetFitData):
            target = self._get_asset_data(target)
        utils.import_vg(
            target.obj, self._transfer_weights_get(target.binding, vg_data),
            bpy.context.window_manager.charmorph_ui.fitting_weights_ovr)


class MorpherFitCalculator(FitCalculator):
    def __init__(self, mcore):
        self.mcore = mcore
        geom = geom_morpher(mcore)
        subset = mcore.char.fitting_subset
        if subset:
            geom = geom_subset(geom, subset)
        super().__init__(geom)

    def _get_asset_conf(self, obj):
        if not obj:
            return charlib.Asset
        return self.mcore.char.assets.get(obj.data.get("charmorph_asset"), charlib.Asset)

    def _add_asset_data(self, afd):
        afd.conf = self._get_asset_conf(afd.obj)
        afd.morph = afd.conf.morph  # TODO: get morph from mcore

    def get_char_geom(self, afd: AssetFitData) -> Geometry:
        if afd.morph:
            return geom_morph(self.geom, afd.morph)
        return self.geom


repsilon = 1e-5


class RiggerFitCalculator(FitCalculator):
    def __init__(self, morpher):
        super().__init__(geom_morpher(morpher.core), morpher.fitter)

    # when transferring joints to another geometry, we need to make sure
    # that every original vertex will be mapped to new topology
    def _calc_binding_kd_reverse(self, weights, asset_verts):
        kd = utils.kdtree_from_verts(asset_verts)
        for i, vert in self.geom.verts_enum():
            for _, vi, dist in kd.find_n(vert, 4):
                d = weights[vi]
                d[i] = d.get(i, 0) + 1 / max(dist**2, repsilon)

    def get_binding(self, target: AssetFitData):
        t = utils.Timer()
        cg = self.get_char_geom(target)
        verts = target.geom.verts
        # calculate weights based on nearest vertices
        bind_dict = _calc_binding_kd(cg.kd, verts, repsilon, 16)
        self._calc_binding_kd_reverse(bind_dict, verts)
        _calc_binding_reverse(bind_dict, cg.verts, target.geom, lambda a, b: a + b)
        result = _binding_convert(bind_dict, False)
        t.time("rigger calc time")
        return result

    def transfer_weights_get(self, obj, vg_data, cutoff=1e-4):
        return self._transfer_weights_get(self._get_asset_data(obj), vg_data, cutoff)
