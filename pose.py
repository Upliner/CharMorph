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

import logging
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
    bone_map["thigh_%s" % side] = ("thigh_fk.%s" % side, m2)
    bone_map["calf_%s" % side] = ("shin_fk.%s" % side, m2)
    bone_map["foot_%s" % side] = ("foot_fk.%s" % side, m2)
    bone_map["toes_%s" % side] = ("toe.%s" % side, m1)
    bone_map["breast_%s" % side] = ("breast.%s" % side, m1)
    bone_map["clavicle_%s" % side] = ("shoulder.%s" % side, shoulder_rot[side])
    bone_map["upperarm_%s" % side] = ("upper_arm_fk.%s" % side, m1)
    bone_map["lowerarm_%s" % side] = ("forearm_fk.%s" % side, m1)
    bone_map["hand_%s" % side] = ("hand_fk.%s" % side, flip_x_z[side])
    for i in range(1,4):
        is_master = "_master" if i==1 else ""
        bone_map["thumb0%d_%s" % (i, side)] = ("thumb.0%d%s.%s" % (i, is_master, side), m2)
        for finger in ["index", "middle", "ring", "pinky"]:
            bone_map["%s0%d_%s" % (finger, i, side)] = ("f_%s.0%d%s.%s" % (finger, i, is_master, side), m2)

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
        min_z = 1
        def check_bone(name):
            nonlocal min_z
            b = erig.pose.bones.get(name)
            if not b:
                return
            for attr in ["head","tail"]:
                val = getattr(b, attr)
                if val[2] < min_z:
                    min_z = val[2]
        check_bone("head")
        for side in ["L","R"]:
            for bone in ["ORG-heel.02","toe","hand"]:
                check_bone("%s.%s" % (bone, side))
        min_z = max(min_z, 0)
        torso = rig.pose.bones.get("torso")
        if torso:
            torso.location = (0, 0, -min_z)

    ik2fk = None
    if ui.pose_ik2fk:
        ik2fk_attr = "rigify_limb_ik2fk_" + rig_id
        if hasattr(bpy.ops.pose, ik2fk_attr):
            ik2fk = getattr(bpy.ops.pose, ik2fk_attr)
    if ik2fk:
        for side in ["L","R"]:
            ik2fk(prop_bone='upper_arm_parent.' + side,
                fk_bones='["upper_arm_fk.{0}", "forearm_fk.{0}", "hand_fk.{0}"]'.format(side),
                ik_bones = '["upper_arm_ik.{0}", "MCH-forearm_ik.{0}", "MCH-upper_arm_ik_target.{0}"]'.format(side),
                ctrl_bones = '["upper_arm_ik.{0}", "hand_ik.{0}", "upper_arm_ik_target.{0}"]'.format(side),
                extra_ctrls = '[]')
            ik2fk(prop_bone='thigh_parent.' + side,
                fk_bones='["thigh_fk.{0}", "shin_fk.{0}", "foot_fk.{0}", "toe.{0}"]'.format(side),
                ik_bones = '["thigh_ik.{0}", "MCH-shin_ik.{0}", "MCH-thigh_ik_target.{0}"]'.format(side),
                ctrl_bones = '["thigh_ik.{0}", "foot_ik.{0}", "thigh_ik_target.{0}"]'.format(side),
                extra_ctrls = '["foot_heel_ik.{0}", "foot_spin_ik.{0}"]'.format(side))

        for k, v in ik_fk.items():
            rig.pose.bones[k]["IK_FK"] = v

class CHARMORPH_PT_Pose(bpy.types.Panel):
    bl_label = "Pose"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 10

    @classmethod
    def poll(cls, context):
        if not (context.mode in ["OBJECT", "POSE"] and context.active_object and
            context.active_object.type == "ARMATURE" and
            context.active_object.data.get("rig_id")):
            return False
        char = library.obj_char(context.active_object)
        return len(char.poses) > 0

    def draw(self, context):
        self.layout.prop(context.window_manager.charmorph_ui, "pose_ik2fk")
        self.layout.prop(context.window_manager.charmorph_ui, "pose")

classes = [CHARMORPH_PT_Pose]
