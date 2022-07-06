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

import math
import bpy, mathutils  # pylint: disable=import-error

from ..lib import utils


def closest_point_on_face(face, co):
    if len(face) == 3:
        return mathutils.geometry.closest_point_on_tri(co, face[0], face[1], face[2])
    results = []
    for _ in range(len(face)):
        results.append(mathutils.geometry.closest_point_on_tri(co, face[0], face[1], face[2]))
        face = face[1:] + face[:1]
    results.sort(key=lambda elem: (elem - co).length)
    return (results[0] + results[1]) / 2


def dist_edge(co, v1, v2):
    co2, dist = mathutils.geometry.intersect_point_line(co, v1, v2)
    if dist <= 0:
        return (v1 - co).length
    if dist >= 1:
        return (v2 - co).length
    return (co2 - co).length


def barycentric_weight_calc(veclist, co):
    result = mathutils.interpolate.poly_3d_calc(veclist, co)
    if sum(result) < 0.5:
        return [1] * len(result)
    return result


def vg_full_to_avg(group):
    if group is None:
        return None
    total = 0
    vec = mathutils.Vector()
    for co, _, weight in group:
        total += weight
        vec += co * weight
    if total < 0.1:
        return None
    return vec / total


def vg_full_to_dict(group):
    return {tup[1]: tup[2] for tup in group}


def vg_mult(vg, coeff):
    for idx, weight in vg.items():
        vg[idx] = weight * coeff


def vg_add(a, b, coeff=1):
    if isinstance(b, dict):
        b = b.items()
    for idx, weight in b:
        a[idx] = a.get(idx, 0) + weight * coeff
    return a


def vg_mix2(a, b, factor):
    if factor < 1e-30:
        return a
    if factor >= 1:
        a.clear()
        a.update(b)
        return a
    vg_mult(a, (1 - factor) / sum(a.values()))
    return vg_add(a, b, factor / sum(b.values()))


def vg_mixmany(groups):
    groups = [
        (group, gweight / gsum)
        for group, gweight, gsum in (
            (group, gweight, sum(group.values()))
            for group, gweight in groups
        ) if gsum >= 1e-30
    ]
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
    if hasattr(offs, "__len__") and len(offs) == 3:
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


def calc_group_weights(groups, co):
    groups2 = []
    coords = []
    for g in groups:
        co2 = vg_full_to_avg(g)
        if co2 is not None:
            groups2.append(vg_full_to_dict(g))
            coords.append(co2)
    return list(zip(groups2, barycentric_weight_calc(coords, co)))


class VGCalculator:
    def __init__(self, rig, char, ui):
        self.rig = rig
        self.char = char
        self.ui = ui
        self.errors = []

        self.cur_bone = None
        self.cur_attr = ""
        self.cur_name = ""

        self.kdj_groups = None

        self.calc_lambdas = {
            "NP": lambda co: calc_lst(co, self.kd_verts.find_n(co, self.ui.vg_n)),
            "NR": lambda co: calc_lst(co, self.kd_verts.find_range(co, self.ui.vg_radius)),
            "NC": lambda co: self._calc_nc_nw(co, False),
            "NW": lambda co: self._calc_nc_nw(co, True),
            "RB": lambda co: self._calc_rays(co, self._rays_bone),
            "RG": lambda co: self._calc_rays(co, self._rays_global),
        }

    def get_calc_func(self, typ=None):
        if not typ:
            typ = self.ui.vg_calc
        return self.calc_lambdas.get(typ) or getattr(self, "calc_" + typ.lower())

    @utils.lazyproperty
    def vg_full(self):
        return utils.get_vg_data(
            self.char, lambda: [],
            lambda data_item, v, co, gw: data_item.append((co, v.index, gw.weight)))

    @utils.lazyproperty
    def vg_avg(self):
        return utils.get_vg_avg(self.char)

    @utils.lazyproperty
    def kd_verts(self):
        return utils.kdtree_from_verts(self.char.data.vertices)

    @utils.lazyproperty
    def bvh(self):
        return mathutils.bvhtree.BVHTree.FromPolygons(
            [v.co for v in self.char.data.vertices],
            [f.vertices for f in self.char.data.polygons])

    @utils.lazyproperty
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

    @utils.lazyproperty
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
                    if bv >> coord & 1 != (1 if vco[coord] > co[coord] else 0):
                        break
                else:
                    dist = sum(abs(vco[coord] - co[coord]) for coord in range(3))
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

        avg_front = sum((co * weight for co, weight in zip(front_face, weights_front)), mathutils.Vector())
        avg_back = sum((co * weight for co, weight in zip(back_face, weights_back)), mathutils.Vector())
        axis = avg_back - avg_front
        offs = min(max((co - avg_front).dot(axis) / axis.dot(axis), 0), 1)

        weights_front[2], weights_front[3] = weights_front[3], weights_front[2]
        weights_back[2], weights_back[3] = weights_back[3], weights_back[2]

        weights = [w * (1 - offs) for w in weights_front] + [w * offs for w in weights_back]

        return {item[1]: weight for item, weight in zip(lst, weights)}

    def calc_cu(self, co):
        lst = self.vg_full.get(self.cur_name)
        if lst is None or len(lst) == 0:
            return "No vertices in current group"

        if len(lst) > 256:
            return "Too many vertices in current group"

        # Solving travelling salesman problem by nearest neighbour algorithm
        slst = [lst[0]]
        co1 = lst[0][0]
        lst = lst[1:]
        while len(lst) > 0:
            idx = 0
            dist = (lst[0][0] - co1).length
            for i, item in enumerate(lst[1:]):
                dist2 = (item[0] - co1).length
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
        for _, vert, _ in self.kd_verts.find_n(co, 32):
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
        verts = [(self.char.data.vertices[i].co, i) for i in self.char.data.polygons[idx].vertices]
        if self.ui.vg_snap > 1e-30:
            for co2, idx1 in verts:
                if (co2 - co).length < self.ui.vg_snap:
                    return {idx1: 1}

            for i, (co2, idx2) in enumerate(verts):
                co1, idx1 = verts[i - 1]
                co3, p = mathutils.geometry.intersect_point_line(co, co1, co2)
                if (co3 - co).length < self.ui.vg_snap and 0 <= p <= 1:
                    return {idx1: 1 - p, idx2: p}

        return calc_lst(co, verts)

    def calc_nf(self, co):
        co2, _, idx, _ = self.bvh.find_nearest(co)
        return self.calc_face(co2, idx)

    def calc_xl(self, co):
        verts = self.kd_verts.find_n(co, self.ui.vg_xl_vn)
        lns = []
        for i in range(len(verts) - 1):
            for j in range(i + 1, len(verts)):
                co1 = verts[i][0]
                co2 = verts[j][0]
                co3, p = mathutils.geometry.intersect_point_line(co, co1, co2)
                if p < 0 or p > 1:
                    continue
                d = (co3 - co).length
                if d < (co2 - co1).length / 2:
                    lns.append((verts[i][1], verts[j][1], d, p))

        lns.sort(key=lambda tup: tup[2])
        lns = lns[:self.ui.vg_xl_n]
        if len(lns) == 0:
            return "No cross lines found"

        return vg_add({}, (tup for i, j, _, p in lns for tup in ((i, 1 - p), (j, p))))

    def _cast_rays(self, co, d):
        b = self.bvh
        co1, _, idx1, _ = b.ray_cast(co, d)
        co2, _, idx2, _ = b.ray_cast(co, -d)
        if idx1 is None or idx2 is None:
            return None
        _, p = mathutils.geometry.intersect_point_line(co, co1, co2)
        p = min(max(p, 0), 1)
        return vg_mix2(self.calc_face(co1, idx1), self.calc_face(co2, idx2), p)

    def _calc_rays(self, co, callback):
        if not self.ui.vg_x and not self.ui.vg_y and not self.ui.vg_z:
            return "No axes selected"
        result = {}

        def cast(d):
            vg = self._cast_rays(co, d)
            if vg is not None:
                vg_add(result, vg)

        callback(cast, co)

        if not result:
            return "Ray cast failed"

        return result

    def _rays_bone(self, cast, _):
        def cast_perp(axis, y):
            for i in range(self.ui.vg_rays):
                cast(mathutils.Quaternion(y, math.pi * i / self.ui.vg_rays + self.ui.vg_roll) @ axis)

        def cast_axes(x, y, z):
            if self.ui.vg_y:
                cast(y)
            if self.ui.vg_x:
                cast_perp(x, y)
            if self.ui.vg_z:
                cast_perp(z, y)

        def cast_bone(bone):
            cast_axes(bone.x_axis, bone.y_axis, bone.z_axis)

        children = self.cur_bone.children
        child = children[0] if len(children) == 1 else None

        if ((self.cur_attr == "head" and (self.cur_bone.parent is None or self.ui.vg_bone == "C"))
                or (self.cur_attr == "tail" and (child is None or self.ui.vg_bone == "P"))):
            cast_bone(self.cur_bone)
        elif self.ui.vg_bone == "P":
            cast_bone(self.cur_bone.parent)
        elif self.ui.vg_bone == "C":
            cast_bone(child)
        else:
            if self.cur_attr == "head":
                bone2 = self.cur_bone.parent
            else:
                bone2 = child
            if self.ui.vg_bone == "M":
                cast_bone(self.cur_bone)
                cast_bone(bone2)
            else:
                cast_axes(
                    self.cur_bone.x_axis + bone2.x_axis,
                    self.cur_bone.y_axis + bone2.y_axis,
                    self.cur_bone.z_axis + bone2.z_axis
                )

    def _rays_global(self, cast, co):
        xcoeff = 1
        if self.ui.vg_obj:
            mat = self.ui.vg_obj.matrix_world.to_3x3().transposed()
            if self.ui.vg_obj_mirror == "S":
                xcoeff = math.copysign(1, self.ui.vg_obj.location[0] * co[0])
            elif self.ui.vg_obj_mirror == "G":
                xcoeff = co[0] / self.ui.vg_obj.location[0]
        else:
            mat = mathutils.Matrix.Identity(3)

        def getvec(item):
            item = mathutils.Vector(item)
            item[0] *= xcoeff
            return item

        for i, axis in enumerate("xyz"):
            if getattr(self.ui,"vg_" + axis):
                cast(getvec(mat[i]))

    def _calc_nc_nw(self, co, is_nw: bool):
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
            groups = [get_head(bone.parent), vgroups.get(f"joint_{bone.name}_tail")]
        else:
            groups = [get_head(bone)] + [vgroups.get(f"joint_{child.name}_tail") for child in bone.children]

        groups = (g for g in groups if g is not None)
        if is_nw:
            groups = calc_group_weights(groups, co)
        else:
            groups = [(vg_full_to_dict(g), 1) for g in groups]
        if len(groups) < 2:
            return "Can't find enough already calculated neighbors"
        return vg_mixmany(groups)

    def calc_nj(self, co):
        cur_groups = []
        coords = []
        for co2, idx, _ in sorted(self.kd_joints.find_n(co, self.ui.vg_n + 1), key=lambda tup: tup[2]):
            name, group = self.kdj_groups[idx]
            if name == self.cur_name:
                continue
            cur_groups.append(vg_full_to_dict(group))
            coords.append(co2)
            if len(cur_groups) >= self.ui.vg_n:
                break
        return vg_mixmany(zip(cur_groups, barycentric_weight_calc(coords, co)))

    def calc_nb(self, co):
        vgroups = self.vg_full
        a = None
        b = None
        final_p = 0
        mindist = 1e30
        for bone in self.rig.data.edit_bones:
            co2, p = mathutils.geometry.intersect_point_line(co, bone.head, bone.tail)
            if p < 0 or p > 1:
                continue
            dist = (co - co2).length
            if dist < mindist and bone != self.cur_bone and dist < (bone.tail - bone.head).length / 2:
                new_a = vgroups.get(f"joint_{bone.name}_head")
                if new_a is None and bone.parent is not None:
                    new_a = vgroups.get(f"joint_{bone.parent.name}_tail")
                new_b = vgroups.get(f"joint_{bone.name}_tail")
                if new_a is not None and new_b is not None:
                    mindist = dist
                    a = new_a
                    b = new_b
                    final_p = p

        if a is None:
            return "Nearest bone is not found"

        return vg_mix2(vg_full_to_dict(a), vg_full_to_dict(b), final_p)

    def run(self, joints):
        if self.ui.vg_widgets:
            joints = {name: tup for name, tup in joints.items() if tup[1] == "head"}
            offsets = {k: v[0].tail - v[0].head for k, v in joints.items()}

        char = self.char
        verts = char.data.vertices

        calc_func = self.get_calc_func()

        for name, (bone, attr) in joints.items():
            co = getattr(bone, attr)
            self.cur_name = name
            self.cur_bone = bone
            self.cur_attr = attr

            co1 = co.copy()
            if self.ui.vg_offs == "S":
                co1 -= get_offs(bone, attr)

            if self.ui.vg_shift > 1e-6:
                group = self.vg_full.get(name)
                if group is not None:
                    co2 = vg_full_to_avg(group)
                    if co2 is not None:
                        co1 += (co1 - co2) * self.ui.vg_shift

            vg_data = calc_func(co1)
            if isinstance(vg_data, str):
                return name + ": " + vg_data

            if self.ui.vg_mix < 1:
                group = self.vg_full.get(name)
                if group is not None:
                    vg_mix2(vg_data, vg_full_to_dict(group), 1 - self.ui.vg_mix)

            coeff = max(vg_data.values())
            if coeff < 1e-30:
                return name + ": empty vg returned"

            coeff = 1 / coeff
            co2 = mathutils.Vector()
            wsum = 0.0

            vg = overwrite_vg(char.vertex_groups, name)
            if self.ui.vg_widgets:
                vgt = overwrite_vg(char.vertex_groups, "joint_" + bone.name + "_tail")

            for idx, weight in vg_data.items():
                weight *= coeff
                vg.add([idx], weight, 'REPLACE')
                if self.ui.vg_widgets:
                    vgt.add([idx], weight, 'REPLACE')
                co2 += verts[idx].co * weight
                wsum += weight

            co2 /= wsum

            k = "charmorph_offs_" + attr
            if self.ui.vg_offs == "R":
                offs = co - co2
                if offs.length >= self.ui.vg_snap:
                    bone[k] = list(offs)
                elif k in bone:
                    del bone[k]
            elif self.ui.vg_offs == "C":
                if k in bone:
                    del bone[k]

            if self.ui.vg_widgets:
                bone["charmorph_offs_tail"] = get_offs(bone, "head") + offsets.get(name, mathutils.Vector())

        return True


class UIProps:
    vg_xmirror: bpy.props.BoolProperty(
        name="X Mirror",
        description="Use X mirror for vertex group calculation",
        default=True,
    )
    vg_auto_snap: bpy.props.BoolProperty(
        name="Auto snap",
        description="Automatically snap the joint to newly created vertex group",
        default=True,
    )
    vg_widgets: bpy.props.BoolProperty(
        name="Widget mode",
        description="Recalc vertex groups only for head of the bone while keeping head to tail offset",
    )
    vg_calc: bpy.props.EnumProperty(
        name="Recalc mode",
        default="NF",
        items=[
            ("", "Surface", ""),
            ("NP", "n nearest vertices", "Snap joint to n nearest vertices"),
            ("NF", "Nearest face", "Snap joint to nearest face"),
            ("NE", "Nearest edge", "Snap joint to nearest edge"),
            ("NR", "By distance", "Snap joint to vertices within specified distance"),
            ("", "Inner", ""),
            ("RB", "Raycast bone axes", "Cast rays along bone axes and calculate VGs based on hit faces"),
            ("RG", "Raycast global", "Cast rays along global axes or selected object"),
            ("XL", "Cross lines", "Calculate based on lines crossing the desired point"),
            ("BB", "Bounding box (exp)", "Recalculate vertex group based on smallest bounding box vertices (experimental)"),
            ("", "Other", ""),
            ("CU", "Current", "Use current vertex group members and recalc only weights"),
            ("NJ", "n nearest joints", "Snap joint to other nearest joints"),
            ("NB", "Nearest bone", "Snap joint to the middle of already calculated bone"),
            ("NC", "Neighbors equal", "Mix neighbors vertex groups at equal proportion"),
            ("NW", "Neighbors weighted", "Mix neighbors vertex groups based on distance to them"),
        ]
    )
    vg_offs: bpy.props.EnumProperty(
        name="Offsets",
        description="Use offset if vertex group can't properly point at joint position",
        default="C",
        items=[
            ("C", "Clear", "Clear any offsets, use only vertex group positions"),
            ("R", "Recalculate", "Recalculate offsets exactly point specified joint position"),
            ("K", "Keep", "Keep current offsets"),
            ("S", "Keep and subtract", "Keep current offset and subtract it when recalculating vertex group"),
        ]
    )
    vg_xl_vn: bpy.props.IntProperty(
        name="Search point count",
        description="Search vertex count for cross lines",
        default=32,
        min=3, soft_max=256,
    )
    vg_xl_n: bpy.props.IntProperty(
        name="Cross lines count",
        description="How many cross lines to search",
        default=4,
        min=1, soft_max=16,
    )
    vg_n: bpy.props.IntProperty(
        name="VG Point count",
        description="Vertex/Joint count for vertex group recalc",
        default=1,
        min=1, soft_max=20,
    )
    vg_radius: bpy.props.FloatProperty(
        name="Search radius",
        description="Search vertices within given radius",
        default=0.1,
        min=0, soft_max=0.5,
    )
    vg_snap: bpy.props.FloatProperty(
        name="Snap distance",
        description="Snap to vertex or edge instead of face within given distance. Also affects mininum possible offset",
        default=0.0001,
        precision=5,
        min=0, soft_max=0.1,
    )
    vg_bone: bpy.props.EnumProperty(
        name="Bone",
        description="Which bone axes to use for middle joints. Has no effect at ends of bone chain",
        default="A",
        items=[
            ("P", "Parent", "Use parent (upper) bone axes"),
            ("C", "Child", "Use child (lower) bone axes"),
            ("A", "Average", "Average axes of two bones"),
            ("M", "Mix", "Cast rays along both bones axes"),
        ]
    )
    vg_rays: bpy.props.IntProperty(
        name="Ray count",
        description="When more than 1, cast additional rays perpendicular to the bone. It's recommended to select only one of X,Z axes in this case",
        default=1,
        min=1, soft_max=16,
    )
    vg_roll: bpy.props.FloatProperty(
        name="Roll adjust",
        description="Roll bone axes to this amount when casting rays",
        default=0,
        subtype="ANGLE",
    )
    vg_mix: bpy.props.FloatProperty(
        name="Mix factor",
        description="Mix newly calculated vertex group with existing one. Use 1 to fully replace existing group and 0 to never replace existing group",
        default=1,
        min=0, max=1,
        subtype='FACTOR',
    )
    vg_shift: bpy.props.FloatProperty(
        name="Shift factor",
        description="Shift joint location away from current vg location. Recommended value is 1/(Mix factor)-1.",
        default=0,
        min=0, soft_max=1,
        subtype='FACTOR',
    )
    vg_obj: bpy.props.PointerProperty(
        name="Object",
        type=bpy.types.Object,
        description="Get raycast axes from an object (usually empty object, optional)",
    )
    vg_obj_mirror: bpy.props.EnumProperty(
        name="X mirror",
        description="Mirror rays direction depending on casting position",
        default="S",
        items=[
            ("N", "No", "Don't use X mirror"),
            ("S", "By side", "Use one direction on one side and other on another"),
            ("G", "Gradual", "Gradually change X direction of rays"),
        ]
    )

    vg_x: bpy.props.BoolProperty(
        name="X",
        description="Cast rays along X axis",
        default=True,
    )
    vg_y: bpy.props.BoolProperty(
        name="Y",
        description="Cast rays along Y axis",
        default=True,
    )
    vg_z: bpy.props.BoolProperty(
        name="Z",
        description="Cast rays along Y axis",
        default=True,
    )


class CMEDIT_PT_VGCalc(bpy.types.Panel):
    bl_label = "Joint VG Calculation"
    bl_parent_id = "CMEDIT_PT_Rigging"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"
    bl_order = 1

    def draw(self, context):
        ui = context.window_manager.cmedit_ui
        l = self.layout

        l.operator("cmedit.calc_vg")

        l.prop(ui, "vg_xmirror")
        l.prop(ui, "vg_auto_snap")
        l.prop(ui, "vg_widgets")
        l.prop(ui, "vg_offs")
        l.prop(ui, "vg_calc")
        if ui.vg_calc in ("NF", "RB") or ui.vg_offs == "R":
            l.prop(ui, "vg_snap")

        if ui.vg_calc in ("RB", "RG"):
            r1 = l.row(heading="Axes", align=True)
            r1.prop(ui, "vg_x", toggle=True)
            r1.prop(ui, "vg_y", toggle=True)
            r1.prop(ui, "vg_z", toggle=True)

        if ui.vg_calc == "RB":
            l.prop(ui, "vg_bone")
            l.prop(ui, "vg_rays")
            l.prop(ui, "vg_roll")

        if ui.vg_calc == "RG":
            l.prop(ui, "vg_obj")
            if ui.vg_obj:
                l.prop(ui, "vg_obj_mirror")

        if ui.vg_calc in ("NP", "NJ"):
            l.prop(ui, "vg_n")
        elif ui.vg_calc == "NR":
            l.prop(ui, "vg_radius")
        elif ui.vg_calc == "XL":
            l.prop(ui, "vg_xl_vn")
            l.prop(ui, "vg_xl_n")
        l.prop(ui, "vg_mix", slider=True)
        l.prop(ui, "vg_shift", slider=True)


classes = (CMEDIT_PT_VGCalc,)
