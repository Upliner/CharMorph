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

import os, logging
import bpy

from . import morphing

logger = logging.getLogger(__name__)

data_dir=""
has_dir = False

data_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "data")
logger.debug("Looking for the database in the folder %s...", data_dir)

if not os.path.isdir(data_dir):
    logger.error("Charmorph data is not found at {}".format(data_dir))
else:
    has_dir=True

class CHARMORPH_PT_Creation(bpy.types.Panel):
    bl_label = "Creation"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'

    def draw(self, context):
        if data_dir == "" or not has_dir:
            self.layout.label(text= "Data dir is not found at {}. Creation is not available.".format(data_dir))
            return
        ui = context.scene.charmorph_ui
        self.layout.prop(ui, 'base_model')
        self.layout.prop(ui, 'material_mode')
        self.layout.operator('charmorph.create', icon='ARMATURE_DATA')

def import_obj(file, obj):
    with bpy.data.libraries.load(os.path.join(data_dir, file)) as (data_from, data_to):
        if obj not in data_from.objects:
            raise(obj + " object is not found")
        data_to.objects = [obj]
    bpy.context.collection.objects.link(data_to.objects[0])
    return data_to.objects[0]

class CharMorphCreate(bpy.types.Operator):
    bl_idname = "charmorph.create"
    bl_label = "Create character"
    bl_order = 1

    def execute(self, context):
        global last_object
        base_model = str(context.scene.charmorph_ui.base_model)
        if not base_model:
            raise("Please select base model")
        obj = import_obj("characters/{}/char.blend".format(base_model),"char")
        if obj == None:
            raise("Object is not found")
        obj["charmorph_template"] = base_model
        last_object = obj
        morphing.create_charmorphs(obj)
        return {"FINISHED"}

classes = [CHARMORPH_PT_Creation, CharMorphCreate]
