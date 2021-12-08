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
# Copyright (C) 2020-2021 Michael Vigovsky

import logging, os
import bpy, mathutils # pylint: disable=import-error

from . import edit_io, edit_vg_calc
from . import yaml, rigging, utils

logger = logging.getLogger(__name__)

class VIEW3D_PT_CMEdit(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_CMEdit"
    bl_label = "Character editing"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 2

    def draw(self, _): # pylint: disable=no-self-use
        pass

class CMEDIT_PT_Rigging(bpy.types.Panel):
    bl_label = "Rigging"
    bl_parent_id = "VIEW3D_PT_CMEdit"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"
    bl_order = 1

    def draw(self, context):
        ui = context.window_manager.cmedit_ui
        l = self.layout
        l.prop(ui, "rig_char")
        l.operator("cmedit.joints_to_vg")
        l.prop(ui, "rig_xmirror")
        l.prop(ui, "rig_widgets")
        l.prop(ui, "rig_vg_offs")
        l.prop(ui, "rig_vg_calc")
        if ui.rig_vg_calc in ("NP","NJ"):
            l.prop(ui, "rig_vg_n")
        elif ui.rig_vg_calc == "NR":
            l.prop(ui, "rig_vg_radius")
        elif ui.rig_vg_calc == "XL":
            l.prop(ui, "rig_vg_xl_vn")
            l.prop(ui, "rig_vg_xl_n")

        l.operator("cmedit.calc_vg")
        l.operator("cmedit.symmetrize_joints")
        l.operator("cmedit.bbone_handles")
        l.operator("cmedit.rigify_finalize")
        l.prop(ui, "rig_tweaks_file")
        l.operator("cmedit.rigify_tweaks")

class CMEDIT_PT_Utils(bpy.types.Panel):
    bl_label = "Utils"
    bl_parent_id = "VIEW3D_PT_CMEdit"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"
    bl_order = 2

    def draw(self, _):
        l = self.layout
        l.operator("cmedit.cleanup_joints")
        l.operator("cmedit.check_symmetry")
        l.operator("cmedit.symmetrize_vg")

def obj_by_type(name, typ):
    if not name:
        return None
    obj = bpy.data.objects.get(name)
    if obj and obj.type == typ:
        return obj
    return None

def get_char(context):
    return obj_by_type(context.window_manager.cmedit_ui.rig_char, "MESH")

def kdtree_from_bones(bones):
    kd = mathutils.kdtree.KDTree(len(bones)*2)
    for i, bone in enumerate(bones):
        if not bone.use_connect:
            kd.insert(bone.head, i*2)
        kd.insert(bone.tail, i*2+1)
    kd.balance()
    return kd

def joint_list_extended(context, xmirror):
    result = rigging.selected_joints(context)
    bones = context.object.data.edit_bones
    kd = kdtree_from_bones(bones)
    for name, tup in list(result.items()):
        co = tup[0]
        checklist = [co]
        if xmirror:
            checklist.append(mathutils.Vector((-co[0], co[1], co[2])))
        for co2 in checklist:
            for co3, jid, _ in kd.find_range(co2, 0.00001):
                bone = bones[jid//2]
                attr = "head" if jid&1 == 0 else "tail"
                name = "joint_{}_{}".format(bone.name, attr)
                if name not in result:
                    result[name] = (co3, bone, attr)
    return result

def editable_bones_poll(context):
    return context.mode == "EDIT_ARMATURE" and get_char(context)

class OpJointsToVG(bpy.types.Operator):
    bl_idname = "cmedit.joints_to_vg"
    bl_label = "Selected joints to VG"
    bl_description = "Move selected joints according to their vertex groups"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return editable_bones_poll(context)

    def execute(self, context): # pylint: disable=no-self-use
        r = rigging.Rigger(context)
        r.joints_from_char(get_char(context))
        r.run(joint_list_extended(context, False))
        return {"FINISHED"}

class OpCalcVg(bpy.types.Operator):
    bl_idname = "cmedit.calc_vg"
    bl_label = "Recalc vertex groups"
    bl_description = "Recalculate joint vertex groups according to selected method"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return editable_bones_poll(context)

    def execute(self, context): # pylint: disable=no-self-use
        ui = context.window_manager.cmedit_ui
        joints = joint_list_extended(context, ui.rig_xmirror)

        if not edit_vg_calc.do_calc(get_char(context), joints, ui):
            self.report({"ERROR"}, "Calculation failed")

        return {"FINISHED"}

class OpRigifyFinalize(bpy.types.Operator):
    bl_idname = "cmedit.rigify_finalize"
    bl_label = "Finalize Rigify rig"
    bl_description = "Fix Rigify rig to make it suitable for char (in combo box). It adds deform flag to necessary bones and fixes facial bendy bones"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "ARMATURE" and get_char(context)

    def execute(self, context): # pylint: disable=no-self-use
        rigging.rigify_finalize(context.object, get_char(context))
        return {"FINISHED"}

class OpRigifyTweaks(bpy.types.Operator):
    bl_idname = "cmedit.rigify_tweaks"
    bl_label = "Apply rigify tweaks"
    bl_description = "Apply rigify tweaks from yaml file"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "ARMATURE"

    def execute(self, context): # pylint: disable=no-self-use
        file = context.window_manager.cmedit_ui.rig_tweaks_file
        with open(file) as f:
            tweaks = yaml.safe_load(f)
        pre_tweaks, editmode_tweaks, post_tweaks = rigging.unpack_tweaks(os.path.dirname(file), tweaks)
        old_mode = context.mode
        if old_mode.startswith("EDIT_"):
            old_mode = "EDIT"
        override = context.copy()
        if len(pre_tweaks) > 0:
            bpy.ops.object.mode_set(override, mode="OBJECT")
            for tweak in pre_tweaks:
                rigging.apply_tweak(context.object, tweak)

        if len(editmode_tweaks) > 0:
            bpy.ops.object.mode_set(override, mode="EDIT")
            for tweak in editmode_tweaks:
                rigging.apply_editmode_tweak(context, tweak)

        if len(post_tweaks) > 0:
            bpy.ops.object.mode_set(override, mode="OBJECT")
            for tweak in post_tweaks:
                rigging.apply_tweak(context.object, tweak)
        bpy.ops.object.mode_set(override, mode=old_mode)
        return {"FINISHED"}


def is_deform(group_name):
    return group_name.startswith("DEF-") or group_name.startswith("MCH-") or group_name.startswith("ORG-")

def swap_l_r(name):
    new_name = name.replace(".L", ".R").replace("_L_", "_R_")
    if new_name != name:
        return new_name
    return name.replace(".R", ".L").replace("_R_", "_L_")

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

    def execute(self, context): # pylint: disable=no-self-use
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
                if abs(g.weight-g2_weight) >= 0.01:
                    print(v.index, v.co, g1.name, "vg weight mismatch:", g.weight, g2_weight)
                    continue

            if abs(wgt-1) >= 0.0001:
                print(v.index, v.co, "not normalized:", wgt)
        return {"FINISHED"}


class OpSymmetrizeWeights(bpy.types.Operator):
    bl_idname = "cmedit.symmetrize_vg"
    bl_label = "Normalize+symmetrize weights"
    bl_description = "Normalize and symmetrize selected vertices using X axis"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH"

    def execute(self, context):
        obj = context.object
        mesh = obj.data
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
                if abs(wgt-1) < 0.0001:
                    return
                for ge in groups:
                    ge.weight /= wgt
            if v.co[0] == 0 or v.co[0] == -0:
                normalize(v)
                continue
            v2 = counterpart_vertex(mesh.vertices, kd, v)
            if v2 is None:
                continue
            gdict = {obj.vertex_groups[g.group].name: g for g in v2.groups}

            wgt2 = 0
            #cleanup groups without counterparts before normalizing
            for g in v.groups:
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
            if abs(wgt2-1) < 0.0001:
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

                if abs(g1e.weight-g2w) >= 0.00001:
                    if not is_deform(g2name):
                        print("Normalizing non-deform", v.index, v2.index, g2name)
                    #print("Normalizing", v.index, v2.index, g1e.weight, g2w, wgt2, g2name)
                    if v2.select:
                        wgt = (g1e.weight+g2w)/2
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

    def execute(self, context): # pylint: disable=no-self-use
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
                    if abs(g.weight-w2) >= 1e-5:
                        print("assymetry:", cvg.name, v.index, g.weight, v2.index, w2)

        return {"FINISHED"}

class OpCleanupJoints(bpy.types.Operator):
    bl_idname = "cmedit.cleanup_joints"
    bl_label = "Cleanup joint VGs"
    bl_description = "Remove all unused joint_* vertex groups. Metarig must be selected."
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return get_char(context) and context.object and context.object.type == "ARMATURE"

    def execute(self, context):
        char = get_char(context)
        joints = rigging.all_joints(context.object)
        if len(joints) == 0:
            self.report({'ERROR'}, "No joints found")
            return {"CANCELLED"}

        for k, v in list(joints.items()):
            if v[2] == "head" and utils.is_true(v[1].get("charmorph_connected")):
                del joints[k]

        for vg in list(char.vertex_groups):
            if vg.name.startswith("joint_") and vg.name not in joints:
                char.vertex_groups.remove(vg)

        return {"FINISHED"}

class OpBBoneHandles(bpy.types.Operator):
    bl_idname = "cmedit.bbone_handles"
    bl_label = "B-Bone handles"
    bl_description = "Add custom handles same to automatic for selected bbones"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_ARMATURE"

    def execute(self, context):  # pylint: disable=no-self-use
        for bone in context.selected_editable_bones:
            if bone.bbone_segments < 2:
                continue
            if bone.bbone_handle_type_start == "AUTO":
                bone.bbone_handle_type_start = "ABSOLUTE"
                if abs(bone.bbone_easein) > 0.01 and bone.parent and bone.use_connect:
                    bone.bbone_custom_handle_start = bone.parent
            if bone.bbone_handle_type_end == "AUTO":
                bone.bbone_handle_type_end = "ABSOLUTE"
                if abs(bone.bbone_easeout) > 0.01:
                    children = bone.children
                    if len(children) == 1:
                        bone.bbone_custom_handle_end = bone.children[0]
        return {"FINISHED"}

def objects_by_type(typ):
    return [(o.name, o.name, "") for o in bpy.data.objects if o.type == typ]

rigify_tweaks_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), "data/tweaks/rigify_default.yaml")

class CMEditUIProps(bpy.types.PropertyGroup):
    # Rigging
    rig_char: bpy.props.EnumProperty(
        name="Char",
        items=lambda ui, context: objects_by_type("MESH"),
        description="Character mesh for rigging"
    )
    rig_xmirror: bpy.props.BoolProperty(
        name="X Mirror",
        description="Use X mirror for vertex group calculation",
        default=True,
    )
    rig_widgets: bpy.props.BoolProperty(
        name="Widget mode",
        description="Recalc vertex groups only for head of the bone while keeping head to tail offset",
    )
    rig_vg_calc: bpy.props.EnumProperty(
        name="Recalc mode",
        default="NF",
        items=[
            ("CU", "Current", "Use current vertex group members and recalc only weights"),
            ("NP", "n nearest vertices", "Recalculate vertex group based on n nearest vertices"),
            ("NJ", "n nearest joints", "Recalculate vertex group based on n nearest joints"),
            ("XL", "Cross lines", "Calculate based on lines crossing the desired point (good for interior joints)"),
            ("NR", "By distance", "Recalculate vertex group based on vertices within specified distance"),
            ("NF", "Nearest face", "Recalculate vertex group based on nearest face"),
            ("NE", "Nearest edge", "Recalculate vertex group based on nearest edge"),
            ("NC", "Neighbors equal", "Mix neighbors vertex groups at equal proportion"),
            ("NW", "Neighbors weighted", "Mix neighbors vertex groups based on distance to them"),
            ("BB", "Bounding box (exp)", "Recalculate vertex group based on smallest bounding box vertices (experimental)"),
        ]
    )
    rig_vg_offs: bpy.props.EnumProperty(
        name="Offsets",
        description="Use offset if vertex group can't properly point at joint position",
        default="C",
        items=[
            ("K", "Keep", "Keep current offsets"),
            ("R", "Recalculate", "Recalculate offsets exactly point specified joint position"),
            ("C", "Clear", "Clear any offsets, use only vertex group positions"),
        ]
    )
    rig_vg_xl_vn: bpy.props.IntProperty(
        name="Search point count",
        description="Search vertex count for cross lines",
        default=32,
        min=3, soft_max=256,
    )
    rig_vg_xl_n: bpy.props.IntProperty(
        name="Cross lines count",
        description="How many cross lines to search",
        default=4,
        min=1, soft_max=16,
    )
    rig_vg_n: bpy.props.IntProperty(
        name="VG Point count",
        description="Vertex/Joint count for vertex group recalc",
        default=1,
        min=1, soft_max=20,
    )
    rig_vg_radius: bpy.props.FloatProperty(
        name="VG recalc radius",
        description="Radius for vertex group recalc",
        default=0.1,
        min=0, soft_max=0.5,
    )
    rig_tweaks_file: bpy.props.StringProperty(
        name="Tweaks file",
        description="Path to rigify tweaks yaml file",
        default=rigify_tweaks_file,
        subtype='FILE_PATH',
    )
    rig_bones_mode: bpy.props.EnumProperty(
        name="Bones mode",
        description="Bones export mode",
        default="N",
        items=[
            ("N", "Props only", "Export data only where charmorph_* custom props are present"),
            ("X", "X axis", "Export X axis for all bones"),
            ("Z", "Z axis", "Export Z axis for all bones"),
        ]
    )
    vg_regex: bpy.props.StringProperty(
        name="VG regex",
        description="Regular expression for vertex group export",
        default="^(DEF-|MCH-|ORG|(corrective_smooth|preserve_volume)(_inv)?$)",
    )
    vg_overwrite: bpy.props.BoolProperty(
        name="VG overwrite",
        description="Overwrite existing vertex groups with imported ones",
    )
    morph_float_precicion: bpy.props.EnumProperty(
        name="Precision",
        description="Floating point precision for morph npz files",
        default="32",
        items=[
            ("32", "32 bits", "IEEE Single precision floating point"),
            ("64", "64 bits", "IEEE Double precision floating point"),
        ]
    )

classes = [
    CMEditUIProps, OpJointsToVG, OpCalcVg, OpRigifyFinalize, VIEW3D_PT_CMEdit, CMEDIT_PT_Rigging, OpCleanupJoints,
    OpCheckSymmetry, OpSymmetrizeWeights, OpSymmetrizeJoints, OpBBoneHandles, OpRigifyTweaks, CMEDIT_PT_Utils]

classes.extend(edit_io.classes)

register_classes, unregister_classes = bpy.utils.register_classes_factory(classes)

def register():
    register_classes()
    bpy.types.WindowManager.cmedit_ui = bpy.props.PointerProperty(type=CMEditUIProps, options={"SKIP_SAVE"})

def unregister():
    del bpy.types.WindowManager.cmedit_ui
    unregister_classes()

if __name__ == "__main__":
    register()
