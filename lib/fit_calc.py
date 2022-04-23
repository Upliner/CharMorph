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

import typing, logging, numpy

import bpy, mathutils # pylint: disable=import-error

from . import charlib, utils

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

def calc_fit(arr: numpy.ndarray, positions, idx, weights) -> numpy.ndarray:
    return numpy.add.reduceat(arr[idx] * weights, positions)

def mesh_faces(mesh):
    return [list(f.vertices) for f in mesh.polygons]

class BaseGeometry:
    verts: numpy.ndarray
    faces: list
    verts_enum: typing.Callable[[], typing.Iterable[tuple[int, list[int]]]]
    verts_cnt: typing.Callable[[], int]

    @utils.lazyproperty
    def kd(self):
        return utils.kdtree_from_verts_enum(self.verts_enum(), self.verts_cnt())
    @utils.lazyproperty
    def bvh(self):
        return mathutils.bvhtree.BVHTree.FromPolygons(self.verts, self.faces)
    @utils.lazyproperty
    def bbox(self):
        return self.verts.min(axis=0), self.verts.max(axis=0)

class Geometry(BaseGeometry):
    def __init__(self, mesh):
        self.mesh = mesh

    @staticmethod
    def get_basis(mesh):
        return charlib.get_basis(mesh, None, False)
    @utils.lazyproperty
    def verts(self):
        return self.get_basis(self.mesh)
    @utils.lazyproperty
    def faces(self):
        return mesh_faces(self.mesh)

    def verts_cnt(self):
        return len(self.verts)
    def verts_enum(self):
        return enumerate(self.verts)

class FitCalculator(Geometry):
    tmp_buf: numpy.ndarray = None

    def __init__(self, mesh, parent=None):
        super().__init__(mesh)
        self.geom_cache = {} if parent is None else parent.geom_cache

    def get_char_geom(self, _asset):
        return self

    def get_asset_geom(self, data):
        if isinstance(data, bpy.types.Object):
            data = data.data
        if data is self.mesh:
            return self
        key = data.get("charmorph_fit_id", data.name)
        result = self.geom_cache.get(key)
        if result is None:
            result = Geometry(data)
            self.geom_cache[data] = result
        return result

    # calculate weights based on distance from asset vertices to character faces
    @staticmethod
    def _calc_weights_direct(char_geom, weights, asset_verts):
        verts = char_geom.verts
        faces = char_geom.faces
        bvh = char_geom.bvh
        for i, v in enumerate(asset_verts):
            loc, _, idx, fdist = bvh.find_nearest(v.tolist(), dist_thresh)
            if loc is None:
                continue
            face = faces[idx]
            d = weights[i]
            fdist = max(fdist ** 2, epsilon)
            for vi, bw in zip(face, mathutils.interpolate.poly_3d_calc(verts[face].tolist(), loc)):
                d[vi] = max(d.get(vi, 0), bw/fdist)

    # calculate weights based on distance from character vertices to assset faces
    def _calc_weights_reverse(self, char_geom, weights, asset):
        asset_geom = self.get_asset_geom(asset)
        verts = asset_geom.verts
        faces = asset_geom.faces
        bvh = asset_geom.bvh
        for i, cvert in char_geom.verts_enum():
            loc, _, idx, fdist = bvh.find_nearest(cvert.tolist(), dist_thresh)
            if idx is None:
                continue
            face = faces[idx]
            fdist = max(fdist ** 2, 1e-15) # using lower epsilon to avoid some artifacts
            for vi, bw in zip(face, mathutils.interpolate.poly_3d_calc([verts[i] for i in face], loc)):
                d = weights[vi]
                d[i] = max(d.get(i, 0), bw/fdist)

    def _calc_weights_internal(self, asset_verts, asset=None):
        t = utils.Timer()
        cg = self.get_char_geom(asset)
        weights = calc_weights_kd(cg.kd, asset_verts, epsilon, 16)
        t.time("kdtree")
        self._calc_weights_direct(cg, weights, asset_verts)
        t.time("bvh direct")
        if asset:
            self._calc_weights_reverse(cg, weights, asset)
            t.time("bvh reverse")
        positions, idx, wresult = weights_convert(weights)
        t.time("convert")
        weights_normalize(positions, wresult)
        t.time("normalize")
        return positions, idx, wresult.reshape(-1,1)

    def get_weights(self, asset):
        return self._calc_weights_internal(self.get_asset_geom(asset).verts, asset)

    def calc_weights_hair(self, arr):
        return self._calc_weights_internal(arr)

    def _transfer_weights_iter_arrays(self, asset, vg_data):
        if self.tmp_buf is None:
            self.tmp_buf = numpy.empty((len(self.verts)))
        positions, fit_idx, fit_weights = self.get_weights(asset)
        fit_weights = fit_weights.reshape(-1) # While fitting we reduce 2D arrays, but for vertex groups we need 1 dimension
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
        utils.import_vg(asset, self.transfer_weights_get(asset, vg_data),
            bpy.context.window_manager.charmorph_ui.fitting_weights_ovr)

class MorpherGeometry(Geometry):
    def __init__(self, mcore):
        super().__init__(mcore.obj.data)
        self.mcore = mcore

    @utils.lazyproperty
    def verts(self):
        return self.mcore.full_basis

    @utils.lazyproperty
    def faces(self):
        return self.mcore.char.faces if self.mcore.char.faces is not None else mesh_faces(self.mesh)

class MorpherFinalGeometry(MorpherGeometry):
    @utils.lazyproperty
    def verts(self):
        return self.mcore.get_final()

class ChildGeometry(BaseGeometry):
    def __init__(self, parent):
        self.parent = parent
    def __getattr__(self, attr):
        return getattr(self.parent, attr)

class SubsetGeometry(ChildGeometry):
    def __init__(self, parent, subset):
        super().__init__(parent)
        self.subset = subset

    def verts_cnt(self):
        return len(self.subset["verts"])
    def verts_enum(self):
        return ((i, self.verts[i]) for i in self.subset["verts"])
    @utils.lazyproperty
    def subset_faces(self):
        if self.subset is None:
            return self.faces
        faces = self.faces
        return [faces[i] for i in self.subset["faces"]]

class MorphedGeometry(ChildGeometry):
    def __init__(self, parent, *morphs):
        super().__init__(parent)
        self.verts = self.verts.copy()
        for morph in morphs:
            morph.apply(self.verts)

class MorpherFitCalculator(FitCalculator):
    def __init__(self, mcore):
        super().__init__(mcore.obj.data)
        self.mcore = mcore
        self.geom = MorpherGeometry(mcore)
        subset = mcore.char.fitting_subset
        if subset is not None:
            self.geom = SubsetGeometry(self, self.geom)

    def get_basis(self, mesh):
        return charlib.get_basis(mesh, self.mcore)

    def _get_asset_conf(self, data):
        if isinstance(data, bpy.types.Object):
            data = data.data
        if not data:
            return charlib.Asset
        return self.mcore.char.assets.get(data.get("charmorph_asset"), charlib.Asset)

    def _get_asset_morph(self, asset):
        return self._get_asset_conf(asset).morph

    def get_char_geom(self, asset):
        morph = self._get_asset_morph(asset)
        if morph:
            return MorphedGeometry(self.geom, morph)
        return self.geom

repsilon = 1e-5

class RiggerFitCalculator(MorpherFitCalculator):
    def __init__(self, mcore):
        super().__init__(mcore)
        self.subset = None

    # when transferring joints to another geometry, we need to make sure
    # that every original vertex will be mapped to new topology
    # calculate weights based on distance from character vertices to assset faces
    def _calc_weights_reverse(self, char_geom, weights, asset): # pylint: disable=too-many-locals
        asset_geom = self.get_asset_geom(asset)
        for i, cvert in char_geom.verts_enum():
            loc, _, idx, fdist = asset_geom.bvh.find_nearest(cvert)
            if idx is None:
                continue
            face = asset_geom.faces[idx]
            fdist = max(fdist ** 2, repsilon)
            for vi, bw in zip(face, mathutils.interpolate.poly_3d_calc([asset_geom.verts[i] for i in face], loc)):
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
        verts = self.get_asset_geom(asset).verts
        # calculate weights based on nearest vertices
        weights = calc_weights_kd(utils.kdtree_from_verts_enum(((idx, vert) for idx, vert in enumerate(self.verts)), len(self.verts)),
             verts, repsilon, 16)
        self._calc_weights_kd_reverse(weights, verts)
        self._calc_weights_reverse(weights, self.verts, asset)
        result = weights_convert(weights, False)
        t.time("rigger calc time")
        return result

class ReverseFitCalculator(FitCalculator):
    def __init__(self, mcore):
        super().__init__(mcore.obj.data)
        self.mcore = mcore

    def get_char_geom(self, _asset):
        return MorpherFinalGeometry(self.mcore)
