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

from . import charlib, utils

logger = logging.getLogger(__name__)

dist_thresh = 0.1
epsilon = 1e-30


def calc_fit(arr: numpy.ndarray, positions, idx, weights) -> numpy.ndarray:
    return numpy.add.reduceat(arr[idx] * weights, positions)


def weights_convert(weights, cut=True):
    positions = numpy.empty((len(weights)), dtype=numpy.uint32)
    pos = 0
    idx = []
    wresult = []
    thresh = 0
    for i, d in enumerate(weights):
        if cut:
            thresh = max(d.values()) / 32
        positions[i] = pos
        for k, v in d.items():
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
    cnt[-1] = len(wresult) - positions[-1]
    wresult /= numpy.add.reduceat(wresult, positions).repeat(cnt)


# calculate weights based on distance from asset vertices to character faces
def calc_weights_direct(weights, char_geom, asset_verts):
    verts = char_geom.verts
    faces = char_geom.faces
    bvh = char_geom.bvh
    for i, v in enumerate(asset_verts):
        loc, _, idx, fdist = bvh.find_nearest(v.tolist(), dist_thresh)
        if loc is None:
            continue
        face = faces[idx]
        d = weights[i]
        fdist = (1 - fdist / dist_thresh) / max(fdist, epsilon)
        for vi, bw in zip(face, mathutils.interpolate.poly_3d_calc(verts[face].tolist(), loc)):
            d[vi] = max(d.get(vi, 0), bw * fdist)


# calculate weights based on distance from character vertices to assset faces
def calc_weights_reverse(weights, char_geom, asset_geom, reduce_func=max):
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
            d = weights[vi]
            d[i] = reduce_func(d.get(i, 0), bw * fdist)


# calculate weights based on nearest vertices
def calc_weights_kd(kd, verts, _epsilon, n):
    result = []
    for v in verts:
        pdata = kd.find_n(v, n)
        maxdist = max([p[2] for p in pdata])
        result.append({idx: (1 - (dist / maxdist)) / (max(dist ** 2, _epsilon)) for _, idx, dist in pdata})
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


def geom_morph(geom: Geometry, *morphs):
    result = geom.copy()
    result.verts = result.verts.copy()
    for morph in morphs:
        morph.apply(result.verts)
    return result


class FitCalculator:
    tmp_buf: numpy.ndarray = None
    geom_cache: dict[str, Geometry]

    def __init__(self, geom: Geometry, parent: "FitCalculator" = None):
        self.geom = geom
        self.geom_cache = {} if parent is None else parent.geom_cache

    def get_char_geom(self, _asset):
        return self.geom

    def get_asset_geom(self, data) -> Geometry:
        if isinstance(data, bpy.types.Object):
            data = data.data
        key = data.get("charmorph_fit_id", data.name)
        result = self.geom_cache.get(key)
        if result is None:
            result = geom_mesh(data)
            self.geom_cache[data] = result
        return result

    def _calc_weights_internal(self, asset_verts, asset=None):
        t = utils.Timer()
        cg = self.get_char_geom(asset)
        weights = calc_weights_kd(cg.kd, asset_verts, epsilon, 16)
        t.time("kdtree")
        calc_weights_direct(weights, cg, asset_verts)
        t.time("bvh direct")
        if asset:
            calc_weights_reverse(weights, cg, self.get_asset_geom(asset))
            t.time("bvh reverse")
        positions, idx, wresult = weights_convert(weights)
        weights_normalize(positions, wresult)
        t.time("finalize")
        return positions, idx, wresult.reshape(-1, 1)

    def get_weights(self, asset):
        return self._calc_weights_internal(self.get_asset_geom(asset).verts, asset)

    def calc_weights_hair(self, arr):
        return self._calc_weights_internal(arr)

    def _transfer_weights_iter_arrays(self, asset, vg_data):
        if self.tmp_buf is None:
            self.tmp_buf = numpy.empty(len(self.geom.verts))
        positions, fit_idx, fit_weights = self.get_weights(asset)
        # Reshepe is needed because vertex arrays are 2D and weight arrays are 1D
        fit_weights = fit_weights.reshape(-1)
        for name, vg_idx, vg_weights in utils.vg_read(vg_data):
            self.tmp_buf.fill(0)
            self.tmp_buf.put(vg_idx, vg_weights)
            yield name, calc_fit(self.tmp_buf, positions, fit_idx, fit_weights)

    def transfer_weights_get(self, asset, vg_data, cutoff=1e-4):
        for name, weights in self._transfer_weights_iter_arrays(asset, vg_data):
            idx = (weights > cutoff).nonzero()[0]
            if len(idx) > 0:
                yield name, idx, weights[idx]

    def transfer_weights(self, asset, vg_data):
        utils.import_vg(
            asset, self.transfer_weights_get(asset, vg_data),
            bpy.context.window_manager.charmorph_ui.fitting_weights_ovr)


class MorpherFitCalculator(FitCalculator):
    def __init__(self, mcore):
        self.mcore = mcore
        geom = geom_morpher(mcore)
        subset = mcore.char.fitting_subset
        if subset:
            geom = geom_subset(geom, subset)
        super().__init__(geom)

    def _get_asset_conf(self, data):
        if isinstance(data, bpy.types.Object):
            data = data.data
        if not data:
            return charlib.Asset
        return self.mcore.char.assets.get(data.get("charmorph_asset"), charlib.Asset)

    def _get_asset_morph(self, asset) -> Geometry:
        return self._get_asset_conf(asset).morph

    def get_char_geom(self, asset) -> Geometry:
        morph = self._get_asset_morph(asset)
        if morph:
            return geom_morph(self.geom, morph)
        return self.geom


repsilon = 1e-5


class RiggerFitCalculator(FitCalculator):
    def __init__(self, morpher):
        super().__init__(geom_morpher(morpher.core), morpher.fitter)

    # when transferring joints to another geometry, we need to make sure
    # that every original vertex will be mapped to new topology
    def _calc_weights_kd_reverse(self, weights, asset_verts):
        kd = utils.kdtree_from_verts(asset_verts)
        for i, vert in self.geom.verts_enum():
            for _, vi, dist in kd.find_n(vert, 4):
                d = weights[vi]
                d[i] = d.get(i, 0) + 1 / max(dist**2, repsilon)

    def get_weights(self, asset):
        t = utils.Timer()
        cg = self.get_char_geom(asset)
        verts = self.get_asset_geom(asset).verts
        # calculate weights based on nearest vertices
        weights = calc_weights_kd(cg.kd, verts, repsilon, 16)
        self._calc_weights_kd_reverse(weights, verts)
        calc_weights_reverse(weights, cg.verts, asset, lambda a, b: a + b)
        result = weights_convert(weights, False)
        t.time("rigger calc time")
        return result
