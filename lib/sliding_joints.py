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
# Copyright (C) 2022 Michael Vigovsky
#
# It is my implementation of sliding joints on top of rigify
# Thanks to DanPro for the idea!
# https://www.youtube.com/watch?v=c7csuy-09k8
#

import re, math, typing, logging

import bpy  # pylint: disable=import-error
from rna_prop_ui import rna_idprop_ui_create  # pylint: disable=import-error, no-name-in-module

from . import charlib, utils

logger = logging.getLogger(__name__)
eval_unsafe = re.compile(r"__|\(\s*\)|[:;,{'\"\[]")

def _parse_joint(item: dict):
    side = item.get("side", "")
    if isinstance(side, list):
        for s in side:
            yield item["upper_bone"], item["lower_bone"], s
    else:
        yield item["upper_bone"], item["lower_bone"], side

class SJCalc:
    rig_name = ""
    influence: dict[str, dict[str, float]] = {}

    def __init__(self, char: charlib.Character, rig, get_co):
        self.rig = rig
        self.char = char
        self.get_co = get_co
        if rig:
            self.rig_name = rig.data.get("charmorph_rig_type")

        if self.rig_name:
            joints = self._rig_joints(self.rig_name)
            if joints:
                self.influence = {
                    self.rig_name:
                    {k: v for k, v in ((k, self._get_influence(v)) for k, v in joints.items()) if v is not None}
                }
        else:
            self.influence = {
                name: {k: self._calc_influence(v) for k, v in rig.sliding_joints.items()}
                for name, rig in self.char.armature.items() if rig.sliding_joints
            }

    def _rig_joints(self, rig):
        return self.char.armature.get(rig, charlib.Armature).sliding_joints

    def _calc_avg_dists(self, vert_pairs):
        if not vert_pairs:
            return 1
        return sum((self.get_co(a)-self.get_co(b)).length for a, b in vert_pairs)/len(vert_pairs)

    def _calc_influence(self, data):
        result = data.get("influence")
        if result is not None:
            return result
        calc = data["calc"]
        if not calc:
            return 0

        if isinstance(calc, str):
            # Check for eval safety. Attacks like 9**9**9 are still possible, but it's quite useless
            if eval_unsafe.search(calc):
                logger.error("bad calc: %s", calc)
                return 0
            calc = compile(calc, "", "eval")
            data["calc"] = calc

        vals = {}
        for k, v in data.items():
            if k.startswith("verts_"):
                vals[k] = self._calc_avg_dists(v)
        try:
            return eval(calc, {"__builtins__": None}, vals)
        except Exception as e:
            logger.error("bad calc: %s", e, exc_info=e)
            return 0

    def recalc(self):
        for rig, influence in self.influence.items():
            for k, v in self._rig_joints(rig).items():
                if k in influence and "calc" in v:
                    influence[k] = self._calc_influence(v)

    def _get_influence(self, item):
        for c in self._get_constraints(item):
            return c.influence
        return None

    def _get_constraints(self, joint):
        for _, lower_bone, side in _parse_joint(joint):
            bone = self.rig.pose.bones.get(f"MCH-{lower_bone}{side}")
            if not bone:
                continue
            c = bone.constraints
            if not c or c[0].type != "COPY_ROTATION":
                continue
            yield c[0]

    def _prop(self, rig, influence, name):
        joint = self._rig_joints(rig).get(name)

        def setter(_, value):
            influence[name] = value
            if self.rig:
                ok = False
                for c in self._get_constraints(joint):
                    c.influence = value
                    ok = True
                if not ok:
                    influence[name] = 0

        return bpy.props.FloatProperty(
            name="_".join((rig, name)),
            min=0, soft_max=0.2, max=1.0,
            precision=3,
            get=lambda _: influence.get(name, 0),
            set=setter
        )

    def rig_joints(self, rig):
        if self.rig_name:
            return self.rig_name, self.influence.get(self.rig_name, ())
        return rig, self._rig_joints(rig)

    def props(self):
        return (self._prop(rig, influence, name) for rig, influence in self.influence.items() for name in influence)

def create(context, upper_bone, lower_bone, side):
    bones = context.object.data.edit_bones

    mch_name = f"MCH-{lower_bone}{side}"

    if mch_name in bones:
        raise Exception("Seems to already have sliding joint")

    tweak_name = f"{lower_bone}_tweak{side}"

    bone = bones[f"MCH-{tweak_name}"]
    bone.name = f"MCH-{upper_bone}_tweak{side}.002"

    mch_size = bone.bbone_x
    mch_layer = bone.layers

    bone = bones[tweak_name]
    bone.name = f"{upper_bone}_tweak{side}.002"
    tweak_tail = bone.tail
    tweak_layer = bone.layers
    tweak_size = bone.bbone_x

    bone = bones.new(mch_name)
    bone.parent = bones[f"ORG-{lower_bone}{side}"]
    bone.use_connect = True
    bone.use_deform = False
    bone.tail = bone.parent.head
    org_roll = bone.parent.z_axis
    bone.align_roll(-org_roll)
    bone.layers = mch_layer
    bone.bbone_x = bone.parent.bbone_x
    bone.bbone_z = bone.parent.bbone_z
    mch_bone = bone

    bone = bones.new(f"MCH-{lower_bone}_tweak{side}")
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

    lower_bone = bones[f"DEF-{lower_bone}{side}"]
    lower_bone.use_connect = False

    bone = bones[f"DEF-{upper_bone}{side}.001"]
    bone.bbone_handle_type_end = "TANGENT"
    bone.bbone_custom_handle_end = lower_bone

def finalize(rig, upper_bone, lower_bone, side, influence):
    bones = rig.pose.bones

    mch_name = f"MCH-{lower_bone}{side}"
    tweak_name = f"{lower_bone}_tweak{side}"
    old_tweak = f"{upper_bone}_tweak{side}.002"

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
    c.subtarget = f"ORG-{lower_bone}{side}"
    c.use_y = False
    c.use_z = False
    c.influence = influence
    c.owner_space = "LOCAL"
    c.target_space = "LOCAL"

    c = bones[f"MCH-{lower_bone}_tweak{side}"].constraints.new("COPY_SCALE")
    c.target = rig
    c.subtarget = "root"
    c.use_make_uniform = True

    def replace_tweak(bone):
        for c in bone.constraints:
            if c.type == "COPY_TRANSFORMS" and c.target == rig and c.subtarget == old_tweak:
                c.subtarget = tweak_name

    replace_tweak(bones[f"DEF-{lower_bone}{side}"])
    replace_tweak(bones[f"MCH-{tweak_name}.001"])

    c = mch_bone.constraints.new("LIMIT_ROTATION")
    c.owner_space = "LOCAL"
    c.use_limit_x = True
    c.max_x = math.pi/2

def _parse_dict(data):
    return ((k, *result) for k, v in data.items() for result in _parse_joint(v))

def create_from_conf(sj_calc, conf) -> list[tuple[str, str, str, float]]:
    result = []
    for name, upper_bone, lower_bone, side in _parse_dict(conf.sliding_joints):
        influence = sj_calc.influence.get(conf.name, {}).get(name, 0)
        if influence > 0.0001:
            create(bpy.context, upper_bone, lower_bone, side)
            result.append((upper_bone, lower_bone, side, influence))
    return result
