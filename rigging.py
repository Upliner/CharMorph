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

import logging, math, os
import numpy

import bpy                                   # pylint: disable=import-error
from mathutils import Vector, Matrix         # pylint: disable=import-error, no-name-in-module
from rna_prop_ui import rna_idprop_ui_create # pylint: disable=import-error, no-name-in-module

from . import yaml, utils

logger = logging.getLogger(__name__)

def get_joints(bones, is_all):
    joints = {}
    for bone in bones:
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

def all_joints(obj):
    return get_joints(obj.data.bones, True)
def selected_joints(context):
    return get_joints(context.object.data.edit_bones, False)

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
    def accumulate(data_item, _, co, gw):
        data_item[0] += gw.weight
        data_item[1] += co*gw.weight
    return get_vg_data(char, lambda: [0, Vector()], accumulate, verts)

def vg_names(file):
    if isinstance(file, str):
        file = numpy.load(file)
    return [n.decode("utf-8") for n in bytes(file["names"]).split(b'\0')]

def process_vg_file(file, callback):
    z = numpy.load(file)
    names = vg_names(z)
    i = 0
    idx = z["idx"]
    weights = z["weights"]
    for name, cnt in zip(names, z["cnt"]):
        i2 = i+cnt
        callback(name, zip(idx[i:i2], weights[i:i2]))
        i = i2

def char_rig_vg_names(char, rig):
    conf = char.armature.get(rig.data.get("charmorph_rig_type"))
    if conf:
        weights = conf.get("weights")
        if weights:
            try:
                return vg_names(char.path(weights))
            except:
                pass
    return []

def import_vg(obj, file, overwrite):
    def callback(name, data):
        if name in obj.vertex_groups:
            if overwrite:
                obj.vertex_groups.remove(obj.vertex_groups[name])
            else:
                return
        vg = obj.vertex_groups.new(name=name)
        for i, weight in data:
            vg.add([int(i)], weight, 'REPLACE')

    process_vg_file(file, callback)

def add_joints_from_file(verts, avg, file):
    def callback(name, data):
        if not name.startswith("joint_"):
            return
        if name in avg:
            return
        item = [0, Vector()]
        avg[name] = item
        for i, weight in data:
            item[0] += weight
            item[1] += verts[i].co*weight
    process_vg_file(file, callback)

class Rigger:
    def __init__(self, context, char, verts=None, jfile=None, opts=None):
        self.context = context
        self.locs = get_vg_avg(char, verts)
        if jfile:
            add_joints_from_file(verts, self.locs, jfile)
        self.opts = opts

    def get_opt(self, bone, opt):
        if self.opts:
            bo = self.opts.get(bone.name)
            if bo:
                val = bo.get(opt)
                if val:
                    return val
        return bone.get("charmorph_" + opt)

    def run(self, lst):
        result = True
        bones = set()
        edit_bones = self.context.object.data.edit_bones
        for name, (_, bone, attr) in lst.items():
            item = self.locs.get(name)
            if item and item[0] > 0.1:
                eb = edit_bones[bone.name]
                bones.add(eb)
                pos = item[1]/item[0]
                offs = self.get_opt(bone, "offs_" + attr)
                if offs and len(offs) == 3:
                    pos += Vector(tuple(offs))
                setattr(eb, attr, pos)
            else:
                logger.error("No vg for %s", name)
                if item:
                    logger.error(item[0])
                result = False

        # Bone roll
        for bone in bones:
            axis = self.get_opt(bone, "axis_z")
            flip = False
            if not axis:
                axis = self.get_opt(bone, "axis_x")
                flip = True
            if axis and len(axis) == 3:
                axis = Vector(tuple(axis))
                if flip:
                    axis = Matrix.Rotation(-math.pi/2, 4, bone.y_axis) @ axis
                bone.align_roll(axis)
        return result

bbone_attributes_pose = {
    'bbone_easein', 'bbone_easeout', 'bbone_rollin', 'bbone_rollout',
    'bbone_curveinx', 'bbone_curveiny', 'bbone_curveoutx', 'bbone_curveouty',
    'bbone_scaleinx', 'bbone_scaleiny', 'bbone_scaleoutx', 'bbone_scaleouty',
}
bbone_attributes_full = ['bbone_segments', 'bbone_handle_type_start', 'bbone_handle_type_end'] + list(bbone_attributes_pose)

def rigify_finalize(rig, char):
    vgs = char.vertex_groups
    bbones = []
    for bone in rig.data.bones:
        is_org = bone.name.startswith("ORG-")
        if is_org or bone.name.startswith("MCH-"):
            if bone.name in vgs:
                bone.use_deform = True
            if is_org:
                def_start = None
                def_end = None
                b = bone.bbone_custom_handle_start
                if b and b.name.startswith("ORG-"):
                    def_start = rig.data.bones.get("DEF-"+b.name[4:])
                b = bone.bbone_custom_handle_end
                if b and b.name.startswith("ORG-"):
                    def_end = rig.data.bones.get("DEF-"+b.name[4:])

                if def_start or def_end:
                    def_bone = rig.data.bones.get("DEF-"+bone.name[4:])
                    if def_bone.bbone_segments == 1:
                        bbones.append((def_bone.name, bone.name))
                        for attr in bbone_attributes_full:
                            setattr(def_bone, attr, getattr(bone, attr))
                    if def_start:
                        def_bone.bbone_custom_handle_start = def_start
                    if def_end:
                        def_bone.bbone_custom_handle_end = def_end

    for target, source in bbones:
        tbone = rig.pose.bones[target]
        sbone = rig.pose.bones[source]
        for attr in bbone_attributes_pose:
            setattr(tbone, attr, getattr(sbone, attr))

def reposition_armature_modifier(char):
    override = {"object": char}
    pos = len(char.modifiers)-1
    name = char.modifiers[pos].name

    i = 0
    for i, mod in enumerate(char.modifiers):
        if mod.type != "MASK":
            break
    for i in range(pos-i):
        if bpy.ops.object.modifier_move_up.poll(override):
            bpy.ops.object.modifier_move_up(override, modifier=name)

def unpack_tweaks(path, tweaks, editmode_tweaks=None, regular_tweaks=None, depth=0):
    if depth > 100:
        logger.error("Too deep tweaks loading: %s", repr(tweaks))
        return ([], [])

    if editmode_tweaks is None:
        editmode_tweaks = []
    if regular_tweaks is None:
        regular_tweaks = []

    if isinstance(tweaks, str):
        tweaks = [tweaks]

    if not isinstance(tweaks, list):
        if tweaks is not None:
            logger.error("Unknown tweaks format: %s", repr(tweaks))
        return ([], [])
    for tweak in tweaks:
        if isinstance(tweak, str):
            newpath = os.path.join(path, tweak)
            with open(newpath) as f:
                unpack_tweaks(os.path.dirname(newpath), yaml.safe_load(f), editmode_tweaks, regular_tweaks, depth+1)
        elif tweak.get("tweak") == "rigify_sliding_joint":
            editmode_tweaks.append(tweak)
            regular_tweaks.append(tweak)
        elif tweak.get("select") == "edit_bone" or tweak.get("tweak") in ["assign_parents", "align"]:
            editmode_tweaks.append(tweak)
        else:
            regular_tweaks.append(tweak)
    return (editmode_tweaks, regular_tweaks)

def constraint_by_type(bone, typ):
    for c in bone.constraints:
        if c.type == typ:
            return c
    return None

def constraint_by_target(bone, rig, typ, target):
    for c in bone.constraints:
        if c.type == typ and c.target == rig and c.subtarget == target:
            return c
    return None

def apply_editmode_tweak(context, tweak):
    t = tweak.get("tweak")
    edit_bones = context.object.data.edit_bones
    if t == "rigify_sliding_joint":
        sliding_joint_create(context, tweak["upper_bone"], tweak["lower_bone"], tweak["side"])
    elif t == "assign_parents":
        for k, v in tweak["bones"].items():
            edit_bones[k].parent = edit_bones[v]
    elif t == "align":
        for k, v in tweak["bones"].items():
            bone = edit_bones[k]
            target = edit_bones[v]
            bone.align_orientation(target)
            bone.roll = target.roll
    elif tweak.get("select") == "edit_bone":
        bone = edit_bones.get(tweak.get("bone"))
        if not bone:
            logger.error("Tweak bone not found: %s", tweak.get("bone"))
            return
        for attr, val in tweak.get("set").items():
            setattr(bone, attr, val)

def apply_tweak(rig, tweak):
    if tweak.get("tweak") == "rigify_sliding_joint":
        sliding_joint_finalize(rig, tweak["upper_bone"], tweak["lower_bone"], tweak["side"], tweak["influence"])
        return
    bone_name = tweak.get("bone")
    select = tweak.get("select")
    if select == "bone":
        obj = rig.data.bones.get(bone_name)
    elif select == "pose_bone":
        obj = rig.pose.bones.get(bone_name)
        add = tweak.get("add")
        if add is None:
            pass
        elif add == "constraint":
            obj = obj.constraints.new(tweak.get("type"))
            if hasattr(obj, "target"):
                obj.target = rig
        else:
            logger.error("Invalid add operator: %s", repr(tweak))
    elif select == "constraint":
        bone = rig.pose.bones.get(bone_name)
        obj = bone.constraints.get(tweak.get("name", ""))
        if not obj:
            obj = constraint_by_target(bone, rig, tweak.get("type"), tweak.get("target_bone"))
    else:
        logger.error("Invalid tweak select: %s", repr(tweak))
        return
    if not obj:
        logger.error("Tweak object not found: %s", repr(tweak))
        return
    for attr, val in tweak.get("set").items():
        setattr(obj, attr, val)

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
    bone.name = "MCH-{}_tweak.{}.002".format(upper_bone, side)

    mch_size = bone.bbone_x
    mch_layer = bone.layers

    bone = bones[tweak_name]
    bone.name = "{}_tweak.{}.002".format(upper_bone, side)
    tweak_tail = bone.tail
    tweak_layer = bone.layers
    tweak_size = bone.bbone_x

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

    bone = bones.new("MCH-{}_tweak.{}".format(lower_bone, side))
    bone.parent = mch_bone
    bone.use_connect = True
    bone.tail = tweak_tail
    bone.layers = mch_layer
    bone.bbone_x = mch_size
    bone.bbone_z = mch_size
    mch_bone = bone

    bone = bones.new(tweak_name)
    bone.parent = mch_bone
    bone.head = mch_bone.head
    bone.use_deform = False
    bone.tail = tweak_tail
    bone.align_roll(org_roll)
    bone.layers = tweak_layer
    bone.bbone_x = tweak_size
    bone.bbone_z = tweak_size

    lower_bone = bones["DEF-{}.{}".format(lower_bone, side)]
    lower_bone.use_connect = False

    bone = bones["DEF-{}.{}.001".format(upper_bone, side)]
    bone.bbone_handle_type_end = "TANGENT"
    bone.bbone_custom_handle_end = lower_bone

def sliding_joint_finalize(rig, upper_bone, lower_bone, side, influence):
    bones = rig.pose.bones

    mch_name = "MCH-{}.{}".format(lower_bone, side)
    tweak_name = "{}_tweak.{}".format(lower_bone, side)
    old_tweak = "{}_tweak.{}.002".format(upper_bone, side)

    obone = bones[old_tweak]
    bone = bones[tweak_name]
    bone.custom_shape = obone.custom_shape
    bone.bone_group = obone.bone_group
    bone.lock_rotation = (True, False, True)
    bone.lock_scale = (False, True, False)

    # Make rubber tweak property, but lock it to zero
    rna_idprop_ui_create(bone, "rubber_tweak", default=0, min=0, max=0)

    utils.lock_obj(bones[mch_name], True)

    c = bones[mch_name].constraints.new("COPY_ROTATION")
    c.target = rig
    c.subtarget = "ORG-{}.{}".format(lower_bone, side)
    c.use_y = False
    c.use_z = False
    c.influence = influence
    c.owner_space = "LOCAL"
    c.target_space = "LOCAL"

    c = bones["MCH-{}_tweak.{}".format(lower_bone, side)].constraints.new("COPY_SCALE")
    c.target = rig
    c.subtarget = "root"
    c.use_make_uniform = True

    def replace_tweak(bone):
        for c in bone.constraints:
            if c.type == "COPY_TRANSFORMS" and c.target == rig and c.subtarget == old_tweak:
                c.subtarget = tweak_name

    replace_tweak(bones["DEF-{}.{}".format(lower_bone, side)])
    replace_tweak(bones["MCH-{}.001".format(tweak_name)])
