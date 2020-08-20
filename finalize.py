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

from . import library, morphing, rigging, fitting

logger = logging.getLogger(__name__)

def add_rig(char_name, conf, rigtype):
    if conf.get("type") != "rigify":
        raise Exception("Rig type {} is not supported".format(conf.get("type")))
    metarig = library.import_obj(library.char_file(char_name, conf["file"]), conf["obj_name"], "ARMATURE")
    if not metarig:
        raise Exception("Rig import failed")

    # Trying to override the context leads to crash :( TODO: learn more about it
    #override = context.copy()
    #override["object"] = metarig
    #override["active_object"] = metarig
    def remove_metarig():
       old_armature = metarig.data
       bpy.data.objects.remove(metarig)
       bpy.data.armatures.remove(old_armature)

    char_obj = morphing.cur_object
    bpy.context.view_layer.objects.active = metarig
    bpy.ops.object.mode_set(mode="EDIT")
    if not rigging.joints_to_vg(char_obj, rigging.all_joints(bpy.context)):
        remove_metarig()
        raise Exception("Metarig fitting failed")

    bpy.ops.object.mode_set(mode="OBJECT")
    if rigtype != "RG":
        return

    metarig.data.rigify_generate_mode = "new"
    bpy.ops.pose.rigify_generate()
    remove_metarig()
    bpy.ops.object.mode_set(mode="EDIT")
    rigging.rigify_add_deform(bpy.context, char_obj)
    bpy.ops.object.mode_set(mode="OBJECT")

    rig = bpy.context.object

    rig.location = char_obj.location
    rig.rotation_quaternion = char_obj.rotation_quaternion
    rig.scale = char_obj.scale

    char_obj.location = (0,0,0)
    char_obj.rotation_quaternion = (1,0,0,0)
    char_obj.scale = (1,1,1)
    char_obj.parent = rig
    mod = char_obj.modifiers.new("charmorph_rigify", "ARMATURE")
    mod.use_deform_preserve_volume = True
    mod.use_vertex_groups = True
    mod.object = rig

    if bpy.context.scene.charmorph_ui.fitting_armature:
        fitting.transfer_new_armature(char_obj)

class OpFinalize(bpy.types.Operator):
    bl_idname = "charmorph.finalize"
    bl_label = "Finalize"
    bl_description = "Finalize character (add rig, modifiers, cleanup)"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode=="OBJECT" and morphing.cur_object is not None

    def execute(self, context):
        ui = context.scene.charmorph_ui
        char_obj = morphing.cur_object
        char_conf = library.obj_char(char_obj)
        if not char_conf.config:
            self.report({'ERROR'}, "Character config is not found")
            return {"CANCELLED"}

        if ui.fin_rig != "NO":
            rigs = char_conf.config["armature"]
            if not rigs or len(rigs) == 0:
                self.report({"ERROR"}, "Rig is not found")
                return {"CANCELLED"}
            if len(rigs) > 1:
                self.report({"ERROR"}, "Multiple rigs aren't supported yet")
                return {"CANCELLED"}
            rig_type = ui.fin_rig
            if rig_type == "RG" and not hasattr(bpy.ops.pose, "rigify_generate"):
                self.report({"ERROR"}, "Rigify is not found! Generating metarig only")
                rig_type = "MR"
            if isinstance(char_obj.parent, bpy.types.Object) and char_obj.parent.type == "ARMATURE":
                self.report({"WARNING"}, "Character is already attached to armature, skipping rig")
                return {"CANCELLED"}
            try:
                add_rig(char_conf.name, rigs[0], rig_type)
            except Exception as e:
                self.report({"ERROR"}, repr(e))
                return {"CANCELLED"}
        return {"FINISHED"}


class CHARMORPH_PT_Finalize(bpy.types.Panel):
    bl_label = "Finalization"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 8

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and morphing.cur_object != None

    def draw(self, context):
        ui = context.scene.charmorph_ui
        self.layout.prop(ui, "fin_morph")
        self.layout.prop(ui, "fin_subdivision")
        self.layout.prop(ui, "fin_csmooth")
        self.layout.prop(ui, "fin_vg_cleanup")
        self.layout.prop(ui, "fin_rig")
        self.layout.operator("charmorph.finalize")

classes = [OpFinalize, CHARMORPH_PT_Finalize]
