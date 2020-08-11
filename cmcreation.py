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
import bpy, mathutils

logger = logging.getLogger(__name__)

class VIEW3D_PT_CMCreation(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_CMCreation"
    bl_label = "Character creation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 2

    def draw(self, context):
        pass

class CMCREATION_PT_Rigging(bpy.types.Panel):
    bl_label = "Rigging"
    bl_parent_id = "VIEW3D_PT_CMCreation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"

    def draw(self, context):
        ui = context.scene.cmcreation_ui
        self.layout.prop(ui, "rig_char")
        self.layout.prop(ui, "rig_armature")
        self.layout.operator("cmcreation.joints_to_vg")

def obj_by_type(name, type):
    if not name:
        return None
    obj = bpy.data.objects.get(name)
    if obj and obj.type == type:
        return obj
def get_char():
    return obj_by_type(bpy.context.scene.cmcreation_ui.rig_char, "MESH")
def get_rig():
    return obj_by_type(bpy.context.scene.cmcreation_ui.rig_armature, "ARMATURE")

def joints_to_vg(char, rig):
    weights = {}
    for v in char.data.vertices:
        for item in v.groups:
            vg = char.vertex_groups[item.group]
            if not vg.name.startswith("joint_"):
                continue
            name = vg.name[6:]
            witem = weights.get(name)
            if not witem:
                witem = [0,mathutils.Vector()]
                weights[name] = witem
            witem[0] += item.weight
            witem[1] += v.co*item.weight

    for k, v in weights.items():
        bone_name, jtype = k.rsplit("_", 1)
        bone = rig.data.edit_bones.get(bone_name)
        if not bone:
            logger.warn("Bone not found: " + bone_name)
            continue
        pos = v[1]/v[0]
        if jtype == "head":
            bone.head = pos
        elif jtype == "tail":
            bone.tail = pos

class OpJointsToVG(bpy.types.Operator):
    bl_idname = "cmcreation.joints_to_vg"
    bl_label = "All Joints to VG"
    bl_description = "Move selected joints according to their vertex groups"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return get_char() and get_rig()

    def execute(self, context):
        joints_to_vg(get_char(), get_rig())
        return {"FINISHED"}

def objects_by_type(type):
    return [(o.name,o.name,"") for o in bpy.data.objects if o.type == type]

class CMCreationUIProps(bpy.types.PropertyGroup):
    # Rigging
    rig_char: bpy.props.EnumProperty(
        name = "Char",
        items = lambda ui, context: objects_by_type("MESH"),
        description = "Character mesh for rigging")
    rig_armature: bpy.props.EnumProperty(
        name = "Rig",
        items = lambda ui, context: objects_by_type("ARMATURE"),
        description = "Armature for rigging")

    def draw(self, context):
        ui = context.scene.cmcreation_ui
        self.layout.prop(ui,"rig_char")
        self.layout.prop(ui,"rig_armature")

classes = [CMCreationUIProps, OpJointsToVG, VIEW3D_PT_CMCreation, CMCREATION_PT_Rigging]

register_classes, unregister_classes = bpy.utils.register_classes_factory(classes)

def register():
    register_classes()
    bpy.types.Scene.cmcreation_ui = bpy.props.PointerProperty(type=CMCreationUIProps, options={"SKIP_SAVE"})

def unregister():
    del bpy.types.Scene.cmcreation_ui
    unregister_classes()

if __name__ == "__main__":
    register()
