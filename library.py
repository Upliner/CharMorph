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

import os, json, logging, traceback, numpy
import bpy # pylint: disable=import-error

from . import utils

logger = logging.getLogger(__name__)

try:
    from yaml import load as yload, CSafeLoader as SafeLoader
except ImportError:
    from .yaml import load as yload, SafeLoader
    logger.debug("Using bundled yaml library!")

data_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "data")
logger.debug("Looking for the char library in the folder %s", data_dir)

chars = {}
additional_assets = {}
hair_colors = {}

def char_file(char, file):
    if not char or not file:
        return ""
    if file == ".":
        os.path.join(data_dir, "characters", char)
    return os.path.join(data_dir, "characters", char, file)

def parse_file(path, parse_func, default):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r") as f:
            return parse_func(f)
    except Exception as e:
        logger.error(e)
        return default

def load_yaml(data):
    return yload(data, Loader=SafeLoader)

def load_assets_dir(path):
    result = {}
    if not os.path.isdir(path):
        if path:
            logger.debug("path is not found: %s", path)
        return result
    for file in os.listdir(path):
        name, ext = os.path.splitext(file)
        if ext == ".blend" and os.path.isfile(os.path.join(path, file)):
            result[name] = (os.path.join(path, file), name)
    return result

def load_json_dir(path):
    result = {}
    if not os.path.isdir(path):
        if path:
            logger.debug("path is not found: %s", path)
        return result
    for file in os.listdir(path):
        name, ext = os.path.splitext(file)
        full_path = os.path.join(path, file)
        if ext == ".json" and os.path.isfile(full_path):
            result[name] = parse_file(full_path, json.load, {})
    return result

_empty_dict = object()

class Character:
    def __init__(self, name):
        self.name = name
        self.armature = {}
        self.config = {
            "title": name,
            "char_file": "char.blend",
            "char_obj": "char",
            "randomize_incl_regex": None,
            "randomize_excl_regex": None,
            "default_type": "",
            "default_armature": "",
            "default_hair_length": 0.1,
            "armature": {},
            "armature_defaults": {},
            "hairstyles": [],
            "materials": [],
            "underwear": [],
            "types": {},
            "assets": {},
            "poses": {},
        }
        self.config.update(self.get_yaml("config.yaml"))
        if self.name:
            self.assets = load_assets_dir(self.path("assets"))
            self.poses = load_json_dir(self.path("poses"))

        self._parse_armature()

    def __getattr__(self, item):
        if item == "morphs_meta":
            self.morphs_meta = self.get_yaml("morphs_meta.yaml")
            return self.morphs_meta
        if item == "material_lib":
            self.material_lib = self.config.get("material_lib", self.char_file)
            return self.material_lib
        return self.config[item]

    def path(self, file):
        return char_file(self.name, file)

    def get_yaml(self, file, default=_empty_dict):
        if default is _empty_dict:
            default = {}
        if self.name == "":
            return default
        return parse_file(self.path(file), load_yaml, default)

    def blend_file(self):
        return self.path(self.char_file)

    def _parse_armature(self):
        data = self.config["armature"]
        if isinstance(data, list):
            self._parse_armature_list(data)
        else:
            self._parse_armature_dict(data)

    def _parse_armature_list(self, data):
        for i, a in enumerate(data):
            title = a.get("title")
            if title:
                k = title.lower().replace(" ", "_")
            else:
                k = str(i)
                a["title"] = "<unnamed %s>" % k
            if not self.default_armature:
                self.config["default_armature"] = k
            self.armature[k] = Armature(self, k, a)

    def _parse_armature_dict(self, data):
        for k, v in data.items():
            self.armature[k] = Armature(self, k, v)

class Armature():
    def __init__(self, char: Character, name : str, conf : dict):
        self.char = char
        self.config = {
            "file": char.char_file,
            "title": name,
            "type": "",
            "tweaks": [],
            "ik_limits": {},
            "mixin": "",
            "mixin_bones": {},
            "arp_reference_layer": 17,
        }
        self.config.update(char.armature_defaults)
        self.config.update(conf)

        for item in ("weights", "joints"):
            value = self.config.get(item)
            if value:
                value = char.path(value)
            setattr(self, item, value)

        if "bones" not in self.config: # Legacy
            self.config["bones"] = char.config.get("bones", {})

    def __getattr__(self, item):
        value = self.config[item]
        if item in ("bones", "mixin_bones"):
            if isinstance(value, str):
                value = self.char.get_yaml(value)
            setattr(self, item, value)
        return value

empty_char = Character("")

def obj_char(obj):
    if not obj:
        return empty_char
    tpl = obj.data.get("charmorph_template")
    if tpl:
        return chars.get(tpl)

    # MB-Lab characters support
    tpl = obj.get("manuellab_id")
    if not tpl:
        return empty_char
    if tpl in ("f_af01", "f_an03", "f_as01", "f_ca01", "f_ft01", "f_la01"):
        return chars.get("mb_human_female")
    if tpl in ("m_af01", "m_an03", "m_as01", "m_ca01", "m_ft01", "m_ft02", "m_la01"):
        return chars.get("mb_human_male")
    return empty_char

def update_fitting_assets(ui, _):
    global additional_assets
    path = ui.fitting_library_dir
    if not path:
        return
    additional_assets = load_assets_dir(path)

def fitting_asset_data(context):
    ui = context.window_manager.charmorph_ui
    item = ui.fitting_library_asset
    if item.startswith("char_"):
        obj = bpy.data.objects.get(ui.fitting_char)
        char = obj_char(obj)
        return char.assets.get(item[5:])
    if item.startswith("add_"):
        return additional_assets.get(item[4:])
    return None

def load_library():
    t = utils.Timer()
    global hair_colors
    chars.clear()
    hair_colors = parse_file(os.path.join(data_dir, "hair_colors.yaml"), load_yaml, {})
    chardir = os.path.join(data_dir, "characters")
    if not os.path.isdir(chardir):
        logger.error("Directory %s is not found.", format(chardir))
        return

    for char_name in os.listdir(chardir):
        try:
            char = Character(char_name)
        except Exception as e:
            logger.error("Error in character %s: %s", char_name, e)
            logger.error(traceback.format_exc())
            continue

        if not os.path.isfile(char.blend_file()):
            logger.error("Character %s doesn't have char file %s.", char_name, char.blend_file())
            continue

        chars[char_name] = char

    t.time("Library load")

if not os.path.isdir(data_dir):
    logger.error("Charmorph data is not found at %s", data_dir)

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
            data = numpy.zeros((len(sk.data), 3))
        else:
            data = basis.copy().reshape(-1, 3)
        data[idx] += delta
        data = data.reshape(-1)
    else:
        logger.error("bad morph file: %s", file)
        return None
    sk.data.foreach_set("co", data)
    return data

def import_shapekeys(obj, char_name):
    if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
        obj.shape_key_add(name="Basis", from_mix=False)
    path = char_file(char_name, "morphs/L1")
    L1_basis_dict = {}
    if os.path.isdir(path):
        for file in sorted(os.listdir(path)):
            if os.path.isfile(os.path.join(path, file)):
                name, _ = os.path.splitext(file)
                morph = import_morph(None, obj.shape_key_add(name="L1_" + name, from_mix=False), os.path.join(path, file))
                if morph is not None:
                    L1_basis_dict[name] = morph

    basis = numpy.empty(len(obj.data.vertices) * 3)
    obj.data.vertices.foreach_get("co", basis)

    path = char_file(char_name, "morphs/L2")
    if os.path.isdir(path):
        for file in sorted(os.listdir(path)):
            if os.path.isfile(os.path.join(path, file)):
                name, _ = os.path.splitext(file)
                import_morph(basis, obj.shape_key_add(name="L2__" + name, from_mix=False), os.path.join(path, file))
        for file in sorted(os.listdir(path)):
            if os.path.isdir(os.path.join(path, file)):
                L1_basis = L1_basis_dict.get(file)
                if L1_basis is None:
                    logger.error("Unknown L1 type: %s", file)
                    continue
                for file2 in sorted(os.listdir(os.path.join(path, file))):
                    name, _ = os.path.splitext(file2)
                    sk = obj.shape_key_add(name="L2_%s_%s" % (file, name), from_mix=False)
                    sk.relative_key = obj.data.shape_keys.key_blocks["L1_" + file]
                    import_morph(L1_basis, sk, os.path.join(path, file, file2))

from  . import morphing, materials, fitting

def get_obj_char(context):
    m = morphing.morpher
    if m:
        return m.obj, m.char
    obj = context.object
    if obj:
        if obj.type == "ARMATURE":
            children = obj.children
            if len(children) == 1:
                obj = children[0]
        if obj.type == "MESH":
            char = obj_char(obj)
            if char.name:
                return obj, char
    return (None, None)

def import_obj(file, obj, typ="MESH", link=True):
    fitting.invalidate_cache()
    with bpy.data.libraries.load(file) as (data_from, data_to):
        if obj not in data_from.objects:
            if len(data_from.objects) == 1:
                obj = data_from.objects[0]
            else:
                logger.error("object %s is not found in %s", obj, file)
                return None
        data_to.objects = [obj]
    obj = data_to.objects[0]
    if obj.type != typ:
        bpy.data.objects.remove(obj)
        return None
    if link:
        bpy.context.collection.objects.link(obj)
    return obj

class OpReloadLib(bpy.types.Operator):
    bl_idname = "charmorph.reload_library"
    bl_label = "Reload library"
    bl_description = "Reload character library"

    def execute(self, _context): # pylint: disable=no-self-use
        load_library()
        return {"FINISHED"}

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
        if obj is None:
            self.report({'ERROR'}, "Import failed")
            return {"CANCELLED"}

        obj.location = context.scene.cursor.location
        if ui.import_cursor_z:
            obj.rotation_mode = "XYZ"
            obj.rotation_euler = (0, 0, context.scene.cursor.rotation_euler[2])

        obj.data["charmorph_template"] = ui.base_model
        materials.init_materials(obj, char)

        if ui.import_shapekeys:
            import_shapekeys(obj, ui.base_model)
        elif os.path.isdir(char.path("morphs")):
            obj.data["cm_morpher"] = "ext"

        morphing.create_charmorphs(obj)
        context.view_layer.objects.active = obj
        ui.fitting_char = obj.name

        if char.randomize_incl_regex is not None:
            ui.randomize_incl = char.randomize_incl_regex
        if char.randomize_excl_regex is not None:
            ui.randomize_excl = char.randomize_excl_regex

        if char.default_armature and ui.fin_rig == '-':
            ui.fin_rig = char.default_armature

        if not is_adult_mode():
            for name in char.underwear:
                fitting.fit_import(context, *char.assets[name])

        return {"FINISHED"}

class UIProps:
    base_model: bpy.props.EnumProperty(
        name="Base",
        items=lambda ui, context: [(name, conf.title, "") for name, conf in chars.items()],
        description="Choose a base model")
    material_mode: bpy.props.EnumProperty(
        name="Materials",
        default="TS",
        description="Share materials between different Charmorph characters or not",
        items=[
            ("NS", "Non-Shared", "Use unique material for each character"),
            ("TS", "Shared textures only", "Use same texture for all characters"),
            ("MS", "Shared", "Use same materials for all characters")]
    )
    import_cursor_z: bpy.props.BoolProperty(
        name="Use Z cursor rotation", default=True,
        description="Take 3D cursor Z rotation into account when creating the character")
    material_local: bpy.props.BoolProperty(
        name="Use local materials", default=True,
        description="Use local copies of materials for faster loading")
    import_shapekeys: bpy.props.BoolProperty(
        name="Import shape keys", default=False,
        description="Import and morph character using shape keys")

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
        l = self.layout
        l.operator('charmorph.reload_library')
        l.separator()
        if data_dir == "":
            l.label(text="Data dir is not found. Importing is not available.")
            return
        if not chars:
            l.label(text="No characters found at {}. Nothing to import.".format(data_dir))
            return
        for prop in UIProps.__annotations__: # pylint: disable=no-member
            l.prop(context.window_manager.charmorph_ui, prop)
        l.operator('charmorph.import_char', icon='ARMATURE_DATA')

        l.alignment = "CENTER"
        c = l.column(align=True)
        if is_adult_mode():
            labels = ["Adult mode is on", "The character will be naked"]
        else:
            labels = ["Adult mode is off", "Default underwear will be added"]
        for text in labels:
            r = c.row()
            r.alignment = "CENTER"
            r.label(text=text)


classes = [OpReloadLib, OpImport, CHARMORPH_PT_Library]
