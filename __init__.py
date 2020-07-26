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

from . import morpher

rootLogger = logging.getLogger(None)
rootLogger.setLevel(10)
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(name)s - %(funcName)s - %(lineno)s - %(message)s'))
rootLogger.addHandler(ch)

logger = logging.getLogger(__name__)

bl_info = {
    "name": "CharMorph",
    "author": "Michael Vigovsky",
    "version": (0, 0, 1),
    "blender": (2, 83, 0),
    "location": "View3D > Tools > CharMorph",
    "description": "Character creation and morphing (MB-Lab based)",
    "warning": "",
    'wiki_url': "",
    'tracker_url': 'https://github.com/Upliner/CharMorph/issues',
    "category": "Characters"
}

has_dir = False
data_dir=""
last_object = None
owner = object()

class CharMorphPanel(bpy.types.Panel):
    bl_label = "CharMorph {0}.{1}.{2}".format(bl_info["version"][0], bl_info["version"][1], bl_info["version"][2])
    bl_idname = "OBJECT_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"

    @classmethod
    def poll(self, context):
        if last_object == None and context.active_object != None and not hasattr(context.scene,'charmorphs'):
            bpy.msgbus.publish_rna(key=(bpy.types.LayerObjects, "active"))
        return True

    def draw(self, context):
        scn = bpy.context.scene

        self.layout.label(text= "CREATION", icon='RNA_ADD')
        box_new_opt = self.layout.column(align=True)
        if data_dir != "" and has_dir:
            box_new_opt.prop(scn, 'charmorph_base_model')
            box_new_opt.operator('charmorph.create', icon='ARMATURE_DATA')
        else:
            self.layout.label(text= "Data dir is not found at {}. Creation is not available.".format(data_dir))
        
        box_new_opt.separator(factor=0.5)
   
        if hasattr(scn,'charmorphs'):
            self.layout.label(text= "MORPHING", icon='MODIFIER_ON')
            box_new_opt = self.layout.column(align=True)
            for prop in (p for p in dir(scn.charmorphs) if p.startswith("prop_")):
                box_new_opt.prop(scn.charmorphs, prop)

def import_obj(file,obj):
    with bpy.data.libraries.load(os.path.join(data_dir,file)) as (data_from, data_to):
        if obj not in data_from.objects:
            raise(obj + " object is not found")
        data_to.objects = [obj]
    bpy.context.collection.objects.link(data_to.objects[0])
    return data_to.objects[0]

class CharMorphCreate(bpy.types.Operator):
    bl_idname = "charmorph.create"
    bl_label = "Create character"

    def execute(self, context):
        global last_object
        base_model = str(bpy.context.scene.charmorph_base_model)
        if not base_model:
            raise("Please select base model")
        obj = import_obj("characters/{}/char.blend".format(base_model),"char")
        last_object = obj
        morpher.create_charmorphs(morpher.get_obj_morphs(obj))
        return {"FINISHED"}

def getBaseModels():
    return [("mb_human_female", "Human female (MB-Lab, AGPL3)","")]

data_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "data")
logger.debug("Looking for the database in the folder %s...", data_dir)

if not os.path.isdir(data_dir):
    logger.error("Charmorph data is not found at {}".format(data_dir))
else:
    has_dir=True    


def on_select_object():
    global last_object
    obj = bpy.context.active_object 
    if obj == None or obj == last_object:
        return
    last_object = obj
    morphs = morpher.get_obj_morphs(obj)
    if not morphs:
        return
    morpher.create_charmorphs(morphs)

classes = (CharMorphPanel, CharMorphCreate)
class_register, class_unregister = bpy.utils.register_classes_factory(classes)

@bpy.app.handlers.persistent
def load_handler(dummy):
    global last_object
    last_object = None
    morpher.del_charmorphs()
    on_select_object()

bpy.app.handlers.load_post.append(load_handler)


def register():
    print("Charmorph register")
    class_register()
    bpy.types.Scene.charmorph_base_model = bpy.props.EnumProperty(
        name = "Base",
        items = getBaseModels(),
        description = "Choose a base model",
        options = {"SKIP_SAVE"})

    bpy.msgbus.subscribe_rna(
        owner=owner,
        key = (bpy.types.LayerObjects, "active"),
        args=(),
        notify = on_select_object)

def unregister():
    print("Charmorph unregister")
    bpy.msgbus.clear_by_owner(owner)
    del bpy.types.Scene.charmorph_base_model
    morpher.del_charmorphs()

    class_unregister()

if __name__ == "__main__":
    register()

