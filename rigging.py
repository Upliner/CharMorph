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

def apply_tweaks(rig, tweaks, depth=0):
    if depth>100:
        logger.error("Too deep tweaks loading: " + repr(tweaks))
        return

    if isinstance(tweaks, str):
        tweaks = [tweaks]

    if not isinstance(tweaks, list):
        if tweaks is not None:
            logger.error("Unknown tweaks format: " + repr(tweaks))
        return
    for tweak in tweaks:
        apply_tweak(rig, tweak, depth)

def constraint_by_target(bone, rig, type, target):
    for c in bone.constraints:
        if c.type == type and c.target == rig and c.subtarget == target:
            return c

def apply_tweak(rig, tweak, depth=0):
    if not rig.pose:
        logger.error("No pose in rig " + repr(rig) + " no tweaks were applied")
        return
    if isinstance(tweak, str):
        char_name = rig.data["charmorph_template"]
        with open(library.char_file(char_name, tweak)) as f:
            apply_tweaks(rig, yaml.safe_load(f), depth+1)
        return
    bone_name = tweak.get("bone")
    bone = rig.pose.bones.get(bone_name,"")
    if not bone:
        logger.error("Tweak bone not found: " + bone_name)
        return
    select = tweak.get("select")
    if select=="bone":
        add = tweak.get("add","")
        if add != "constraint":
            logger.error("bone select must contain add constraint operator: " + add)
        constraint = bone.constraints.new(tweak.get("type"))
        if hasattr(constraint, "target"):
            constraint.target = rig
    elif select == "constraint":
        constraint = bone.constraints.get(tweak.get("name",""))
        if not constraint:
            constraint = constraint_by_target(bone, rig, tweak.get("type"), tweak.get("target_bone"))
    else:
        logger.error("Invalid tweak select: " + select)
        return
    if not constraint:
        logger.error("Constraint not found: name: " + repr(tweak))
        return
    for attr, val in tweak.get("set").items():
        setattr(constraint, attr, val)

def make_gaming_rig(context, char):
    a = context.object.data
    bones = a.edit_bones
    for bone in list(bones):
        if bone.name == "ORG-face":
            bone.parent = bones["DEF-spine.006"]
        elif bone.use_deform:
            bone.bbone_segments = 1 # Game engines don't support bendy bones
        else:
            bones.remove(bone)

    for bone in context.object.pose.bones:
        c = bone.constraints
        while len(c) > 0:
            c.remove(c[0])
        bone.lock_ik_x = False
        bone.lock_ik_y = False
        bone.lock_ik_z = False
        bone.lock_location = (False, False, False)
        bone.lock_rotation = (False, False, False)
        bone.lock_rotation_w = False
        bone.lock_rotations_4d = False
        bone.lock_scale = (False, False, False)

    for i in range(len(a.layers)):
        a.layers[i] = True
