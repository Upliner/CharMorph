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

import logging
import bpy, mathutils # pylint: disable=import-error

from . import rigging, utils

logger = logging.getLogger(__name__)

def closest_point_on_face(face, co):
    if len(face) == 3:
        return mathutils.geometry.closest_point_on_tri(co, face[0], face[1], face[2])
    results = []
    for _ in range(len(face)):
        results.append(mathutils.geometry.closest_point_on_tri(co, face[0], face[1], face[2]))
        face = face[1:]+face[:1]
    results.sort(key=lambda elem: (elem-co).length)
    return (results[0]+results[1])/2

def recalc_bb(char, co, name):
    lst = [(None, None, None) for _ in range(8)]
    for idx, vert in enumerate(char.data.vertices):
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
            logger.error("Not all bbox points was found")
            return

    front_face = [item[0] for item in lst[:4]]
    back_face = [item[0] for item in lst[4:]]

    front_face[2], front_face[3] = front_face[3], front_face[2]
    back_face[2], back_face[3] = back_face[3], back_face[2]

    weights_front = mathutils.interpolate.poly_3d_calc(front_face, closest_point_on_face(front_face, co))
    weights_back = mathutils.interpolate.poly_3d_calc(back_face, closest_point_on_face(back_face, co))

    avg_front = sum((co*weight for co, weight in zip(front_face, weights_front)), mathutils.Vector())
    avg_back = sum((co*weight for co, weight in zip(back_face, weights_back)), mathutils.Vector())
    axis = avg_back-avg_front
    offs = min(max((co-avg_front).dot(axis)/axis.dot(axis), 0), 1)

    weights_front[2], weights_front[3] = weights_front[3], weights_front[2]
    weights_back[2], weights_back[3] = weights_back[3], weights_back[2]

    weights = [w*(1-offs) for w in weights_front]+[w*offs for w in weights_back]

    if name in char.vertex_groups:
        char.vertex_groups.remove(char.vertex_groups[name])
    vg = char.vertex_groups.new(name=name)
    for item, weight in zip(lst, weights):
        vg.add([item[1]], weight, 'REPLACE')

def recalc_cu(vg, lst, co):
    if len(lst) == 0:
        logger.error("No points")
        return False
    weights = mathutils.interpolate.poly_3d_calc([item[0] for item in lst], co)
    coeff = max(weights)
    if coeff < 1e-30:
        logger.error("Bad coeff")
        return False
    coeff = 1/coeff
    for weight, item in zip(weights, lst):
        vg.add([item[1]], weight*coeff, 'REPLACE')
    return True

def recalc_lst(char, co, name, lst):
    if name in char.vertex_groups:
        char.vertex_groups.remove(char.vertex_groups[name])
    vg = char.vertex_groups.new(name=name)
    vg.add([item[1] for item in lst], 1, 'REPLACE')
    if len(lst) == 1:
        return True
    return recalc_cu(vg, lst, co)

def calc_emap(char):
    result = {}
    for edge in char.data.edges:
        for vert in edge.vertices:
            item = result.get(vert)
            if item is None:
                item = []
                result[vert] = item
            item.append(edge.index)
    return result

def dist_edge(co, v1, v2):
    co2, dist = mathutils.geometry.intersect_point_line(co, v1, v2)
    if dist <= 0:
        return (v1-co).length
    if dist >= 1:
        return (v2-co).length
    return (co2-co).length

def dist_edge2(co, v1, v2):
    co2, dist = mathutils.geometry.intersect_point_line(co, v1, v2)
    if dist <= 0 or dist >= 1:
        return None
    return (co2-co).length

def recalc_ne(char, co, name, kd, emap):
    verts = char.data.vertices
    edges = char.data.edges
    lst = None
    mindist = 1e30
    for _, vert, _ in kd.find_n(co, 4):
        for edge in emap[vert]:
            v1, v2 = tuple((verts[i].co, i) for i in edges[edge].vertices)
            dist = dist_edge(co, v1[0], v2[0])
            if dist < mindist:
                mindist = dist
                lst = [v1, v2]
    if lst is None:
        logger.error("Edge not found")
        return False
    return recalc_lst(char, co, name, lst)

def recalc_nf(char, co, name, bvh):
    _, _, idx, _ = bvh.find_nearest(co)
    if idx is None:
        logger.error("Face not found")
        return False
    verts = char.data.vertices
    return recalc_lst(char, co, name, [(verts[i].co, i) for i in char.data.polygons[idx].vertices])

def recalc_np(char, co, name, kd, n):
    return recalc_lst(char, co, name, kd.find_n(co, n))
def recalc_nr(char, co, name, kd, radius):
    return recalc_lst(char, co, name, kd.find_range(co, radius))

def recalc_xl(char, co, name, kd, vn, n):
    verts = kd.find_n(co, vn)
    lns = []
    for i in range(len(verts)-1):
        for j in range(i+1, len(verts)):
            co1 = verts[i][0]
            co2 = verts[j][0]
            d = dist_edge2(co, co1, co2)
            if d is not None and d < (co2-co1).length/2:
                lns.append((i,j,d))

    lns.sort(key=lambda tup:tup[2])
    lns = lns[:n]
    if len(lns) == 0:
        logger.error("No cross lines found")
        return False
    m = {}
    for i, j, _ in lns:
        weights = mathutils.interpolate.poly_3d_calc([verts[i][0], verts[j][0]], co)
        m[i] = m.get(i, 0) + weights[0]
        m[j] = m.get(j, 0) + weights[1]

    coeff = 1/max(m.values())

    if name in char.vertex_groups:
        char.vertex_groups.remove(char.vertex_groups[name])
    vg = char.vertex_groups.new(name=name)
    for idx, weight in m.items():
        vg.add([verts[idx][1]], weight*coeff, 'REPLACE')

    return True

def get_vg_full(char):
    return rigging.get_vg_data(char, lambda: [], lambda data_item, v, co, gw: data_item.append((v.index, co, gw.weight)))

def recalc_othergroups(char, name, groups):
    if len(groups) == 0:
        return
    if len(groups) == 1:
        gw = [1]
        mx = 1
    else:
        gw = []
        mx = 1e-5
        for g, weight in groups:
            coeff = weight/sum(item[2] for item in g)
            gw.append(coeff)
            mx = max(mx, max(item[2]*coeff for item in g))
    if name in char.vertex_groups:
        char.vertex_groups.remove(char.vertex_groups[name])
    vg = char.vertex_groups.new(name=name)
    for g, coeff in zip(groups, gw):
        for idx, _, weight in g[0]:
            vg.add([idx], weight*coeff/mx, 'REPLACE')

def full_to_avg(group):
    if group is None:
        return None
    total = 0
    vec = mathutils.Vector()
    for _, co, weight in group:
        total += weight
        vec += co*weight
    if total < 0.1:
        return None
    return vec / total


def barycentric_weight_calc(veclist, co):
    result = mathutils.interpolate.poly_3d_calc(veclist, co)
    if sum(result)<0.5:
        return [1] * len(result)
    return result

def recalc_nc(char, joints, weighted : bool):
    vgroups = get_vg_full(char)
    def get_head(bone):
        if bone is None:
            return None
        result = vgroups.get("joint_" + bone.name + "_head")
        if result is not None:
            return result
        if bone.parent is None:
            return None
        return vgroups.get("joint_" + bone.parent.name + "_tail")

    for name, (co, bone, attr) in joints.items():
        if attr == "head":
            groups = [get_head(bone.parent), vgroups.get("joint_%s_tail" % bone.name)]
        else:
            groups = [get_head(bone)] + [vgroups.get("joint_%s_tail" % child.name) for child in bone.children]

        groups = (g for g in groups if g is not None)
        if weighted:
            z = list(zip(*((g, co2) for g, co2 in ((g, full_to_avg(g)) for g in groups) if co2 is not None)))
            groups = list(zip(z[0], barycentric_weight_calc(z[1], co)))
        else:
            groups = [(g, 1) for g in groups]
        if len(groups) > 1:
            recalc_othergroups(char, name, groups)

def recalc_nj(char, joints, n):
    all_groups = get_vg_full(char)
    kd = mathutils.kdtree.KDTree(len(all_groups))
    groups = []
    for name, group in all_groups.items():
        co = full_to_avg(group)
        if co is not None:
            kd.insert(co, len(groups))
            groups.append((name,group))
    kd.balance()
    for name, (co, _, _) in joints.items():
        cur_groups = []
        coords = []
        for co2, idx, _ in sorted(kd.find_n(co, n+1), key=lambda tup: tup[2]):
            name2, group = groups[idx]
            if name2 == name:
                continue
            cur_groups.append(group)
            coords.append(co2)
            if len(cur_groups) >= n:
                break
        recalc_othergroups(char, name, list(zip(cur_groups, barycentric_weight_calc(coords, co))))

def calc_vg(char, joints, ui):
    typ = ui.rig_vg_calc

    if typ in ("NC", "NW"):
        recalc_nc(char, joints, typ == "NW")
        return True
    if typ == "NJ":
        recalc_nj(char, joints, ui.rig_vg_n)
        return True

    result = True
    if typ == "CU":
        vgroups = rigging.get_vg_data(char, lambda: [], lambda data_item, v, co, gw: data_item.append((co, v.index)))
        for name, tup in joints.items():
            co = tup[0]
            vg = char.vertex_groups.get(name)
            if not vg:
                logger.error("%s doesn't have current vertex group", name)
                continue
            result &= recalc_cu(vg, vgroups.get(name, []), co)
    else:
        if typ in ("NP", "NR", "NE", "XL"):
            kd = utils.kdtree_from_verts(char.data.vertices)
            if typ == "NE":
                emap = calc_emap(char)
        elif typ == "NF":
            bvh = mathutils.bvhtree.BVHTree.FromPolygons([v.co for v in char.data.vertices], [f.vertices for f in char.data.polygons])

        for name, tup in joints.items():
            co = tup[0]
            if typ == "NP":
                result &= recalc_np(char, co, name, kd, ui.rig_vg_n)
            elif typ == "NR":
                result &= recalc_nr(char, co, name, kd, ui.rig_vg_radius)
            elif typ == "XL":
                result &= recalc_xl(char, co, name, kd, ui.rig_vg_xl_vn, ui.rig_vg_xl_n)
            elif typ == "NE":
                result &= recalc_ne(char, co, name, kd, emap)
            elif typ == "NF":
                result &= recalc_nf(char, co, name, bvh)
            elif typ == "BB":
                result &= recalc_bb(char, co, name)
            else:
                logger.error("Inavlid typ!")
                result = False
    return result

def do_calc(char, joints, ui):
    if ui.rig_widgets:
        joints = {name:tup for name,tup in joints.items() if tup[2] == "head"}
        offsets = [(tup[1],tup[1].tail-tup[1].head) for tup in joints.values()]

    if not calc_vg(char, joints, ui):
        return False

    if ui.rig_vg_offs == "R":
        avg = rigging.get_vg_avg(char)
        for name, (co, bone, attr) in joints.items():
            item = avg.get(name)
            if item:
                offs = co-(item[1]/item[0])
                k = "charmorph_offs_"+attr
                if offs.length > 0.0001:
                    bone[k] = list(offs)
                elif k in bone:
                    del bone[k]
            else:
                logger.error("Can't calculate offset for %s", name)
    elif ui.rig_vg_offs == "C":
        for _, bone, attr in joints.values():
            k = "charmorph_offs_"+attr
            if k in bone:
                del bone[k]

    if ui.rig_widgets:
        vgroups = get_vg_full(char)
        for bone, offs in offsets:
            grp = vgroups.get( "joint_" + bone.name + "_head")
            if grp is None:
                continue
            offs2 = bone.get("charmorph_offs_head")
            if isinstance(offs2, list) and len(offs2)==3:
                offs += mathutils.Vector(offs2)
            name = "joint_" + bone.name + "_tail"
            if name in char.vertex_groups:
                char.vertex_groups.remove(char.vertex_groups[name])
            vg = char.vertex_groups.new(name=name)
            for idx, co, weight in grp:
                vg.add([idx], weight, 'REPLACE')
            bone["charmorph_offs_tail"] = offs

    return True
