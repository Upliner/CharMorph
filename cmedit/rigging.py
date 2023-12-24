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

import os, logging, json
import bpy, bpy_extras, mathutils  # pylint: disable=import-error

from ..lib import rigging, drivers, utils
from . import vg_calc

logger = logging.getLogger(__name__)


def kdtree_from_bones(bones):
    kd = mathutils.kdtree.KDTree(len(bones) * 2)
    for i, bone in enumerate(bones):
        if not bone.use_connect:
            kd.insert(bone.head, i * 2)
        kd.insert(bone.tail, i * 2 + 1)
    kd.balance()
    return kd


def selected_joints(context):
    joints = {}
    for bone in context.object.data.edit_bones:
        if bone.select_head:
            if bone.use_connect:
                b = bone.parent
                joints[f"joint_{b.name}_tail"] = (b, "tail")
            else:
                joints[f"joint_{bone.name}_head"] = (bone, "head")
        if bone.select_tail:
            joints[f"joint_{bone.name}_tail"] = (bone, "tail")
    return joints


def joint_list_extended(context, xmirror):
    result = selected_joints(context)
    bones = context.object.data.edit_bones
    kd = kdtree_from_bones(bones)
    for name, (bone, attr) in list(result.items()):
        co = getattr(bone, attr)
        checklist = [co]
        if xmirror:
            checklist.append(mathutils.Vector((-co[0], co[1], co[2])))
        for co2 in checklist:
            for _, jid, _ in kd.find_range(co2, 0.00001):
                bone2 = bones[jid // 2]
                if list(utils.bone_get_collections(bone2)) != list(utils.bone_get_collections(bone)):
                    continue
                attr = "head" if jid & 1 == 0 else "tail"
                name = f"joint_{bone2.name}_{attr}"
                if name not in result:
                    result[name] = (bone2, attr)
    return result


def editable_bones_poll(context):
    return context.mode == "EDIT_ARMATURE" and context.window_manager.cmedit_ui.char_obj


class OpStoreRoll(bpy.types.Operator):
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return editable_bones_poll(context)

    def execute(self, context):
        for bone in context.selected_editable_bones:
            for axis in ('x', 'z'):
                if axis != self.axis and "charmorph_axis_" + axis in bone:
                    del bone["charmorph_axis_" + axis]
            bone["charmorph_axis_" + self.axis] = list(getattr(bone, self.axis + "_axis"))
        return {"FINISHED"}


class OpStoreRollX(OpStoreRoll):
    bl_idname = "cmedit.store_roll_x"
    bl_label = "Save bone roll X axis"
    axis = "x"


class OpStoreRollZ(OpStoreRoll):
    bl_idname = "cmedit.store_roll_z"
    bl_label = "Save bone roll Z axis"
    axis = "z"


class OpJointsToVG(bpy.types.Operator):
    bl_idname = "cmedit.joints_to_vg"
    bl_label = "Selected joints to VG"
    bl_description = "Move selected joints according to their vertex groups"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return editable_bones_poll(context)

    def execute(self, context):  # pylint: disable=no-self-use
        ui = context.window_manager.cmedit_ui
        r = rigging.Rigger(context)
        r.joints_from_char(ui.char_obj)
        r.run(joint_list_extended(context, False).values())
        return {"FINISHED"}


class OpCalcVg(bpy.types.Operator):
    bl_idname = "cmedit.calc_vg"
    bl_label = "Recalc vertex groups"
    bl_description = "Recalculate joint vertex groups according to selected method"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return editable_bones_poll(context)

    def execute(self, context):  # pylint: disable=no-self-use
        ui = context.window_manager.cmedit_ui
        joints = joint_list_extended(context, ui.vg_xmirror)

        err = vg_calc.VGCalculator(context.object, ui.char_obj, ui).run(joints)
        if isinstance(err, str):
            self.report({"ERROR"}, err)
        elif ui.vg_auto_snap:
            bpy.ops.cmedit.joints_to_vg()

        return {"FINISHED"}


class OpRigifyFinalize(bpy.types.Operator):
    bl_idname = "cmedit.rigify_finalize"
    bl_label = "Finalize Rigify rig"
    bl_description = "Fix Rigify rig to make it suitable for char (in combo box)."\
        "It adds deform flag to necessary bones and fixes facial bendy bones"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "ARMATURE" and context.window_manager.cmedit_ui.char_obj

    def execute(self, context):  # pylint: disable=no-self-use
        rigging.rigify_finalize(context.object, context.window_manager.cmedit_ui.char_obj)
        return {"FINISHED"}


class OpRigifyTweaks(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    bl_idname = "cmedit.rigify_tweaks"
    bl_label = "Apply rigify tweaks"
    bl_description = "Apply rigify tweaks from yaml file"
    bl_options = {"UNDO"}

    filter_glob: bpy.props.StringProperty(default="*.yaml", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "ARMATURE"

    def execute(self, context):  # pylint: disable=no-self-use
        with open(self.filepath, "r", encoding="utf-8") as f:
            tweaks = utils.load_yaml(f)
        pre_tweaks, editmode_tweaks, post_tweaks = rigging.unpack_tweaks(os.path.dirname(self.filepath), tweaks)
        old_mode = context.mode
        if old_mode.startswith("EDIT_"):
            old_mode = "EDIT"
        if len(pre_tweaks) > 0:
            bpy.ops.object.mode_set(mode="OBJECT")
            for tweak in pre_tweaks:
                rigging.apply_tweak(context.object, tweak)

        if len(editmode_tweaks) > 0:
            bpy.ops.object.mode_set(mode="EDIT")
            for tweak in editmode_tweaks:
                rigging.apply_editmode_tweak(context, tweak)

        if len(post_tweaks) > 0:
            bpy.ops.object.mode_set(mode="OBJECT")
            for tweak in post_tweaks:
                rigging.apply_tweak(context.object, tweak)
        bpy.ops.object.mode_set(mode=old_mode)
        return {"FINISHED"}


class OpCleanupJoints(bpy.types.Operator):
    bl_idname = "cmedit.cleanup_joints"
    bl_label = "Cleanup joint VGs"
    bl_description = "Remove all unused joint_* vertex groups. Metarig must be selected"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.window_manager.cmedit_ui.char_obj and context.object and context.object.type == "ARMATURE"

    def execute(self, context):
        char = context.window_manager.cmedit_ui.char_obj
        joints = rigging.get_joints(context.object)
        joints = {
            f"joint_{bone.name}_{attr}"
            for bone, attr in joints
            if attr != "head" or not utils.is_true(bone.get("charmorph_connected"))
        }
        if len(joints) == 0:
            self.report({'ERROR'}, "No joints found")
            return {"CANCELLED"}

        for vg in list(char.vertex_groups):
            if vg.name.startswith("joint_") and vg.name not in joints:
                logger.debug("removing group %s", vg.name)
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


class OpDrExport(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.dr_export"
    bl_label = "Export drivers"
    bl_description = 'Export rig drivers. Have rig selected And character mesh chosen above'
    filename_ext = ".json"

    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        ui = context.window_manager.cmedit_ui
        return context.object and context.object.type == "ARMATURE" and ui.char_obj

    def execute(self, context):
        ui = context.window_manager.cmedit_ui
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(drivers.export(char=ui.char_obj, rig=context.object), f, indent=4)
        return {"FINISHED"}


class OpDrImport(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    bl_idname = "cmedit.dr_import"
    bl_label = "Import drivers"
    bl_description = "Import rig drivers. Have rig selected And character mesh chosen above."
    bl_options = {"UNDO"}

    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        ui = context.window_manager.cmedit_ui
        return context.object and context.object.type == "ARMATURE" and ui.char_obj

    def execute(self, context):
        ui = context.window_manager.cmedit_ui
        drivers.dimport(
            utils.parse_file(self.filepath, json.load, {}),
            char=ui.char_obj, rig=context.object)
        return {"FINISHED"}


class OpDrClean(bpy.types.Operator):
    bl_idname = "cmedit.dr_clean"
    bl_label = "Clean drivers"
    bl_description = "Delete all drivers from selected object"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object

    def execute(self, context):
        drivers.clear_obj(context.object)
        return {"FINISHED"}


class CMEDIT_PT_Rigging(bpy.types.Panel):
    bl_label = "Rigging"
    bl_parent_id = "VIEW3D_PT_CMEdit"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 1

    def draw(self, context):
        ui = context.window_manager.cmedit_ui
        l = self.layout
        l.prop(ui, "char_obj")
        l.operator("cmedit.cleanup_joints")
        l.operator("cmedit.store_roll_x")
        l.operator("cmedit.store_roll_z")
        l.operator("cmedit.bbone_handles")
        l.operator("cmedit.rigify_finalize")
        l.operator("cmedit.rigify_tweaks")
        l.separator()
        l.operator("cmedit.dr_export")
        l.operator("cmedit.dr_import")
        l.operator("cmedit.dr_clean")
        l.separator()
        l.operator("cmedit.joints_to_vg")


classes = (OpJointsToVG, OpCalcVg, OpRigifyFinalize, OpCleanupJoints, OpBBoneHandles, OpRigifyTweaks,
           OpDrExport, OpDrImport, OpDrClean, OpStoreRollX, OpStoreRollZ, CMEDIT_PT_Rigging)
