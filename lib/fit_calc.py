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

import numpy

import bpy, mathutils # pylint: disable=import-error

from . import rigging, utils

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

    @utils.lazyprop
    def subset_kd(self):
        return utils.kdtree_from_verts_enum(self.subset_verts_enum(), self.subset_verts_cnt())

    @utils.lazyprop
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
    @utils.lazyprop
    def verts(self):
        return self.get_basis(self.obj)

    @utils.lazyprop
    def faces(self):
        return obj_faces(self.obj)

    @utils.lazyprop
    def subset_faces(self):
        return self.faces

    @utils.lazyprop
    def subset_bvh(self):
        return self.bvh

class MorpherFitCalculator(FitCalculator):
    def __init__(self, morpher, get_basis):
        super().__init__(morpher.obj, get_basis)
        self.morpher = morpher
        self.char = morpher.char
        self.subset = self.char.fitting_subset
        self.alt_topo = self.morpher.alt_topo

    @utils.lazyprop
    def verts(self):
        return self.morpher.get_basis()

    @utils.lazyprop
    def faces(self):
        return self.char.faces if self.char.faces is not None else obj_faces(self.obj)

    def subset_verts_cnt(self):
        return len(self.verts) if self.subset is None else len(self.subset["verts"])
    def subset_verts_enum(self):
        return enumerate(self.verts) if self.subset is None else ((i, self.verts[i]) for i in self.subset["verts"])

    @utils.lazyprop
    def subset_faces(self):
        if self.subset is None:
            return self.faces
        faces = self.faces
        return [faces[i] for i in self.subset["faces"]]

    @utils.lazyprop
    def subset_bvh(self):
        if self.subset is None:
            return self.bvh
        return mathutils.bvhtree.BVHTree.FromPolygons(self.verts, self.subset_faces)

repsilon = 1e-5

class RiggerFitCalculator(MorpherFitCalculator):
    def __init__(self, morpher, get_basis):
        super().__init__(morpher, get_basis)
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

    @utils.lazyprop
    def verts(self):
        return self.morpher.get_final()
