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

#
# Rigify functions that aren't used in editing module
# Those used in editing are in rigging.py
#

import math
import bpy          # pylint: disable=import-error
import rna_prop_ui  # pylint: disable=import-error

from . import morphing
from .lib import charlib, rigging, utils

def remove_rig(rig):
    try:
        bpy.data.texts.remove(rig["rig_ui"])
    except:
        pass
    bpy.data.armatures.remove(rig.data)

def apply_metarig_parameters(metarig):
    if not hasattr(bpy.types.PoseBone, "rigify_type"):
        return
    ui = bpy.context.window_manager.charmorph_ui
    for bone in metarig.pose.bones:
        if bone.rigify_type == "limbs.super_palm":
            if ui.rigify_palm_2sides:
                bone.rigify_parameters.palm_both_sides = True
            if ui.rigify_palm_fk:
                bone.rigify_parameters.make_extra_control = True
        elif bone.rigify_type == "spines.basic_spine" and ui.rigify_spine_pivot:
            bone.rigify_parameters.make_custom_pivot = True
        elif bone.rigify_type == "limbs.super_finger" and ui.rigify_finger_ik:
            bone.rigify_parameters.make_extra_ik_control = True
        elif bone.rigify_type == "basic.super_copy" and bone.name.startswith("shoulder.") and hasattr(bone.rigify_parameters, "super_copy_widget_type"):
            # Special widget for shoulders is supported in new Rigify versions.
            # But for compatibility it isn't enabled in metarig by default
            params = bone.rigify_parameters
            params.make_widget = True
            params.super_copy_widget_type = "shoulder"

lim_default_min = -10
lim_default_max = 160

def apply_rig_parameters(rig, conf):
    ui = bpy.context.window_manager.charmorph_ui
    if not ui.rigify_disable_ik_stretch and not ui.rigify_limit_ik:
        return

    def set_ik_limits(bone, lim_min, lim_max):
        bone.use_ik_limit_x = True
        bone.ik_min_x = math.radians(lim_min)
        bone.ik_max_x = math.radians(lim_max)
        if ui.rigify_disable_ik_stretch:
            bone.ik_stiffness_x = 0.98

    limit_ik = ui.rigify_limit_ik
    if limit_ik and conf.ik_limits:
        limit_ik = False
        for key, value in conf.ik_limits.items():
            bone = rig.pose.bones.get(key)
            if bone is not None:
                set_ik_limits(bone, value.get("min", lim_default_min), value.get("max", lim_default_max))

    for bone in rig.pose.bones:
        have_ik = False
        for c in bone.constraints:
            if c.type == "IK":
                have_ik = True
                if ui.rigify_disable_ik_stretch:
                    c.use_stretch = False
        if limit_ik and have_ik and not bone.lock_ik_x and bone.lock_ik_y and bone.lock_ik_z:
            set_ik_limits(bone, lim_default_min, lim_default_max)
        if ui.rigify_disable_ik_stretch and "IK_Stretch" in bone:
            bone["IK_Stretch"] = 0
            if hasattr(rna_prop_ui,"rna_idprop_ui_prop_get"):
                # Blender < 3.0
                idprop = rna_prop_ui.rna_idprop_ui_prop_get(bone, "IK_Stretch")
                for attr in ("min", "max", "soft_min", "soft_max", "default"):
                    idprop[attr] = 0
            elif hasattr(bone, "id_properties_ui"):
                # Blender >= 3.0
                idprop = bone.id_properties_ui("IK_Stretch")
                idprop.update(min=0, max=0, soft_min=0, soft_max=0, default=0)

def add_mixin(char, conf, rig):
    obj_name = conf.mixin
    if not obj_name:
        return (None, None)
    mixin = utils.import_obj(char.path(conf.file), obj_name, "ARMATURE")
    bones = [b.name for b in mixin.data.bones]
    joints = rigging.all_joints(mixin)
    bpy.ops.object.join({
        "object": rig,
        "selected_editable_objects": [rig, mixin],
    })

    return (bones, joints)

def do_rig(m, conf: charlib.Armature, rigger: rigging.Rigger):
    obj = m.obj
    metarig = bpy.context.object
    if hasattr(metarig.data, "rigify_generate_mode"):
        metarig.data.rigify_generate_mode = "new"
    if hasattr(metarig.data, "rigify_target_rig"):
        metarig.data.rigify_target_rig = None
    t = utils.Timer()
    bpy.ops.pose.rigify_generate()
    t.time("rigify part")
    rig = bpy.context.object
    sliding_joints = []
    try:
        bpy.data.armatures.remove(metarig.data)
        rig.name = obj.name + "_rig"

        rigging.rigify_finalize(rig, obj)
        apply_rig_parameters(rig, conf)

        new_bones, new_joints = add_mixin(m.char, conf, rig)

        pre_tweaks, editmode_tweaks, post_tweaks = rigging.unpack_tweaks(m.char.path("."), conf.tweaks)
        for tweak in pre_tweaks:
            rigging.apply_tweak(rig, tweak)
        if len(editmode_tweaks) > 0 or len(conf.sliding_joints) > 0 or new_joints:
            bpy.ops.object.mode_set(mode="EDIT")

            if new_joints:
                rigger.set_opts(conf.mixin_bones)
                if not rigger.run(new_joints):
                    raise rigging.RigException("Mixin fitting failed")

            for tweak in editmode_tweaks:
                rigging.apply_editmode_tweak(bpy.context, tweak)

            for name, upper_bone, lower_bone, side in rigging.iterate_sliding_joints(conf.sliding_joints):
                influence = m.sliding_joints.get("_".join((conf.name, name)), 0)
                if influence > 0.0001:
                    rigging.sliding_joint_create(bpy.context, upper_bone, lower_bone, side)
                    sliding_joints.append((upper_bone, lower_bone, side, influence))

            bpy.ops.object.mode_set(mode="OBJECT")

        for tweak in post_tweaks:
            rigging.apply_tweak(rig, tweak)

        for data in sliding_joints:
            rigging.sliding_joint_finalize(rig, *data)

        # adjust bone constraints for mixin
        if new_bones:
            for name in new_bones:
                bone = rig.pose.bones.get(name)
                if not bone:
                    continue
                for c in bone.constraints:
                    if c.type == "STRETCH_TO":
                        c.rest_length = bone.length
    except:
        try:
            remove_rig(rig)
        except:
            pass
        raise
    return rig

class UIProps:
    #TODO: Head pivot shift
    rigify_metarig_only: bpy.props.BoolProperty(
        name="Metarig only",
        description="Generate only metarig for development purposes")
    rigify_spine_pivot: bpy.props.BoolProperty(
        name="Custom spine pivot",
        description="Create a rotation pivot control for spine"
    )
    rigify_finger_ik: bpy.props.BoolProperty(
        name="Finger IK",
        description="Create finger IK controls"
    )
    rigify_palm_2sides: bpy.props.BoolProperty(
        name="Both palm sides contol",
        description="Create controls on both sides of palms"
    )
    rigify_palm_fk: bpy.props.BoolProperty(
        name="Palm FK",
        description="Create extra FK controls for palms"
    )
    rigify_disable_ik_stretch: bpy.props.BoolProperty(
        name="Disable IK stretch",
        description="Totally disable IK stretch. If IK stretch is enabled it can squeeze bones even if you don't try to stretch them.",
        default=True,
    )
    rigify_limit_ik: bpy.props.BoolProperty(
        name="Limit IK rotations",
        description="Forbid IK solver to bend limbs in wrong direction",
        default=True,
    )

class FinalizeSubpanel(bpy.types.Panel):
    bl_parent_id = "CHARMORPH_PT_Finalize"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'

class CHARMORPH_PT_SlidingJoints(FinalizeSubpanel):
    bl_label = "Sliding joints"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        m = morphing.morpher
        if not m or not hasattr(context.window_manager, "charmorphs"):
            return False
        return next(m.sliding_joints_by_rig(context.window_manager.charmorph_ui.fin_rig), False)

    def draw(self, context):
        col = self.layout.column()
        col.label(text="You can adjust these values")
        col.label(text="if joint bending looks strange")
        col = self.layout.column()
        for name in morphing.morpher.sliding_joints_by_rig(context.window_manager.charmorph_ui.fin_rig):
            col.prop(context.window_manager.charmorphs, "sj_" + name,
                slider=True, text=name[name.index("_")+1:])

class CHARMORPH_PT_RigifySettings(FinalizeSubpanel):
    bl_label = "Rigify settings"
    bl_order = 2

    @classmethod
    def poll(cls, context):
        rig = morphing.get_obj_char(context)[1].armature.get(context.window_manager.charmorph_ui.fin_rig)
        if not rig:
            return False
        result = rig.type == "rigify"
        return result

    def draw(self, context):
        for prop in UIProps.__annotations__: # pylint: disable=no-member
            self.layout.prop(context.window_manager.charmorph_ui, prop)

classes = [CHARMORPH_PT_SlidingJoints, CHARMORPH_PT_RigifySettings]
