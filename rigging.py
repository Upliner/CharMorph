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
# Copyright (C) 2020 Michael Vigovsky

import logging, math

from . import yaml, library

from bpy import ops
from mathutils import Vector, Matrix

logger = logging.getLogger(__name__)

def get_joints(context, is_all):
    joints = {}
    for bone in context.object.data.edit_bones:
        if is_all:
            if not bone.use_connect:
                joints["joint_"+bone.name+"_head"] = (bone.head, bone, "head")
        elif bone.select_head:
            if bone.use_connect:
                b = bone.parent
                joints["joint_"+b.name+"_tail"] = (b.tail, b, "tail")
            else:
                joints["joint_"+bone.name+"_head"] = (bone.head, bone, "head")
        if is_all or bone.select_tail:
            joints["joint_"+bone.name+"_tail"] = (bone.tail, bone, "tail")
    return joints

def all_joints(context):      return get_joints(context, True)
def selected_joints(context): return get_joints(context, False)

def get_vg_data(char, new, accumulate, verts):
    if verts is None:
        verts = char.data.vertices
    data = {}
    for v in char.data.vertices:
        for gw in v.groups:
            vg = char.vertex_groups[gw.group]
            if not vg.name.startswith("joint_"):
                continue
            data_item = data.get(vg.name)
            if not data_item:
                data_item = new()
                data[vg.name] = data_item
            accumulate(data_item, v, verts[v.index].co, gw)
    return data

def get_vg_avg(char, verts):
    def accumulate(data_item, v, co, gw):
        data_item[0] += gw.weight
        data_item[1] += co*gw.weight
    return get_vg_data(char, lambda: [0, Vector()], accumulate, verts)

def joints_to_vg(char, lst, verts):
    avg = get_vg_avg(char, verts)
    result = True
    bones = set()
    for name, (_, bone, attr) in lst.items():
        item = avg.get(name)
        if item and item[0]>0.1:
            bones.add(bone)
            pos = item[1]/item[0]
            offs = bone.get("charmorph_offs_" + attr)
            if offs and len(offs) == 3:
                pos += Vector(tuple(offs))
            setattr(bone, attr, pos)
        else:
            logger.error("No vg for " + name)
            if item:
                logger.error(item[0])
            result = False

    # Bone roll
    for bone in bones:
        axis = bone.get("charmorph_axis_z")
        flip = False
        if not axis:
            axis = bone.get("charmorph_axis_x")
            flip = True
        if axis and len(axis) == 3:
            axis = Vector(tuple(axis))
            if flip:
                axis = Matrix.Rotation(-math.pi/2,4,bone.y_axis) @ axis
            bone.align_roll(axis)
    return result

def rigify_add_deform(context, char):
    for vg in char.vertex_groups:
        if vg.name.startswith("ORG-") or vg.name.startswith("MCH-"):
            context.object.data.edit_bones[vg.name].use_deform = True

def reposition_armature_modifier(context, char):
    override = context.copy()
    override["object"] = char
    pos = len(char.modifiers)-1
    name = char.modifiers[pos].name

    for i, mod in enumerate(char.modifiers):
        if mod.type != "MASK":
            break
    for i in range(pos-i):
        if ops.object.modifier_move_up.poll():
            ops.object.modifier_move_up(override, modifier=name)

def unpack_tweaks(char, tweaks, editmode_tweaks=None, regular_tweaks=None, depth=0):
    if depth>100:
        logger.error("Too deep tweaks loading: " + repr(tweaks))
        return

    if editmode_tweaks is None:
        editmode_tweaks = []
    if regular_tweaks is None:
        regular_tweaks = []

    if isinstance(tweaks, str):
        tweaks = [tweaks]

    if not isinstance(tweaks, list):
        if tweaks is not None:
            logger.error("Unknown tweaks format: " + repr(tweaks))
        return
    for tweak in tweaks:
        if isinstance(tweak, str):
            with open(char.path(tweak)) as f:
                unpack_tweaks(char, yaml.safe_load(f), editmode_tweaks, regular_tweaks, depth+1)
        elif tweak.get("tweak")=="rigify_sliding_joint":
            editmode_tweaks.append(tweak)
            regular_tweaks.append(tweak)
        elif tweak.get("select") == "edit_bone":
            editmode_tweaks.append(tweak)
        else:
            regular_tweaks.append(tweak)
    return (editmode_tweaks, regular_tweaks)

def constraint_by_type(bone, type):
    for c in bone.constraints:
        if c.type == type:
            return c

def constraint_by_target(bone, rig, type, target):
    for c in bone.constraints:
        if c.type == type and c.target == rig and c.subtarget == target:
            return c

def apply_editmode_tweak(context, tweak):
    if tweak.get("tweak") == "rigify_sliding_joint":
        sliding_joint_create(context, tweak["upper_bone"], tweak["lower_bone"], tweak["side"])
    elif tweak.get("select") == "edit_bone":
        bone_name = tweak.get("bone")
        bone = context.object.data.edit_bones.get(tweak.get("bone"))
        if not bone:
            logger.error("Tweak bone not found: " + bone_name)
            return
        for attr, val in tweak.get("set").items():
            setattr(bone, attr, val)

def apply_tweak(rig, tweak):
    if not rig.pose:
        logger.error("No pose in rig " + repr(rig) + " no tweaks were applied")
        return
    if tweak.get("tweak") == "rigify_sliding_joint":
        sliding_joint_finalize(rig, tweak["upper_bone"], tweak["lower_bone"], tweak["side"], tweak["influence"])
        return
    bone_name = tweak.get("bone")
    select = tweak.get("select")
    if select=="bone":
        obj = rig.data.bones.get(bone_name)
    elif select=="pose_bone":
        obj = rig.pose.bones.get(bone_name)
        add = tweak.get("add")
        if add is None:
            pass
        elif add == "constraint":
            obj = obj.constraints.new(tweak.get("type"))
            if hasattr(obj, "target"):
                obj.target = rig
        else:
            logger.error("Invalid add operator: " + repr(tweak))
    elif select == "constraint":
        bone = rig.pose.bones.get(bone_name)
        obj = bone.constraints.get(tweak.get("name",""))
        if not obj:
            obj = constraint_by_target(bone, rig, tweak.get("type"), tweak.get("target_bone"))
    else:
        logger.error("Invalid tweak select: " + repr(tweak))
        return
    if not obj:
        logger.error("Tweak object not found: " + repr(tweak))
        return
    for attr, val in tweak.get("set").items():
        setattr(obj, attr, val)

def set_lock(bone, is_lock):
    bone.lock_location = (is_lock, is_lock, is_lock)
    bone.lock_rotation = (is_lock, is_lock, is_lock)
    bone.lock_rotation_w = is_lock
    bone.lock_rotations_4d = is_lock
    bone.lock_scale = (is_lock, is_lock, is_lock)

def make_gaming_rig(context, char):
    a = context.object.data
    bones = a.edit_bones
    for bone in list(bones):
        if bone.name == "ORG-face":
            bone.parent = bones["DEF-spine.006"]
        elif bone.name.startswith("DEF-breast.") or bone.name.startswith("DEF-shoulder."):
            bone.parent = bones["DEF-spine.003"]
        elif bone.name.startswith("DEF-pelvis.") or bone.name == "DEF-thigh.L" or bone.name == "DEF-thigh.R":
            bone.parent = bones["DEF-spine"]
        elif bone.name == "DEF-upper_arm.L" or bone.name == "DEF-upper_arm.R":
            bone.parent = bones["DEF-shoulder." + bone.name[-1:]]
        elif bone.use_deform:
            bone.bbone_segments = 1 # Game engines don't support bendy bones
        else:
            bones.remove(bone)

    for bone in context.object.pose.bones:
        c = bone.constraints
        while len(c) > 0:
            c.remove(c[0])
        set_lock(bone, False)

    for i in range(len(a.layers)):
        a.layers[i] = True


# My implementation of sliding joints on top of rigify
# Thanks to DanPro for the idea!
# https://www.youtube.com/watch?v=c7csuy-09k8

def sliding_joint_create(context, upper_bone, lower_bone, side):
    bones = context.object.data.edit_bones

    mch_name = "MCH-{}.{}".format(lower_bone, side)

    if mch_name in bones:
        raise Exception("Seems to already have sliding joint")

    tweak_name = "{}_tweak.{}".format(lower_bone, side)

    bone = bones["MCH-" + tweak_name]
    bone.parent = bone.parent.parent
    bone.name = "MCH-{}_tweak.{}.002".format(upper_bone, side)
    bone.align_orientation(bone.parent)
    bone.align_roll(bone.parent.z_axis)

    mch_layer = bone.layers

    bone = bones[tweak_name]
    bone.name = "{}_tweak.{}.002".format(upper_bone, side)
    tweak_tail = bone.tail
    tweak_layer = bone.layers
    tweak_size = bone.bbone_x
    bone.align_orientation(bone.parent)
    bone.align_roll(bone.parent.z_axis)

    bone = bones.new(mch_name)
    bone.parent = bones["ORG-{}.{}".format(lower_bone, side)]
    bone.use_connect = True
    bone.use_deform = False
    bone.tail = bone.parent.head
    org_roll = bone.parent.z_axis
    bone.align_roll(-org_roll)
    bone.layers = mch_layer
    bone.bbone_x = bone.parent.bbone_x
    bone.bbone_z = bone.parent.bbone_z
    mch_bone = bone

    bone = bones.new(tweak_name)
    bone.parent = mch_bone
    bone.head = mch_bone.tail
    bone.use_deform = False
    bone.tail = tweak_tail
    bone.align_orientation(mch_bone.parent)
    bone.align_roll(org_roll)
    bone.layers = tweak_layer
    bone.bbone_x = tweak_size
    bone.bbone_z = tweak_size

    bones["DEF-{}.{}".format(lower_bone, side)].use_connect = False

def sliding_joint_finalize(rig, upper_bone, lower_bone, side, influence):
    bones = rig.pose.bones

    mch_name = "MCH-{}.{}".format(lower_bone, side)
    tweak_name = "{}_tweak.{}".format(lower_bone, side)
    old_tweak = "{}_tweak.{}.002".format(upper_bone, side)

    obone = bones[old_tweak]
    del obone["rubber_tweak"] # DEF bones aren't connected anymore so rubber tweak is not possible now
    bone = bones[tweak_name]
    bone.custom_shape = obone.custom_shape
    bone.bone_group = obone.bone_group
    bone.lock_rotation = (True, False, True)
    bone.lock_scale = (False, True, False)

    set_lock(bones[mch_name], True)

    c = bones[mch_name].constraints.new("COPY_ROTATION")
    c.target = rig
    c.subtarget = "ORG-{}.{}".format(lower_bone, side)
    c.use_y = False
    c.use_z = False
    c.influence = influence
    c.owner_space = "LOCAL"
    c.target_space = "LOCAL"

    def replace_tweak(bone):
        for c in bone.constraints:
            if c.type == "COPY_TRANSFORMS" and c.target == rig and c.subtarget == old_tweak:
                c.subtarget = tweak_name

    replace_tweak(bones["DEF-{}.{}".format(lower_bone, side)])
    replace_tweak(bones["MCH-{}.001".format(tweak_name)])
