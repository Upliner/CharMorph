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

import os, json, logging
import bpy

from . import yaml, morphing, materials, fitting

logger = logging.getLogger(__name__)

data_dir=""
adult_mode=False

data_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "data")
logger.debug("Looking for the char library in the folder %s...", data_dir)

chars = {}
additional_assets = {}
hair_colors = {}

class Character:
    def __init__(self, name):
        self.name = name
        self.config = {}
        self.morphs_meta = {}
        self.assets = {}
        self.poses = {}

empty_char = Character("")

def char_file(char, file):
    return os.path.join(data_dir, "characters", char, file)

def parse_file(path, parse_func, default={}):
    try:
        with open(path, "r") as f:
            return parse_func(f)
    except Exception as e:
        logger.error(e)
        return default

def get_char_yaml(char, file, default={}):
    if char == "":
        return default
    return parse_file(char_file(char, file), yaml.safe_load, default)

def obj_char(obj):
    if not obj:
        return empty_char
    return chars.get(obj.data.get("charmorph_template"), chars.get(obj.get("charmorph_template"), empty_char))

def get_fitting_assets(ui, context):
    obj = bpy.data.objects.get(ui.fitting_char)
    char = obj_char(obj)
    return [ ("char_" + k,k,'') for k in sorted(char.assets.keys()) ] + [ ("add_" + k,k,'') for k in sorted(additional_assets.keys()) ]

def get_poses(ui, context):
    return [(" ","<select pose>","")] + [ (k,k,"") for k in obj_char(context.active_object).poses.keys() ]

def get_hair_colors(ui, context):
    return [ (k,k,"") for k in hair_colors.keys() ]

def load_assets_dir(dir):
    result = {}
    if not os.path.isdir(dir):
        return result
    for file in os.listdir(dir):
        name, ext = os.path.splitext(file)
        if ext == ".blend" and os.path.isfile(os.path.join(dir, file)):
            result[name] = (os.path.join(dir, file), name)
    return result

def update_fitting_assets(ui, context):
    global additional_assets
    dir = ui.fitting_library_dir
    if not dir:
        return
    additional_assets = load_assets_dir(dir)

def fitting_asset_data():
    ui = bpy.context.window_manager.charmorph_ui
    item = ui.fitting_library_asset
    if item.startswith("char_"):
        obj = bpy.data.objects.get(ui.fitting_char)
        char = obj_char(obj)
        return char.assets.get(item[5:])
    elif item.startswith("add_"):
        return additional_assets.get(item[4:])
    return None

def load_json_dir(dir):
    result = {}
    if not os.path.isdir(dir):
        return result
    for file in os.listdir(dir):
        name, ext = os.path.splitext(file)
        full_path = os.path.join(dir, file)
        if ext == ".json" and os.path.isfile(full_path):
            result[name] = parse_file(full_path, json.load, {})
    return result

def load_library():
    global hair_colors
    chars.clear()
    hair_colors = parse_file(os.path.join(data_dir,"hair_colors.yaml"), yaml.safe_load)
    for char_name in os.listdir(os.path.join(data_dir,"characters")):
        if not os.path.isfile(char_file(char_name, "char.blend")):
            logger.error("Character {} doesn't have a char.blend!".format(char_name))
            continue
        char = Character(char_name)
        char.config = get_char_yaml(char_name, "config.yaml")
        char.morphs_meta = get_char_yaml(char_name, "morphs_meta.yaml")
        char.assets = load_assets_dir(char_file(char_name, "assets"))
        char.poses = load_json_dir(char_file(char_name, "poses"))
        chars[char_name] = char

if not os.path.isdir(data_dir):
    logger.error("Charmorph data is not found at {}".format(data_dir))

class CHARMORPH_PT_Library(bpy.types.Panel):
    bl_label = "Character library"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def draw(self, context):
        if data_dir == "":
            self.layout.label(text = "Data dir is not found. Creation is not available.")
            return
        if not chars:
            self.layout.label(text = "No characters found at {}. Nothing to create.".format(data_dir))
            return
        ui = context.window_manager.charmorph_ui
        self.layout.prop(ui, 'base_model')
        self.layout.prop(ui, 'material_mode')
        self.layout.prop(ui, 'material_local')
        self.layout.operator('charmorph.import_char', icon='ARMATURE_DATA')

def import_obj(file, obj, typ = "MESH", link = True):
    fitting.invalidate_cache()
    with bpy.data.libraries.load(file) as (data_from, data_to):
        if obj not in data_from.objects:
            if len(data_from.objects) == 1:
                obj = data_from.objects[0]
            else:
                return None
        data_to.objects = [obj]
    obj = data_to.objects[0]
    if obj.type != typ:
        bpy.data.objects.remove(obj)
        return None
    if link:
        bpy.context.collection.objects.link(obj)
    return obj

def is_adult_mode():
    prefs = bpy.context.preferences.addons.get(__package__, None)
    if not prefs:
        return False
    return prefs.preferences.adult_mode

class OpImport(bpy.types.Operator):
    bl_idname = "charmorph.import_char"
    bl_label = "Import character"
    bl_description = "Import character"
    bl_options = {"UNDO"}

    def execute(self, context):
        ui = context.window_manager.charmorph_ui
        base_model = str(ui.base_model)
        if not base_model:
            self.report({'ERROR'}, "Please select base model")
            return {"CANCELLED"}

        obj = import_obj(char_file(base_model, "char.blend"), "char")
        if obj == None:
            self.report({'ERROR'}, "Import failed")
            return {"CANCELLED"}

        obj.location = context.scene.cursor.location

        obj.data["charmorph_template"] = base_model
        materials.init_materials(obj, chars.get(base_model, empty_char))
        morphing.create_charmorphs(obj)
        context.view_layer.objects.active = obj
        return {"FINISHED"}

classes = [OpImport, CHARMORPH_PT_Library]
