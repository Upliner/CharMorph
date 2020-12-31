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

import os, json, logging, numpy
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

def char_file(char, file):
    if not char:
        return ""
    return os.path.join(data_dir, "characters", char, file)

def parse_file(path, parse_func, default={}):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r") as f:
            return parse_func(f)
    except Exception as e:
        logger.error(e)
        return default

class Character:
    def __init__(self, name):
        self.name = name
        self.config = {}
        self.morphs_meta = {}
        self.assets = {}
        self.poses = {}
        self.conf_default = {
            "title": name,
            "char_file": "char.blend",
            "char_obj": "char",
            "default_type": "",
            "default_hair_length": 0.1,
            "hairstyles": [],
            "armature": [],
            "materials": [],
            "types": {},
        }

    def __getattr__(self, item):
        return self.config.get(item, self.conf_default.get(item))

    def path(self, file):
        return char_file(self.name, file)

    def get_yaml(self, file, default={}):
        if self.name == "":
            return default
        return parse_file(self.path(file), yaml.safe_load, default)

    def blend_file(self):
        return self.path(self.char_file)

empty_char = Character("")

def obj_char(obj):
    if not obj:
        return empty_char
    return chars.get(obj.data.get("charmorph_template"), chars.get(obj.get("charmorph_template"), empty_char))

def get_fitting_assets(ui, context):
    obj = bpy.data.objects.get(ui.fitting_char)
    char = obj_char(obj)
    return [ ("char_" + k,k,'') for k in sorted(char.assets.keys()) ] + [ ("add_" + k,k,'') for k in sorted(additional_assets.keys()) ]

def get_poses(ui, context):
    return [(" ","<select pose>","")] + [ (k,k,"") for k in sorted(obj_char(context.active_object).poses.keys()) ]

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

def fitting_asset_data(context):
    ui = context.window_manager.charmorph_ui
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
    chardir = os.path.join(data_dir,"characters")
    if not os.path.isdir(chardir):
        logger.error("Directory {} is not found.".format(chardir))
        return

    for char_name in os.listdir(chardir):
        char = Character(char_name)
        char.config = char.get_yaml("config.yaml")
        if not os.path.isfile(char.blend_file()):
            logger.error("Character {} doesn't have char file {}.".format(char_name, char.blend_file()))
            continue
        char.morphs_meta = char.get_yaml("morphs_meta.yaml")
        char.assets = load_assets_dir(char.path("assets"))
        char.poses = load_json_dir(char.path("poses"))
        chars[char_name] = char

if not os.path.isdir(data_dir):
    logger.error("Charmorph data is not found at {}".format(data_dir))

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

def import_morph(basis, sk, file):
    data = numpy.load(file)
    if isinstance(data, numpy.ndarray):
        data = data.reshape(-1)
        if basis is not None:
            data += basis
    elif isinstance(data, numpy.lib.npyio.NpzFile):
        idx = data["idx"]
        delta = data["delta"]
        if basis is None:
            data = numpy.zeros((len(sk.data),3))
        else:
            data = basis.copy().reshape((-1,3))
        data[idx] += delta
        data = data.reshape(-1)
    else:
        logger.error("bad morph file: " + file)
        return
    sk.data.foreach_set("co", data)
    return data

def import_shapekeys(obj, char_name):
    if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
        obj.shape_key_add(name="Basis", from_mix=False)
    dir = char_file(char_name, "morphs/L1")
    L1_basis_dict = {}
    if os.path.isdir(dir):
        for file in sorted(os.listdir(dir)):
            if os.path.isfile(os.path.join(dir, file)):
                name, _ = os.path.splitext(file)
                L1_basis_dict[name] = import_morph(None, obj.shape_key_add(name = "L1_" + name, from_mix = False), os.path.join(dir, file))

    basis = numpy.empty(len(obj.data.vertices) * 3)
    obj.data.vertices.foreach_get("co", basis)

    dir = char_file(char_name, "morphs/L2")
    if os.path.isdir(dir):
        for file in sorted(os.listdir(dir)):
            if os.path.isfile(os.path.join(dir, file)):
                name, _ = os.path.splitext(file)
                import_morph(basis, obj.shape_key_add(name = "L2__" + name, from_mix = False), os.path.join(dir, file))
        for file in sorted(os.listdir(dir)):
            if os.path.isdir(os.path.join(dir, file)):
                L1_basis = L1_basis_dict.get(file)
                if L1_basis is None:
                    logger.error("Unknown L1 type: " + file)
                    continue
                for file2 in sorted(os.listdir(os.path.join(dir, file))):
                    name, _ = os.path.splitext(file2)
                    sk = obj.shape_key_add(name = "L2_%s_%s" % (file, name), from_mix = False)
                    sk.relative_key = obj.data.shape_keys.key_blocks["L1_" + file]
                    import_morph(L1_basis, sk, os.path.join(dir, file, file2))

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
        self.layout.prop(ui, 'import_shapekeys')
        self.layout.operator('charmorph.import_char', icon='ARMATURE_DATA')

class OpImport(bpy.types.Operator):
    bl_idname = "charmorph.import_char"
    bl_label = "Import character"
    bl_description = "Import character"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def execute(self, context):
        ui = context.window_manager.charmorph_ui
        if not ui.base_model:
            self.report({'ERROR'}, "Please select base model")
            return {"CANCELLED"}

        char = chars[ui.base_model]

        obj = import_obj(char.blend_file(), char.char_obj)
        if obj == None:
            self.report({'ERROR'}, "Import failed")
            return {"CANCELLED"}

        obj.location = context.scene.cursor.location

        obj.data["charmorph_template"] = ui.base_model
        materials.init_materials(obj, char)
        if ui.import_shapekeys:
            import_shapekeys(obj, ui.base_model)
        elif (not obj.data.shape_keys or not obj.data.shape_keys.key_blocks) and os.path.isdir(char.path("morphs")):
            obj.data["cm_morpher"] = "ext"
        morphing.create_charmorphs(obj)
        context.view_layer.objects.active = obj
        return {"FINISHED"}

classes = [OpImport, CHARMORPH_PT_Library]
