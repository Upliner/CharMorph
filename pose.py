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

import logging, re
import bpy

from . import library

from mathutils import Matrix, Vector

logger = logging.getLogger(__name__)

m1 = Matrix.Identity(4)
m2 = m1.copy()
m2[1][1] = -1
m2[3][3] = -1
flip_x_z = {
    "L":Matrix(((1,0,0,0),(0,0,0, 1),(0,0,-1,0),(0,-1,0,0))),
    "R":Matrix(((1,0,0,0),(0,0,0,-1),(0,0, 1,0),(0, 1,0,0))),
}
def qrotation(mat):
    def rot(v):
        return (v[3],v[0],v[1],v[2])
    return Matrix((rot(mat[3]),rot(mat[0]),rot(mat[1]),rot(mat[2])))

shoulder_angle = 1.3960005939006805
shoulder_rot = {
    "L":qrotation(Matrix.Rotation( shoulder_angle, 4, (0,1,0))),
    "R":qrotation(Matrix.Rotation(-shoulder_angle, 4, (0,1,0))),
}

bone_map = {
    "root":   ("root", m2),
    "pelvis": ("torso", qrotation(Matrix.Rotation(1.4466689567595232,4,(1,0,0)))),
    "spine01": ("spine_fk.001", m1),
    "spine02": ("spine_fk.002", m1),
    "spine03": ("spine_fk.003", m1),
    "neck": ("neck", m1),
    "head": ("head", m1),
}

for side in ["L", "R"]:
    bone_map["thigh_" + side] = ("thigh_fk." + side, m2)
    bone_map["calf_" + side] = ("shin_fk." + side, m2)
    bone_map["foot_" + side] = ("foot_fk." + side, m2)
    bone_map["toes_" + side] = ("toe." + side, m1)
    bone_map["breast_" + side] = ("breast." + side, m1)
    bone_map["clavicle_" + side] = ("shoulder." + side, shoulder_rot[side])
    bone_map["upperarm_" + side] = ("upper_arm_fk." + side, m1)
    bone_map["lowerarm_" + side] = ("forearm_fk." + side, m1)
    bone_map["hand_" + side] = ("hand_fk." + side, flip_x_z[side])
    for i in range(1,4):
        is_master = "_master" if i==1 else ""
        bone_map["thumb0%d_%s" % (i, side)] = ("thumb.0%d%s.%s" % (i, is_master, side), m2)
        for finger in ["index", "middle", "ring", "pinky"]:
            bone_map["%s0%d_%s" % (finger, i, side)] = ("f_%s.0%d%s.%s" % (finger, i, is_master, side), m2)


# Different rigify versions use different parameters for IK2FK so we need to scan its modules

ik2fk_map = {}

re_rigid = re.compile(r'^rig_id = "([0-9a-z]*)"$', re.MULTILINE)

def scan_rigify_modules():
    for t in bpy.data.texts:
        s = t.as_string()
        m = re_rigid.search(s)
        if not m:
            continue
        rig_id = m.group(1)
        limbs = []
        s = s[m.end(0)+1:]
        re_operator = re.compile(r"^( *)props = [0-9a-z_]*\.operator\('pose.rigify_limb_ik2fk_%s'" % rig_id, re.MULTILINE)

        while True:
            m = re_operator.search(s)
            if not m:
                break
            indent = m.group(1)
            re_prop = re.compile(r"%sprops.([0-9a-z_]*) = '([^']*)'$" % indent)
            props = {}
            while True:
                s = s[m.end(0):]
                s = s[s.find("\n")+1:]
                line = s[:s.find("\n")]
                m = re_prop.match(line)
                if not m:
                    break
                props[m.group(1)] = m.group(2)
            if len(props)>0:
                limbs.append(props)
        if len(limbs)>0:
            ik2fk_map[rig_id] = limbs

def apply_pose(ui, context):
    if not ui.pose or ui.pose==" ":
        return
    rig = context.active_object
    pose = library.obj_char(rig).poses.get(ui.pose)
    if not pose:
        logger.error("pose not found %s %s", ui.pose, rig)
        return
    rig_id = rig.data["rig_id"]

    # Some settings
    ik_fk = {}
    rig.pose.bones["torso"]["neck_follow"] = 1.0
    rig.pose.bones["torso"]["head_follow"] = 1.0
    for side in ["L","R"]:
        for limb in ["upper_arm","thigh"]:
            bone = rig.pose.bones["{}_parent.{}".format(limb, side)]
            bone["fk_limb_follow"] = 0.0
            ik_fk[bone.name] = bone.get("IK_FK", 1.0)
            bone["IK_FK"] = 1.0

    # TODO: different mix modes
    override = context.copy()
    old_mode = context.mode
    bpy.ops.object.mode_set(override,mode="POSE")
    bpy.ops.pose.select_all(override,action="SELECT")
    bpy.ops.pose.loc_clear(override)
    bpy.ops.pose.rot_clear(override)
    bpy.ops.pose.scale_clear(override)
    bpy.ops.object.mode_set(override,mode=old_mode)

    for k, v in pose.items():
        name, matrix = bone_map.get(k,("", None))
        target_bone = rig.pose.bones.get(name)
        if not target_bone:
            logger.debug("no target for " + k)
            continue
        target_bone.rotation_mode="QUATERNION"
        target_bone.rotation_quaternion = matrix @ Vector(v)

    spine_fk = rig.pose.bones.get("spine_fk")
    spine_fk1 = rig.pose.bones.get("spine_fk.001")
    spine_fk2 = rig.pose.bones.get("spine_fk.002")

    if spine_fk and spine_fk1 and spine_fk2:
        q = spine_fk1.rotation_quaternion
        spine_fk.rotation_quaternion = [-q[0], q[1], q[2], q[3]]
        spine_fk2.rotation_quaternion @= q

    if hasattr(context, "evaluated_depsgraph_get"):
        # Calculate lowest point for sitting and similiar poses
        erig = rig.evaluated_get(context.evaluated_depsgraph_get())
        torso = rig.pose.bones.get("torso")
        min_z = torso.head[2]
        for bone in erig.pose.bones:
            if not bone.name.startswith("ORG-"):
                continue
            for attr in ["head","tail"]:
                val = getattr(bone, attr)
                if val[2] < min_z:
                    min_z = val[2]
        min_z = max(min_z, 0)
        if torso:
            torso.location = (0, 0, -min_z)

    ik2fk_operator = None
    ik2fk_limbs = None

    if ui.pose_ik2fk:
        try:
            op = getattr(bpy.ops.pose, "rigify_limb_ik2fk_" + rig_id)
            if op.poll():
                ik2fk_operator = op
                if rig_id not in ik2fk_map:
                    scan_rigify_modules()
                ik2fk_limbs = ik2fk_map[rig_id]
                if not ik2fk_limbs:
                    logger.error("CharMorph doesn't support IK2FK for your Rigify version")
        except:
            logger.error("Rigify UI doesn't seem to be available. IK2FK is disabled")
            pass
    if ik2fk_operator and ik2fk_limbs:
        fail=False
        for limb in ik2fk_limbs:
            result = ik2fk_operator(**limb)
            if "FINISHED" not in result:
                fail = True

        if fail:
            logger.error("IK2FK failed")
        else:
            for k, v in ik_fk.items():
                rig.pose.bones[k]["IK_FK"] = v

def poll(cls, context):
    if not (context.mode in ["OBJECT", "POSE"] and context.active_object and
        context.active_object.type == "ARMATURE" and
        context.active_object.data.get("rig_id")):
        return False
    char = library.obj_char(context.active_object)
    return len(char.poses) > 0

class OpApplyPose(bpy.types.Operator):
    bl_idname = "charmorph.apply_pose"
    bl_label = "Apply pose"
    bl_description = "Apply selected pose"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return poll(cls, context)

    def execute(self, context):
        apply_pose(context.window_manager.charmorph_ui, context)
        return {"FINISHED"}

class CHARMORPH_PT_Pose(bpy.types.Panel):
    bl_label = "Pose"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 10

    @classmethod
    def poll(cls, context):
        return poll(cls, context)

    def draw(self, context):
        self.layout.prop(context.window_manager.charmorph_ui, "pose_ik2fk")
        self.layout.prop(context.window_manager.charmorph_ui, "pose")
        self.layout.operator("charmorph.apply_pose")

classes = [CHARMORPH_PT_Pose, OpApplyPose]
