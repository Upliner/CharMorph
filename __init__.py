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

import os, logging, re, random
import bpy

from . import utils, morpher

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
        ui = scn.charmorph_ui

        self.layout.label(text= "CREATION", icon='RNA_ADD')
        box_new_opt = self.layout.column(align=True)
        if utils.data_dir != "" and utils.has_dir:
            box_new_opt.prop(ui, 'base_model')
            box_new_opt.prop(ui, 'material_mode')
            box_new_opt.operator('charmorph.create', icon='ARMATURE_DATA')
        else:
            self.layout.label(text= "Data dir is not found at {}. Creation is not available.".format(utils.data_dir))

        box_new_opt.separator(factor=0.5)

        if hasattr(scn,'charmorphs'):
            self.layout.label(text= "MORPHING", icon='MODIFIER_ON')
            propList = sorted(dir(scn.charmorphs))
            self.layout.label(text= "Character type")
            box_new_opt = self.layout.column(align=True)

            box_new_opt.prop(scn,"chartype")
            if hasattr(scn.charmorphs,"preset"):
                box_new_opt.prop(scn.charmorphs,"preset")
                box_new_opt.prop(scn.charmorphs,"preset_mix")

            box_new_opt.separator(factor=0.5)

            meta_morphs = [p for p in propList if p.startswith("meta_")]
            if len(meta_morphs) > 0:
                self.layout.label(text= "Meta morphs")
                box_new_opt = self.layout.column(align=True)
                box_new_opt.prop(scn.charmorphs, "relative_meta")

                for prop in meta_morphs:
                    box_new_opt.prop(scn.charmorphs, prop)

            box_new_opt.prop(scn.charmorphs,"clamp_combos")
            box_new_opt.separator(factor=0.5)

            self.layout.label(text= "Randomize")
            box_new_opt = self.layout.column(align=True)
            box_new_opt.prop(ui, "randomize_rel")
            box_new_opt.prop(ui, "randomize_incl")
            box_new_opt.prop(ui, "randomize_excl")
            box_new_opt.prop(ui, "randomize_strength")
            box_new_opt.operator('charmorph.randomize')
            box_new_opt.separator(factor=0.5)

            self.layout.prop(scn.charmorphs, "category")
            if scn.charmorphs.category != "<None>":
                box_new_opt = self.layout.column(align=True)
                for prop in (p for p in propList if p.startswith("prop_" + ("" if scn.charmorphs.category == "<All>" else scn.charmorphs.category + "_"))):
                    box_new_opt.prop(scn.charmorphs, prop)

def import_obj(file, obj):
    with bpy.data.libraries.load(os.path.join(utils.data_dir, file)) as (data_from, data_to):
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
        base_model = str(context.scene.charmorph_ui.base_model)
        if not base_model:
            raise("Please select base model")
        obj = import_obj("characters/{}/char.blend".format(base_model),"char")
        if obj == None:
            raise("Object is not found")
        obj["charmorph_template"] = base_model
        last_object = obj
        morpher.create_charmorphs(obj)
        return {"FINISHED"}

class CharMorphRandomize(bpy.types.Operator):
    bl_idname = "charmorph.randomize"
    bl_label = "Randomize"

    def execute(self, context):
        scn = context.scene
        if not hasattr(scn,'charmorphs'):
            return {"CANCELLED"}
        ui = scn.charmorph_ui
        cm = scn.charmorphs
        incl = re.compile(ui.randomize_incl)
        excl = re.compile(ui.randomize_excl)
        for prop in dir(cm):
            if not prop.startswith("prop_"):
                continue
            propname = prop[5:]
            if excl.match(propname) or not incl.match(propname):
                continue
            val = (ui.randomize_strength * (random.random() * 2 - 1))
            if ui.randomize_rel:
                val += getattr(cm, prop)
            setattr(cm, prop, val)
        return {"FINISHED"}

def getBaseModels():
    return [("mb_human_female", "Human female (MB-Lab, AGPL3)","")]

class CharMorphUIProps(bpy.types.PropertyGroup):
    base_model: bpy.props.EnumProperty(
        name = "Base",
        items = getBaseModels(),
        description = "Choose a base model")
    material_mode: bpy.props.EnumProperty(
        name = "Materials",
        items = [
            ("NS", "Non-Shared","Use unique material for each character"),
            ("TS", "Shared textures only","Use same texture for all characters"),
            ("MS", "Shared","Use same materials for all characters")],
        description = "Choose a base model")
    randomize_incl: bpy.props.StringProperty(
        name = "Incl. regex")
    randomize_excl: bpy.props.StringProperty(
        name = "Excl. regex", default="^Fantasy\_")
    randomize_rel: bpy.props.BoolProperty(
        name = "Relative")
    randomize_strength: bpy.props.FloatProperty(
        name = "Strength", min=0, max=1, default=0.2, precision=2, description = "Randomization strength", subtype = "FACTOR")


def on_select_object():
    global last_object
    obj = bpy.context.active_object
    if obj == None or obj == last_object:
        return
    last_object = obj
    morpher.create_charmorphs(obj)

classes = (CharMorphPanel, CharMorphUIProps, CharMorphCreate, CharMorphRandomize)
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
    bpy.types.Scene.charmorph_ui = bpy.props.PointerProperty(type=CharMorphUIProps, options={"SKIP_SAVE"})

    bpy.msgbus.subscribe_rna(
        owner=owner,
        key = (bpy.types.LayerObjects, "active"),
        args=(),
        notify = on_select_object)

def unregister():
    print("Charmorph unregister")
    bpy.msgbus.clear_by_owner(owner)
    del bpy.types.Scene.charmorph_ui
    morpher.del_charmorphs()

    class_unregister()

if __name__ == "__main__":
    register()
