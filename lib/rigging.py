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

import bpy                                   # pylint: disable=import-error
from mathutils import Vector, Quaternion     # pylint: disable=import-error, no-name-in-module

from . import sliding_joints, utils

logger = logging.getLogger(__name__)


class RigException(Exception):
    pass


def get_joints(bones, is_all):
    joints = {}
    for bone in bones:
        if is_all:
            if not bone.use_connect:
                joints[f"joint_{bone.name}_head"] = (bone.head, bone, "head")
        elif bone.select_head:
            if bone.use_connect:
                b = bone.parent
                joints[f"joint_{b.name}_tail"] = (b.tail, b, "tail")
            else:
                joints[f"joint_{bone.name}_head"] = (bone.head, bone, "head")
        if is_all or bone.select_tail:
            joints[f"joint_{bone.name}_tail"] = (bone.tail, bone, "tail")
    return joints


####
def all_joints(context):
    return get_joints(context.object.data.edit_bones, True)


def layer_joints(context, layer):
    return get_joints([bone for bone in context.object.data.edit_bones if bone.layers[layer]], True)


def selected_joints(context):
    return get_joints(context.object.data.edit_bones, False)
####


def bb_prev_roll(bone):
    if bone.use_endroll_as_inroll:
        p = bone.bbone_custom_handle_start
        if p:
            return p.bbone_rollout
    return 0


def bb_rollin_axis(bone, base_axis):
    axis = getattr(bone, f"{base_axis}_axis")
    axis.rotate(Quaternion(bone.y_axis, bone.bbone_rollin + bb_prev_roll(bone)))
    return axis


def bb_rollout_axis(bone, base_axis):
    p = bone.bbone_custom_handle_end
    if p:
        axis = getattr(p, f"{base_axis}_axis")
        y_axis = p.y_axis
    else:
        axis = getattr(bone, f"{base_axis}_axis")
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
        if roll < 0:
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
        self.jdata = utils.get_vg_avg(char, verts)

    def joints_from_file(self, file, verts):
        for name, idx, weights in utils.vg_read(file):
            if not name.startswith("joint_"):
                continue
            item = [0, Vector()]
            self.jdata[name] = item
            for i, weight in zip(idx, weights):
                item[0] += weight
                item[1] += Vector(verts[i]) * weight

    def set_opts(self, opts):
        if not opts:
            return
        if "bones" not in opts and "groups" not in opts and "default" not in opts:
            self.opts.update(opts)  # Legacy bones format
            return
        self.default_opts.update(opts.get("default", ""))
        self.opts.update(opts.get("bones", ""))
        for g in opts.get("groups", ""):
            g_opts = g.get("opts", {})
            for b in g.get("bones", ""):
                self.opts[b] = g_opts.copy()

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
        item = self.jdata.get(f"joint_{bone.name}_{attr}")
        if not item or item[0] < 1e-10:
            return None
        pos = item[1] / item[0]
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
            value = self.get_opt(bone, f"{prefix}axis_{axis}")
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
                logger.error("Align bone %s is not found", align)
                self.result = False

            vector, axis = self.get_roll(bone, "")
            if vector:
                if axis == "x":
                    vector.rotate(Quaternion(bone.y_axis, -math.pi / 2))
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
                    bb_align_roll(bone, *self.get_roll(bone, f"bb_{inout}_"), inout)
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
# bbone attributes like bbone_curveiny were changed to bbone_curveinz in Blender 3.0
def __blender3_bbone_attributes():
    props = bpy.types.Bone.bl_rna.properties
    for i, attr in enumerate(bbone_attributes):
        if not attr not in props and attr.endswith("y"):
            bbone_attributes[i] = attr[:-1] + "z"
__blender3_bbone_attributes()


def rigify_finalize(rig, char):
    vgs = char.vertex_groups
    for bone in rig.data.bones:
        is_org = bone.name.startswith("ORG-")
        if is_org or bone.name.startswith("MCH-"):
            if bone.name in vgs:
                bone.use_deform = True
            if is_org:
                handles = [bone.bbone_custom_handle_start, bone.bbone_custom_handle_end]
                for i, b in enumerate(handles):
                    if b and b.name.startswith("ORG-"):
                        handles[i] = rig.data.bones.get("DEF-" + b.name[4:], b)

                if any(handles):
                    def_bone = rig.data.bones.get("DEF-" + bone.name[4:], bone)
                    if def_bone is not bone and (def_bone.bbone_segments == 1 or def_bone.bbone_handle_type_start == "AUTO"):
                        for attr in bbone_attributes:
                            setattr(def_bone, attr, getattr(bone, attr))
                    if handles[0]:
                        def_bone.bbone_custom_handle_start = handles[0]
                    if handles[1]:
                        def_bone.bbone_custom_handle_end = handles[1]
    # Set ease in/out for pose bones or not?


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
            with open(newpath, "r", encoding="utf-8") as f:
                unpack_tweaks(os.path.dirname(newpath), utils.load_yaml(f), stages, depth + 1)
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
        logger.warning("Legacy sliding_joint tweak is used")
        sliding_joints.create(context, tweak["upper_bone"], tweak["lower_bone"], "." + tweak["side"])
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
        logger.warning("Legacy sliding_joint tweak is used")
        sliding_joints.finalize(rig, tweak["upper_bone"], tweak["lower_bone"], "." + tweak["side"], tweak["influence"])
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
