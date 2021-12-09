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
# Copyright (C) 2021 Michael Vigovsky

import mathutils # pylint: disable=import-error

from . import rigging, utils

def closest_point_on_face(face, co):
    if len(face) == 3:
        return mathutils.geometry.closest_point_on_tri(co, face[0], face[1], face[2])
    results = []
    for _ in range(len(face)):
        results.append(mathutils.geometry.closest_point_on_tri(co, face[0], face[1], face[2]))
        face = face[1:]+face[:1]
    results.sort(key=lambda elem: (elem-co).length)
    return (results[0]+results[1])/2

def dist_edge(co, v1, v2):
    co2, dist = mathutils.geometry.intersect_point_line(co, v1, v2)
    if dist <= 0:
        return (v1-co).length
    if dist >= 1:
        return (v2-co).length
    return (co2-co).length

def barycentric_weight_calc(veclist, co):
    result = mathutils.interpolate.poly_3d_calc(veclist, co)
    if sum(result)<0.5:
        return [1] * len(result)
    return result

def vg_full_to_avg(group):
    if group is None:
        return None
    total = 0
    vec = mathutils.Vector()
    for co, _, weight in group:
        total += weight
        vec += co*weight
    if total < 0.1:
        return None
    return vec / total

def vg_full_to_dict(group):
    return {tup[1]:tup[2] for tup in group}

def vg_mult(vg, coeff):
    for idx, weight in vg.items():
        vg[idx] = weight*coeff

def vg_add(a, b, coeff=1):
    if isinstance(b, dict):
        b = b.items()
    for idx, weight in b:
        a[idx] = a.get(idx, 0)+weight*coeff
    return a

def vg_mix(groups):
    groups = [(group, gweight/gsum) for group, gweight, gsum in ((group, gweight, sum(group.values())) for group, gweight in groups) if gsum>=1e-30]
    if len(groups) == 0:
        return "No groups were found by the calculation method"
    result = groups[0][0]
    if len(groups) > 1:
        vg_mult(result, groups[0][1])
    for group, coeff in groups[1:]:
        vg_add(result, group, coeff)
    return result

def get_offs(bone, attr):
    offs = bone.get("charmorph_offs_" + attr)
    if hasattr(offs, "__len__") and len(offs)==3:
        return mathutils.Vector(offs)
    return mathutils.Vector()

def overwrite_vg(vertex_groups, name):
    if name in vertex_groups:
        vertex_groups.remove(vertex_groups[name])
    return vertex_groups.new(name=name)

def calc_lst(co, lst):
    if lst is None or len(lst) == 0:
        return "No vertices were found by the calc method"
    return dict(zip([tup[1] for tup in lst], barycentric_weight_calc([tup[0] for tup in lst], co)))

def lazyprop(fn):
    attr_name = '_lazy_' + fn.__name__
    @property
    def _lazyprop(self):
        if not hasattr(self, attr_name):
            setattr(self, attr_name, fn(self))
        return getattr(self, attr_name)
    return _lazyprop

# pylint doesn't understand lazy properties so disable these errors for class
# pylint: disable=no-member
class VGCalculator:
    def __init__(self, char, ui):
        self.char = char
        self.ui = ui
        self.errors = []

        self.cur_bone = None
        self.cur_attr = ""
        self.cur_name = ""

        self.kdj_groups = None

    @lazyprop
    def vg_full(self):
        return rigging.get_vg_data(self.char, lambda: [], lambda data_item, v, co, gw: data_item.append((co, v.index, gw.weight)))

    @lazyprop
    def vg_avg(self):
        return rigging.get_vg_avg(self.char)

    @lazyprop
    def kd_verts(self):
        return utils.kdtree_from_verts(self.char.data.vertices)

    @lazyprop
    def bvh(self):
        return mathutils.bvhtree.BVHTree.FromPolygons([v.co for v in self.char.data.vertices], [f.vertices for f in self.char.data.polygons])

    @lazyprop
    def emap(self):
        result = {}
        for edge in self.char.data.edges:
            for vert in edge.vertices:
                item = result.get(vert)
                if item is None:
                    item = []
                    result[vert] = item
                item.append(edge.index)
        return result

    @lazyprop
    def kd_joints(self):
        all_groups = self.vg_full
        kd = mathutils.kdtree.KDTree(len(all_groups))
        self.kdj_groups = []
        for name, group in all_groups.items():
            co = vg_full_to_avg(group)
            if co is not None:
                kd.insert(co, len(self.kdj_groups))
                self.kdj_groups.append((name, group))
        kd.balance()
        return kd

    # Calc functions

    def calc_bb(self, co):
        lst = [(None, None, None) for _ in range(8)]
        for idx, vert in enumerate(self.char.data.vertices):
            vco = vert.co
            for bv in range(8):
                for coord in range(3):
                    if bv>>coord&1 != (1 if vco[coord] > co[coord] else 0):
                        break
                else:
                    dist = sum(abs(vco[coord]-co[coord]) for coord in range(3))
                    if lst[bv][2] is None or lst[bv][2] > dist:
                        lst[bv] = (vco, idx, dist)
                    break
        for item in lst:
            if item[0] is None:
                return "Not all bbox points was found"

        front_face = [item[0] for item in lst[:4]]
        back_face = [item[0] for item in lst[4:]]

        front_face[2], front_face[3] = front_face[3], front_face[2]
        back_face[2], back_face[3] = back_face[3], back_face[2]

        weights_front = barycentric_weight_calc(front_face, closest_point_on_face(front_face, co))
        weights_back = barycentric_weight_calc(back_face, closest_point_on_face(back_face, co))

        avg_front = sum((co*weight for co, weight in zip(front_face, weights_front)), mathutils.Vector())
        avg_back = sum((co*weight for co, weight in zip(back_face, weights_back)), mathutils.Vector())
        axis = avg_back-avg_front
        offs = min(max((co-avg_front).dot(axis)/axis.dot(axis), 0), 1)

        weights_front[2], weights_front[3] = weights_front[3], weights_front[2]
        weights_back[2], weights_back[3] = weights_back[3], weights_back[2]

        weights = [w*(1-offs) for w in weights_front]+[w*offs for w in weights_back]

        return {item[1]:weight for item, weight in zip(lst, weights)}

    def calc_cu(self, co):
        lst = self.vg_full.get(self.cur_name)
        if lst is None or len(lst) == 0:
            return "No vertices in current group"

        if len(lst) > 256:
            return "Too many vertices in current group"

        slst = [lst[0]]
        co1 = lst[0][0]
        lst = lst[1:]
        while len(lst) > 0:
            idx = 0
            dist = (lst[0][0]-co1).length
            for i, item in enumerate(lst[1:]):
                dist2 = (item[0]-co1).length
                if dist2 < dist:
                    idx = i + 1
                    dist = dist2
            slst.append(lst[idx])
            co1 = lst[idx][0]
            del lst[idx]

        return calc_lst(co, slst)

    def calc_ne(self, co):
        verts = self.char.data.vertices
        edges = self.char.data.edges
        lst = None
        mindist = 1e30
        for _, vert, _ in self.kd_verts.find_n(co, 4):
            for edge in self.emap[vert]:
                v1, v2 = tuple((verts[i].co, i) for i in edges[edge].vertices)
                dist = dist_edge(co, v1[0], v2[0])
                if dist < mindist:
                    mindist = dist
                    lst = [v1, v2]

        return calc_lst(co, lst)

    def calc_face(self, co, idx):
        if idx is None:
            return "Face not found"
        return calc_lst(co, [(self.char.data.vertices[i].co, i) for i in self.char.data.polygons[idx].vertices])

    def calc_nf(self, co):
        return self.calc_face(co, self.bvh.find_nearest(co)[2])

    def calc_np(self, co):
        return calc_lst(co, self.kd_verts.find_n(co, self.ui.rig_vg_n))
    def calc_nr(self, co):
        return calc_lst(co, self.kd_verts.find_range(co, self.ui.rig_vg_radius))

    def calc_xl(self, co):
        verts = self.kd_verts.find_n(co, self.ui.rig_vg_xl_vn)
        lns = []
        for i in range(len(verts)-1):
            for j in range(i+1, len(verts)):
                co1 = verts[i][0]
                co2 = verts[j][0]
                co3, p = mathutils.geometry.intersect_point_line(co, co1, co2)
                if p < 0 or p > 1:
                    continue
                d = (co3-co).length
                if d < (co2-co1).length/2:
                    lns.append((verts[i][1], verts[j][1], d, p))

        lns.sort(key=lambda tup:tup[2])
        lns = lns[:self.ui.rig_vg_xl_n]
        if len(lns) == 0:
            return "No cross lines found"

        return vg_add({}, (tup for i, j, _, p in lns for tup in ((i, 1-p), (j, p))))

    def calc_nc(self, co):
        vgroups = self.vg_full
        def get_head(bone):
            if bone is None:
                return None
            result = vgroups.get("joint_" + bone.name + "_head")
            if result is not None:
                return result
            if bone.parent is None:
                return None
            return vgroups.get("joint_" + bone.parent.name + "_tail")

        bone = self.cur_bone
        if self.cur_attr == "head":
            groups = [get_head(bone.parent), vgroups.get("joint_%s_tail" % bone.name)]
        else:
            groups = [get_head(bone)] + [vgroups.get("joint_%s_tail" % child.name) for child in bone.children]

        groups = (g for g in groups if g is not None)
        if self.ui.rig_vg_calc == "NW":
            groups2 = []
            coords = []
            for g in groups:
                co2 = vg_full_to_avg(g)
                if co2 is not None:
                    groups2.append(g)
                    coords.append(co2)
            groups = [(vg_full_to_dict(g), weight) for g, weight in zip(groups2, barycentric_weight_calc(coords, co))]
        else:
            groups = [(vg_full_to_dict(g), 1) for g in groups]
        if len(groups) < 2:
            return "Can't find enough already calculated neighbors"
        return vg_mix(groups)

    def calc_nw(self, co):
        return self.calc_nc(co)

    def calc_nj(self, co):
        cur_groups = []
        coords = []
        for co2, idx, _ in sorted(self.kd_joints.find_n(co, self.ui.rig_vg_n+1), key=lambda tup: tup[2]):
            name, group = self.kdj_groups[idx]
            if name == self.cur_name:
                continue
            cur_groups.append(vg_full_to_dict(group))
            coords.append(co2)
            if len(cur_groups) >= self.ui.rig_vg_n:
                break
        return vg_mix(zip(cur_groups, barycentric_weight_calc(coords, co)))

    def run(self, joints):
        if self.ui.rig_widgets:
            joints = {name:tup for name, tup in joints.items() if tup[2] == "head"}
            offsets = {k:v[1].tail-v[1].head for k,v in joints.items()}

        char = self.char
        verts = char.data.vertices

        calc_func = "calc_" + self.ui.rig_vg_calc.lower()
        if not hasattr(self, calc_func):
            return "Invalid calc func"
        calc_func = getattr(self, calc_func)

        for name, (co, bone, attr) in joints.items():
            self.cur_name = name
            self.cur_bone = bone
            self.cur_attr = attr

            co1 = co
            if self.ui.rig_vg_offs == "S":
                co1 -= get_offs(bone, attr)

            vg_data = calc_func(co)
            if isinstance(vg_data, str):
                return name + ": " + vg_data

            coeff = max(vg_data.values())
            if coeff < 1e-30:
                return name + ": empty vg returned"

            coeff = 1/coeff
            co2 = mathutils.Vector()
            wsum = 0.0

            vg = overwrite_vg(char.vertex_groups, name)
            if self.ui.rig_widgets:
                vgt = overwrite_vg(char.vertex_groups, "joint_" + bone.name + "_tail")

            for idx, weight in vg_data.items():
                weight *= coeff
                vg.add([idx], weight, 'REPLACE')
                if self.ui.rig_widgets:
                    vgt.add([idx], weight, 'REPLACE')
                co2 += verts[idx].co * weight
                wsum += weight

            co2 /= wsum

            k = "charmorph_offs_"+attr
            if self.ui.rig_vg_offs == "R":
                offs = co-co2
                if offs.length >= self.ui.rig_vg_snap:
                    bone[k] = list(offs)
                elif k in bone:
                    del bone[k]
            elif self.ui.rig_vg_offs == "C":
                if k in bone:
                    del bone[k]

            if self.ui.rig_widgets:
                bone["charmorph_offs_tail"] = get_offs(bone, "head") + offsets.get(name, mathutils.Vector())

        return True
