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

import bpy, mathutils  # pylint: disable=import-error

from ..lib import utils


def is_deform(group_name):
    return group_name.startswith("DEF-") or group_name.startswith("MCH-") or group_name.startswith("ORG-")


def swap_l_r(name):
    new_name = name.replace(".L", ".R").replace("_L_", "_R_").replace(".l", ".r").replace("_l_", "_r_")
    if new_name != name:
        return new_name
    return name.replace(".R", ".L").replace("_R_", "_L_").replace(".r", ".l").replace("_r_", "_l_")


def counterpart_vertex(verts, kd, v):
    counterparts = kd.find_range(mathutils.Vector((-v.co[0], v.co[1], v.co[2])), 0.00001)
    if len(counterparts) == 0:
        print(v.index, v.co, "no counterpart")
        return None
    if len(counterparts) > 1:
        print(v.index, v.co, "multiple counterparts:", counterparts)
        return None
    return verts[counterparts[0][1]]


class OpCheckSymmetry(bpy.types.Operator):
    bl_idname = "cmedit.check_symmetry"
    bl_label = "Check symmetry"
    bl_description = "Check X axis symmetry and print results to system console"

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH"

    def execute(self, context):  # pylint: disable=no-self-use
        obj = context.object
        mesh = obj.data
        kd = utils.kdtree_from_verts(mesh.vertices)

        def groups_to_list(group):
            return [(obj.vertex_groups[g.group].name, g.weight) for g in group]
        for v in mesh.vertices:
            if v.co[0] == 0 or v.co[0] == -0:
                continue
            v2 = counterpart_vertex(mesh.vertices, kd, v)
            if v2 is None:
                continue
            if len(v.groups) != len(v2.groups):
                print(v.index, v.co, "vg mismatch:", groups_to_list(v.groups), groups_to_list(v2.groups))

            gdict = {obj.vertex_groups[g.group].name: (obj.vertex_groups[g.group], g.weight) for g in v2.groups}

            wgt = 0

            for g in v.groups:
                g1 = obj.vertex_groups[g.group]
                if is_deform(g1.name):
                    wgt += g.weight
                g2_name = swap_l_r(g1.name)
                g2, g2_weight = gdict.get(g2_name, (None, None))
                if not g2:
                    print(v.index, v.co, g1.name, g.weight, "vg counterpart not found")
                    continue
                if abs(g.weight - g2_weight) >= 0.01:
                    print(v.index, v.co, g1.name, "vg weight mismatch:", g.weight, g2_weight)
                    continue

            if abs(wgt - 1) >= 0.0001:
                print(v.index, v.co, "not normalized:", wgt)
        return {"FINISHED"}


def get_group_weight(v, idx):
    for g in v.groups:
        if g.group == idx:
            return g.weight
    return 0


class OpSymmetrizeVG(bpy.types.Operator):
    bl_idname = "cmedit.symmetrize_vg"
    bl_label = "Symmetrize current VG"
    bl_description = "Symmetrize current vertex group using X axis"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH" and context.object.vertex_groups.active

    def execute(self, context):  # pylint: disable=no-self-use
        obj = context.object
        vg = obj.vertex_groups.active
        idx = vg.index
        mesh = obj.data
        kd = utils.kdtree_from_verts(mesh.vertices)
        for v in mesh.vertices:
            if v.co[0] < 1e-30:
                continue
            v2 = counterpart_vertex(mesh.vertices, kd, v)
            if v2 is None:
                continue
            w = (get_group_weight(v, idx) + get_group_weight(v2, idx)) / 2
            if w >= 1e-5:
                vg.add([v.index, v2.index], w, "REPLACE")
            else:
                vg.remove([v.index, v2.index])
        return {"FINISHED"}


class OpSymmetrizeWeights(bpy.types.Operator):
    bl_idname = "cmedit.symmetrize_weights"
    bl_label = "Normalize+symmetrize weights"
    bl_description = "Normalize and symmetrize selected vertices using X axis"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH"

    def execute(self, context):
        obj = context.object
        mesh = obj.data
        if mesh.is_editmode:
            obj.update_from_editmode()
        kd = utils.kdtree_from_verts(mesh.vertices)
        for v in mesh.vertices:
            if not v.select:
                continue

            def normalize(v):
                groups = []
                wgt = 0
                for ge in v.groups:
                    if is_deform(obj.vertex_groups[ge.group].name):
                        wgt += ge.weight
                        groups.append(ge)
                if abs(wgt - 1) < 0.0001:
                    return
                for ge in groups:
                    ge.weight /= wgt
            if v.co[0] == 0 or v.co[0] == -0:
                normalize(v)
                continue
            v2 = counterpart_vertex(mesh.vertices, kd, v)
            if v2 is None:
                print("no counterpart", v.index)
                continue
            gdict = {obj.vertex_groups[g.group].name: g for g in v2.groups}

            wgt2 = 0
            # cleanup groups without counterparts before normalizing
            for g in v.groups:
                if g.group > len(obj.vertex_groups) or g.group < 0:
                    print("bad vg id", v.index, g.group)
                    continue
                vg = obj.vertex_groups[g.group]
                g2e = gdict.get(swap_l_r(vg.name))
                if g2e:
                    if is_deform(vg.name):
                        wgt2 += g2e.weight
                elif not vg.lock_weight:
                    if not is_deform(vg.name):
                        print("removing non-deform vg", v.index, v2.index, v.co, vg.name)
                    vg.remove([v.index])

            if wgt2 < 0.0001:
                print(v.index, v2.index, "situation is too bad, please check")
                continue
            if abs(wgt2 - 1) < 0.0001:
                wgt2 = 1

            normalize(v)

            for g1e in v.groups:
                vg = obj.vertex_groups[g1e.group]
                if vg.lock_weight:
                    continue
                g2name = swap_l_r(vg.name)
                g2e = gdict[g2name]
                g2w = g2e.weight
                if is_deform(g2name):
                    g2w /= wgt2
                if g2w > 1:
                    print(v.index, v2.index, g2name, g2e.group, g2e.weight, g2w, wgt2)
                    self.report({'ERROR'}, "Bad g2 weight!")
                    return {"FINISHED"}

                if abs(g1e.weight - g2w) >= 0.00001:
                    if v2.select:
                        wgt = (g1e.weight + g2w) / 2
                        g1e.weight = wgt
                        g2e.weight = wgt
                    else:
                        g1e.weight = g2w

            normalize(v)
        return {"FINISHED"}


class OpSymmetrizeJoints(bpy.types.Operator):
    bl_idname = "cmedit.symmetrize_joints"
    bl_label = "Symmetrize joints"
    bl_description = "Symmetrize joints: add missing joint vertex groups from other side, report non-symmetrical joints"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH" and context.mode == "OBJECT"

    def execute(self, context):  # pylint: disable=no-self-use
        obj = context.object
        mesh = obj.data
        kd = utils.kdtree_from_verts(mesh.vertices)
        vg_map = {}
        new_vg = set()
        for vg in obj.vertex_groups:
            if not vg.name.startswith("joint_"):
                continue
            cname = swap_l_r(vg.name)
            if cname in obj.vertex_groups:
                cvg = obj.vertex_groups[cname]
            else:
                cvg = obj.vertex_groups.new(name=cname)
                new_vg.add(cvg.index)
            vg_map[vg.index] = cvg

        for v in mesh.vertices:
            for g in v.groups:
                if g.group in new_vg:
                    continue
                cvg = vg_map.get(g.group)
                if cvg is None:
                    continue
                v2 = counterpart_vertex(mesh.vertices, kd, v)
                if v2 is None:
                    continue
                if cvg.index in new_vg:
                    cvg.add([v2.index], g.weight, "REPLACE")
                else:
                    try:
                        w2 = cvg.weight(v2.index)
                    except RuntimeError:
                        w2 = 0
                    if abs(g.weight - w2) >= 1e-5:
                        print("assymetry:", cvg.name, v.index, g.weight, v2.index, w2)

        return {"FINISHED"}


class OpSymmetrizeOffsets(bpy.types.Operator):
    bl_idname = "cmedit.symmetrize_offsets"
    bl_label = "Symmetrize offsets"
    bl_description = "Symmetrize joints: add missing bone offsets from other side, report non-symmetrical offsets"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "ARMATURE"

    def execute(self, context):  # pylint: disable=no-self-use
        if context.mode == "EDIT_ARMATURE":
            bones = context.object.data.edit_bones
        else:
            bones = context.object.data.bones
        for bone in bones:
            cname = swap_l_r(bone.name)
            if cname == bone.name:
                continue
            if cname not in bones:
                print("No counterpart for", bone.name)
                continue
            bone2 = bones[cname]
            for attr in "head", "tail":
                if "charmorph_offs_" + attr in bone and "charmorph_offs_" + attr in bone2:
                    v1 = mathutils.Vector(bone["charmorph_offs_" + attr])
                    v2 = mathutils.Vector(bone2["charmorph_offs_" + attr])
                    v2[0] = -v2[0]
                    if (v1 - v2).length > 1e-6:
                        v2[0] = -v2[0]
                        print("assymetry:", bone.name, attr, v1, v2)
                if "charmorph_offs_" + attr in bone:
                    src = bone
                    dst = bone2
                elif "charmorph_offs_" + attr in bone2:
                    src = bone2
                    dst = bone
                else:
                    continue
                dst["charmorph_offs_" + attr] = src["charmorph_offs_" + attr]

        return {"FINISHED"}


class CMEDIT_PT_Symmetry(bpy.types.Panel):
    bl_label = "Symmetry"
    bl_parent_id = "VIEW3D_PT_CMEdit"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 2

    def draw(self, _):
        l = self.layout
        l.operator("cmedit.check_symmetry")
        l.operator("cmedit.symmetrize_weights")
        l.operator("cmedit.symmetrize_vg")
        l.operator("cmedit.symmetrize_joints")
        l.operator("cmedit.symmetrize_offsets")


classes = [OpCheckSymmetry, OpSymmetrizeVG, OpSymmetrizeWeights, OpSymmetrizeJoints, OpSymmetrizeOffsets, CMEDIT_PT_Symmetry]
