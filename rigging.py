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
from mathutils import Vector, Quaternion     # pylint: disable=import-error, no-name-in-module
from rna_prop_ui import rna_idprop_ui_create # pylint: disable=import-error, no-name-in-module

from . import yaml, utils

logger = logging.getLogger(__name__)

class RigException(Exception):
    pass

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

def all_joints(context):
    return get_joints(context.object.data.edit_bones, True)
def layer_joints(context, layer):
    return get_joints([bone for bone in context.object.data.edit_bones if bone.layers[layer]], True)
def selected_joints(context):
    return get_joints(context.object.data.edit_bones, False)

def get_vg_data(char, new, accumulate, verts=None):
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

def get_vg_avg(char, verts=None):
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
        weights = conf.weights
        if weights:
            try:
                return vg_names(char.path(weights))
            except:
                pass
    return []

def import_vg(obj, file, overwrite):
    names = set()
    def callback(name, data):
        names.add(name)
        if name in obj.vertex_groups:
            if overwrite:
                obj.vertex_groups.remove(obj.vertex_groups[name])
            else:
                return
        vg = obj.vertex_groups.new(name=name)
        for i, weight in data:
            vg.add([int(i)], weight, 'REPLACE')

    process_vg_file(file, callback)
    return names

def bb_prev_roll(bone):
    if bone.use_endroll_as_inroll:
        p = bone.bbone_custom_handle_start
        if p:
            return p.bbone_rollout
    return 0

def bb_rollin_axis(bone, base_axis):
    axis = getattr(bone, "%s_axis" % base_axis)
    axis.rotate(Quaternion(bone.y_axis, bone.bbone_rollin + bb_prev_roll(bone)))
    return axis

def bb_rollout_axis(bone, base_axis):
    p = bone.bbone_custom_handle_end
    if p:
        axis = getattr(p, "%s_axis" % base_axis)
        y_axis = p.y_axis
    else:
        axis = getattr(bone, "%s_axis" % base_axis)
        y_axis = bone.y_axis
    axis.rotate(Quaternion(y_axis, bone.bbone_rollout))
    return axis

def bb_align_roll(bone, vec, axis, inout):
    if not vec:
        return
    x_axis = bone.x_axis
    y_axis = bone.y_axis
    z_axis = bone.z_axis

    if inout == "out":
        p = bone.bbone_custom_handle_end
        if p:
            x_axis = p.x_axis
            y_axis = p.y_axis
            z_axis = p.z_axis

    vec -= vec.project(y_axis)
    vec.normalize()

    if axis == "z":
        axis1 = x_axis
        axis2 = z_axis
    else:
        axis1 = -z_axis
        axis2 = x_axis

    roll = math.asin(max(min(vec.dot(axis1), 1), -1))
    if vec.dot(axis2) < 0:
        if roll<0:
            roll = -math.pi - roll
        else:
            roll = math.pi - roll

    if inout == "in":
        roll -= bb_prev_roll(bone)

    setattr(bone, "bbone_roll" + inout, roll)

class Rigger:
    def __init__(self, context):
        self.context = context
        self.jdata = {}
        self.opts = {}
        self.default_opts = {}

        self.result = True
        self._bones = None

    def joints_from_char(self, char, verts=None):
        self.jdata = get_vg_avg(char, verts)

    def joints_from_file(self, file, verts):
        def callback(name, data):
            if not name.startswith("joint_"):
                return
            if name in self.jdata:
                return
            item = [0, Vector()]
            self.jdata[name] = item
            for i, weight in data:
                item[0] += weight
                item[1] += verts[i].co*weight
        process_vg_file(file, callback)

    def set_opts(self, opts):
        if not opts:
            return
        self.opts.clear()
        if "bones" not in opts and "groups" not in opts and "default" not in opts:
            self.opts.update(opts) # Legacy bones format
            return
        self.default_opts.update(opts.get("default", ""))
        self.opts.update(opts.get("bones", ""))
        for g in opts.get("groups", ""):
            g_opts = g.get("opts", {})
            for b in g.get("bones",""):
                self.opts[b] = g_opts.copy()

    def configure(self, conf, obj, verts):
        if conf.joints:
            self.joints_from_file(conf.joints, verts)
        else:
            self.joints_from_char(obj, verts)
        self.set_opts(conf.bones)

    def get_opt(self, bone, opt):
        if self.opts or self.default_opts:
            bo = self.opts.get(bone.name)
            if bo is None:
                bo = self.default_opts
            if bo:
                val = bo.get(opt)
                if val:
                    return val
        return bone.get("charmorph_" + opt)

    def _set_opt(self, bone_name, opt, value):
        bo = self.opts.get(bone_name)
        if bo:
            bo[opt] = value
        else:
            self.opts[bone_name] = {opt: value}

    def _save_attr(self, bone, opt, get_value):
        if self.get_opt(bone, opt) == "keep":
            self._set_opt(bone.name, opt, get_value(bone))

    def _save_bone_data(self, bone):
        if bone in self._bones:
            return
        self._bones.add(bone)

        self._save_attr(bone, "axis_x", lambda bone: bone.x_axis)
        self._save_attr(bone, "axis_z", lambda bone: bone.z_axis)

        self._save_attr(bone, "bb_in_axis_x", lambda bone: bb_rollin_axis(bone, "x"))
        self._save_attr(bone, "bb_in_axis_z", lambda bone: bb_rollin_axis(bone, "z"))

        self._save_attr(bone, "bb_out_axis_x", lambda bone: bb_rollout_axis(bone, "x"))
        self._save_attr(bone, "bb_out_axis_z", lambda bone: bb_rollout_axis(bone, "z"))

    def joint_position(self, bone, attr):
        if attr == "head" and utils.is_true(self.get_opt(bone, "connected")) and bone.parent:
            bone = bone.parent
            attr = "tail"
        item = self.jdata.get("joint_%s_%s" % (bone.name, attr))
        if not item or item[0] < 0.1:
            return None
        pos = item[1]/item[0]
        offs = self.get_opt(bone, "offs_" + attr)
        if offs and len(offs) == 3:
            pos += Vector(tuple(offs))
        return pos

    def _set_bone_pos(self, lst):
        edit_bones = self.context.object.data.edit_bones
        for _, bone, _ in lst.values():
            edit_bone = edit_bones[bone.name]
            self._save_bone_data(edit_bone)
        for _, bone, attr in lst.values():
            pos = self.joint_position(bone, attr)
            if pos:
                edit_bone = edit_bones[bone.name]
                setattr(edit_bone, attr, pos)
            else:
                logger.error("No data for joint %s_%s", bone.name, attr)
                self.result = False

    def get_roll(self, bone, prefix):
        for axis in ("z", "x"):
            value = self.get_opt(bone, prefix + "axis_" + axis)
            if value and len(value) == 3:
                return Vector(value), axis
        return None, None

    def _post_process_bones(self):
        edit_bones = self.context.object.data.edit_bones
        bbones = {}
        for bone in self._bones:
            if bone.bbone_segments > 1:
                bbones[bone] = {}
            align = self.get_opt(bone, "align")
            if align:
                align_bone = edit_bones.get(align)
                if align_bone:
                    bone.align_orientation(align_bone)
                    continue
                else:
                    logger.error("Align bone %s is not found", align)
                    self.result = False

            vector, axis = self.get_roll(bone, "")
            if vector:
                if axis == "x":
                    vector.rotate(Quaternion(bone.y_axis, -math.pi/2))
                bone.align_roll(vector)

        # Calculate bbone order. Parents need to be processed before childen
        to_remove = []
        for bone, children in bbones.items():
            if not bone.use_endroll_as_inroll:
                continue
            parent = bone.bbone_custom_handle_start
            if not parent:
                continue
            d = bbones.get(parent)
            if d is None:
                continue
            d[bone] = children
            to_remove.append(bone)

        for bone in to_remove:
            del bbones[bone]

        def walk(bone_tree):
            for bone, children in bone_tree.items():
                for inout in ("in", "out"):
                    bb_align_roll(bone, *self.get_roll(bone, "bb_%s_" % inout), inout)
                walk(children)
        walk(bbones)

    def run(self, lst=None):
        if lst is None:
            lst = all_joints(self.context)

        self.result = True
        self._bones = set()
        self._set_bone_pos(lst)
        self._post_process_bones()
        self._bones = None

        return self.result

bbone_attributes = [
    'bbone_segments', 'use_endroll_as_inroll',
    'bbone_handle_type_start', 'bbone_handle_type_end',
    'bbone_easein', 'bbone_easeout', 'bbone_rollin', 'bbone_rollout',
    'bbone_curveinx', 'bbone_curveiny', 'bbone_curveoutx', 'bbone_curveouty',
]

ATTR_CHECKED=False

def check_attributes(bone):
    # bbone attributes like bbone_curveiny were changed to bbone_curveinz in Blender 3.0 Alpha
    global ATTR_CHECKED
    if ATTR_CHECKED:
        return
    for i, attr in enumerate(bbone_attributes):
        if not hasattr(bone, attr) and attr.endswith("y"):
            bbone_attributes[i] = attr[:-1] + "z"

    ATTR_CHECKED = True

def rigify_finalize(rig, char):
    vgs = char.vertex_groups
    if len(rig.data.bones) > 0:
        check_attributes(rig.data.bones[0])
    for bone in rig.data.bones:
        is_org = bone.name.startswith("ORG-")
        if is_org or bone.name.startswith("MCH-"):
            if bone.name in vgs:
                bone.use_deform = True
            if is_org:
                handles = [bone.bbone_custom_handle_start, bone.bbone_custom_handle_end]
                for i, b in enumerate(handles):
                    if b and b.name.startswith("ORG-"):
                        handles[i] = rig.data.bones.get("DEF-"+b.name[4:], b)

                if any(handles):
                    def_bone = rig.data.bones.get("DEF-"+bone.name[4:], bone)
                    if def_bone is not bone and (def_bone.bbone_segments == 1 or def_bone.bbone_handle_type_start == "AUTO"):
                        for attr in bbone_attributes:
                            setattr(def_bone, attr, getattr(bone, attr))
                    if handles[0]:
                        def_bone.bbone_custom_handle_start = handles[0]
                    if handles[1]:
                        def_bone.bbone_custom_handle_end = handles[1]

    # Set ease in/out for pose bones or not?

def reposition_modifier(obj, i):
    override = {"object": obj}
    pos = len(obj.modifiers)-1
    name = obj.modifiers[pos].name

    for _ in range(pos-i):
        if bpy.ops.object.modifier_move_up.poll(override):
            bpy.ops.object.modifier_move_up(override, modifier=name)

def reposition_armature_modifier(char):
    for i, mod in enumerate(char.modifiers):
        if mod.type != "ARMATURE":
            reposition_modifier(char, i)
            return

def reposition_cs_modifier(char):
    i = len(char.modifiers)-1
    while i>=0:
        if char.modifiers[i].type == "ARMATURE":
            reposition_modifier(char, i+1)
            return
        i -= 1

def reposition_subsurf_modifier(char):
    i = len(char.modifiers)-1
    while i>=0:
        if char.modifiers[i].type in ["ARMATURE", "CORRECTIVE_SMOOTH", "MASK"]:
            reposition_modifier(char, i+1)
            return
        i -= 1

def unpack_tweaks(path, tweaks, stages=None, depth=0):
    if depth > 100:
        logger.error("Too deep tweaks loading: %s", repr(tweaks))
        return ([], [], [])

    if stages is None:
        stages = ([], [], [])

    if isinstance(tweaks, str):
        tweaks = [tweaks]

    if not isinstance(tweaks, list):
        if tweaks is not None:
            logger.error("Unknown tweaks format: %s", repr(tweaks))
        return ([], [], [])
    for tweak in tweaks:
        if isinstance(tweak, str):
            newpath = os.path.join(path, tweak)
            with open(newpath) as f:
                unpack_tweaks(os.path.dirname(newpath), yaml.safe_load(f), stages, depth+1)
        elif tweak.get("stage") == "pre":
            stages[0].append(tweak)
        elif tweak.get("tweak") == "rigify_sliding_joint":
            stages[1].append(tweak)
            stages[2].append(tweak)
        elif tweak.get("select") == "edit_bone" or tweak.get("tweak") in ["assign_parents", "align"]:
            stages[1].append(tweak)
        else:
            stages[2].append(tweak)
    return stages

def find_constraint(bone, rig, typ, target):
    for c in bone.constraints:
        if c.type == typ and c.target == rig and (target is None or c.subtarget == target):
            return c
    return None

def parse_layers(val):
    if isinstance(val, list) and len(val) == 32 and isinstance(val[0], bool):
        return val
    if not isinstance(val, list):
        val = [val]
    result = [False] * 32
    for item in val:
        result[item] = True
    return result

def calc_vector(vec, bone):
    if not vec or len(vec) != 3:
        return vec
    for i, item in enumerate(vec):
        if item == "len":
            vec[i] = bone.length
        elif item == "-len":
            vec[i] = -bone.length
    return vec

def extrude_if_necessary(edit_bones, bone, params):
    if not params:
        return bone
    vec = Vector(calc_vector(params.get("local", (0, 0, 0)), bone))
    normalvec = calc_vector(params.get("normal"), bone)
    if normalvec:
        vec += bone.matrix.to_3x3() @ Vector(normalvec)

    new_bone = edit_bones.new(bone.name)
    new_bone.parent = bone
    for attr in ["roll", "bbone_x", "bbone_z"]:
        setattr(new_bone, attr, getattr(bone, attr))
    new_bone.tail = bone.tail + vec
    new_bone.use_deform = False
    new_bone.use_connect = True
    return new_bone

def process_bone_actions(edit_bones, bone, tweak):
    if tweak.get("action") == "copy":
        new_bone = edit_bones.new(bone.name)
        for attr in ["head", "tail", "roll", "bbone_x", "bbone_z", "use_deform"]:
            setattr(new_bone, attr, getattr(bone, attr))
        return new_bone

    return extrude_if_necessary(edit_bones, bone, tweak.get("extrude"))

def apply_editmode_tweak(context, tweak):
    t = tweak.get("tweak")
    edit_bones = context.object.data.edit_bones
    if t == "rigify_sliding_joint":
        sliding_joint_create(context, tweak["upper_bone"], tweak["lower_bone"], tweak["side"])
    elif t == "assign_parents":
        for k, v in tweak["bones"].items():
            if v:
                v = edit_bones[v]
            edit_bones[k].parent = v
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
        bone = process_bone_actions(edit_bones, bone, tweak)
        for attr, val in tweak.get("set", {}).items():
            if attr == "layers":
                setattr(bone, attr, parse_layers(val))
            else:
                setattr(bone, attr, val)

def apply_tweak(rig, tweak):
    if tweak.get("tweak") == "rigify_sliding_joint":
        sliding_joint_finalize(rig, tweak["upper_bone"], tweak["lower_bone"], tweak["side"], tweak["influence"])
        return

    select = tweak.get("select")
    if select == "bone":
        bones = rig.data.bones
    else:
        bones = rig.pose.bones

    obj = bones.get(tweak.get("bone"))

    if select == "pose_bone":
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
        bone = obj
        obj = bone.constraints.get(tweak.get("name", ""))
        if not obj:
            obj = find_constraint(bone, rig, tweak.get("type"), tweak.get("target_bone"))
    elif select != "bone":
        logger.error("Invalid tweak select: %s", repr(tweak))
        return
    if not obj:
        logger.error("Tweak object not found: %s", repr(tweak))
        return
    if tweak.get("action") == "remove":
        bone.constraints.remove(obj)
        return
    for attr, val in tweak.get("set", {}).items():
        if val and attr.startswith("bbone_custom_handle_"):
            val = bones[val]
        if isinstance(val, dict) and attr == "targets" and isinstance(obj, bpy.types.ArmatureConstraint):
            for k, v in val.items():
                t = obj.targets.new()
                t.target = rig
                t.subtarget = k
                t.weight = v
            continue
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

    mch_bone = bones[mch_name]
    utils.lock_obj(mch_bone, True)

    c = mch_bone.constraints.new("COPY_ROTATION")
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

    c = mch_bone.constraints.new("LIMIT_ROTATION")
    c.owner_space = "LOCAL"
    c.use_limit_x = True
    c.max_x = 90
