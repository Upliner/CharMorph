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
# Copyright (C) 2020-2022 Michael Vigovsky

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
char_aliases = {}
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

def load_data_dir(path, target_ext):
    result = {}
    if not os.path.isdir(path):
        if path:
            logger.debug("path is not found: %s", path)
        return result
    for file in os.listdir(path):
        name, ext = os.path.splitext(file)
        if ext == target_ext and os.path.isfile(os.path.join(path, file)):
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
    description = ""
    author = ""
    licence = ""
    char_file = "char.blend"
    char_obj = "char"
    basis = ""
    no_morph_categories = False
    custom_morph_order = False
    randomize_incl_regex = None
    randomize_excl_regex = None
    default_type = ""
    default_armature = ""
    default_hair_length = 0.1
    recurse_materials = False
    armature = {}
    armature_defaults = {}
    bones = {}
    hairstyles = []
    materials = []
    material_lib = None
    default_assets = []
    underwear = []
    types = {}

    def __init__(self, name):
        self.title = name
        self.name = name
        self.__dict__.update(self.get_yaml("config.yaml"))
        self.name = name
        if self.name:
            self.assets = load_data_dir(self.path("assets"), ".blend")
            self.poses = load_json_dir(self.path("poses"))
            self.alt_topos = load_data_dir(self.path("morphs/alt_topo"), ".npy")

        if self.material_lib is None:
            self.material_lib = self.char_file

        self.armature = self._parse_armature(self.armature)

    @utils.lazyprop
    def morphs_meta(self):
        return self.get_yaml("morphs_meta.yaml")

    @utils.lazyprop
    def fitting_subset(self):
        return self.get_np("fitting_subset.npz")

    @utils.lazyprop
    def faces(self):
        result = self.get_np("faces.npy")
        # This array is used for building BVHTree and Blender crashes if array type is other than "object"
        return None if result is None else result.astype(object)

    def path(self, file):
        return char_file(self.name, file)

    def get_np(self, file):
        file = self.path(file)
        return numpy.load(file) if os.path.isfile(file) else None

    def get_yaml(self, file, default=_empty_dict):
        if default is _empty_dict:
            default = {}
        if self.name == "":
            return default
        return parse_file(self.path(file), load_yaml, default)

    def blend_file(self):
        return self.path(self.char_file)

    def get_basis(self):
        return self.get_np("morphs/L1/%s.npy" % self.basis)

    def _parse_armature(self, data):
        if isinstance(data, list):
            return self._parse_armature_list(data)
        else:
            return self._parse_armature_dict(data)

    def _parse_armature_list(self, data):
        result = {}
        for i, a in enumerate(data):
            title = a.get("title")
            if title:
                k = title.lower().replace(" ", "_")
            else:
                k = str(i)
                a["title"] = "<unnamed %s>" % k
            if not self.default_armature:
                self.default_armature = k
            result[k] = Armature(self, k, a)
        return result

    def _parse_armature_dict(self, data):
        result = {}
        for k, v in data.items():
            result[k] = Armature(self, k, v)
        return result

def _wrap_lazy_yaml(name, value):
    if isinstance(value, str):
        return utils.named_lazyprop(name, lambda self: self.char.get_yaml(value))
    return value

class Armature:
    type = "regular"
    tweaks = []
    ik_limits = {}
    bones = None
    mixin = ""
    mixin_bones = {}
    arp_reference_layer = 17

    def __init__(self, char: Character, name : str, conf : dict):
        self.title = name
        self.obj_name = name
        self.file = char.char_file

        self.__dict__.update(char.armature_defaults)
        self.__dict__.update(conf)

        self.char = char

        for item in ("weights", "joints"):
            value = getattr(self, item, None)
            setattr(self, item, char.path(value) if value else char.path(os.path.join(item, name + ".npz")))

        if self.bones is None:
            self.bones = char.bones # Legacy

        self.bones       = _wrap_lazy_yaml("bones", self.bones)
        self.mixin_bones = _wrap_lazy_yaml("mixin_bones", self.mixin_bones)

empty_char = Character("")

def char_by_name(name):
    return chars.get(name) or chars.get(char_aliases.get(name)) or empty_char

def obj_char(obj):
    if not obj:
        return empty_char
    return char_by_name(obj.data.get("charmorph_template") or obj.get("manuellab_id"))

def update_fitting_assets(ui, _):
    global additional_assets
    path = ui.fitting_library_dir
    if not path:
        return
    additional_assets = load_data_dir(path, ".blend")

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

    aliases = parse_file(os.path.join(chardir, "aliases.yaml"), load_yaml, None)
    char_aliases.clear()
    for k, v in aliases.items():
        for k2 in v if isinstance(v, list) else (v,):
            char_aliases[k2] = k

    for char_name in sorted(os.listdir(chardir)):
        if not os.path.isdir(os.path.join(chardir, char_name)):
            continue
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

def ensure_basis_sk(obj):
    if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
        obj.shape_key_add(name="Basis", from_mix=False)
    return utils.get_basis_numpy(obj)

def list_morph_dir(path):
    if not os.path.isdir(path):
        return ()
    jslist = parse_file(os.path.join(path, "morphs.json"), json.load, None)
    if jslist is not None:
        return jslist

    return ({"morph": name[:-4]} for name in sorted(os.listdir(path))
        if name.endswith(".npz") and os.path.isfile(os.path.join(path, name)))

def create_morph_sk(obj, prefix, morph, counter):
    if morph.get("separator"):
        obj.shape_key_add(name="---- sep-%d-%d ----" % tuple(counter), from_mix=False)
        counter[1] += 1
        return None

    sk = obj.shape_key_add(name=prefix + morph["morph"], from_mix=False)
    sk.slider_min = morph.get("min", 0)
    sk.slider_max = morph.get("max", 1)
    return sk

def import_morphs(obj, char_name):
    basis = ensure_basis_sk(obj)
    path = char_file(char_name, "morphs/L1")
    L1_basis_dict = {}
    if os.path.isdir(path):
        for file in sorted(os.listdir(path)):
            if os.path.isfile(os.path.join(path, file)):
                name = os.path.splitext(file)[0]
                morph = import_morph(None, obj.shape_key_add(name="L1_" + name, from_mix=False), os.path.join(path, file))
                if morph is not None:
                    L1_basis_dict[name] = morph

    path = char_file(char_name, "morphs/L2")
    if not os.path.isdir(path):
        return

    counter = [2, 1]

    for morph in list_morph_dir(path):
        sk = create_morph_sk(obj, "L2__", morph, counter)
        if sk:
            import_morph(basis, sk, os.path.join(path, morph["morph"] + ".npz"))

    for file in sorted(os.listdir(path)):
        if os.path.isdir(os.path.join(path, file)):
            L1_basis = L1_basis_dict.get(file)
            if L1_basis is None:
                logger.error("Unknown L1 type: %s", file)
                continue
            for morph in list_morph_dir(os.path.join(path, file)):
                sk = create_morph_sk(obj, "L2_%s_" % file, morph, counter)
                if sk:
                    sk.relative_key = obj.data.shape_keys.key_blocks["L1_" + file]
                    import_morph(L1_basis, sk, os.path.join(path, file, morph["morph"] + ".npz"))

def import_expressions(obj, char_name):
    basis = ensure_basis_sk(obj)
    counter = [3,1]
    path = char_file(char_name, "morphs/L3")
    for morph in  list_morph_dir(path):
        sk = create_morph_sk(obj, "L3_", morph, counter)
        if sk:
            import_morph(basis, sk, os.path.join(path, morph["morph"] + ".npz"))

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

def get_basis(data, use_morpher=True):
    if isinstance(data, bpy.types.Object):
        data = data.data
    k = data.shape_keys
    if k:
        return utils.verts_to_numpy(k.reference_key.data)

    m = morphing.morpher
    if use_morpher and m and m.obj.data == data:
        return m.get_basis_alt_topo()

    char = char_by_name(data.get("charmorph_template"))

    alt_topo = data.get("cm_alt_topo")
    if isinstance(alt_topo, bpy.types.Object) or isinstance(alt_topo, bpy.types.Mesh):
        return get_basis(alt_topo)
    if char.name:
        if not alt_topo:
            return char.get_basis()
        if isinstance(alt_topo, str):
            return char_by_name(data.get("charmorph_template")).get_np("morphs/alt_topo/" + alt_topo)

    return utils.verts_to_numpy(data.vertices)

def import_obj(file, obj, typ="MESH", link=True):
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

        if ui.alt_topo != "<Base>" and char.faces is None:
            self.report({'ERROR'}, "Cannot use alternative topology when the character doesn't have faces.npy")
            return {"CANCELLED"}

        if ui.alt_topo == "<Custom>":
            if not ui.alt_topo_obj or ui.alt_topo_obj.type != "MESH":
                self.report({'ERROR'}, "Please select correct custom alternative topology object")
                return {"CANCELLED"}

            orig_mesh = ui.alt_topo_obj.data
            mesh = orig_mesh.copy()
            mesh.name = char.name
            #TODO: cleanup shape keys
            mesh["cm_alt_topo"] = orig_mesh

            obj = bpy.data.objects.new(char.name, mesh)
            context.collection.objects.link(obj)
        else:
            obj = import_obj(char.blend_file(), char.char_obj)
            if obj is None:
                self.report({'ERROR'}, "Import failed")
                return {"CANCELLED"}

            if not ui.use_sk:
                ui.import_morphs = False
                ui.import_expressions = False

            if ui.import_morphs:
                import_morphs(obj, ui.base_model)
            elif os.path.isdir(char.path("morphs")):
                obj.data["cm_morpher"] = "ext"
            if ui.import_expressions:
                import_expressions(obj, ui.base_model)

            materials.init_materials(obj, char)

        obj.location = context.scene.cursor.location
        if ui.import_cursor_z:
            obj.rotation_mode = "XYZ"
            obj.rotation_euler = (0, 0, context.scene.cursor.rotation_euler[2])

        obj.data["charmorph_template"] = ui.base_model

        morphing.create_charmorphs(obj)
        context.view_layer.objects.active = obj
        ui.fitting_char = obj

        if char.randomize_incl_regex is not None:
            ui.randomize_incl = char.randomize_incl_regex
        if char.randomize_excl_regex is not None:
            ui.randomize_excl = char.randomize_excl_regex

        if char.default_armature and ui.fin_rig == '-':
            ui.fin_rig = char.default_armature

        assets = []
        def add_assets(lst):
            assets.extend((char.assets[name] for name in lst))
        add_assets(char.default_assets)
        if not is_adult_mode():
            add_assets(char.underwear)

        fitting.fit_import(obj, assets)

        return {"FINISHED"}

class UIProps:
    base_model: bpy.props.EnumProperty(
        name="Base",
        items=lambda ui, context: [(name, char.title, char.description) for name, char in chars.items()],
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
    use_sk: bpy.props.BoolProperty(
        name="Use shape keys for morphing", default=False,
        description="Use shape keys during morphing (should be on if you plan to resume morphing later, maybe with other versions of CharMorph)")
    import_morphs: bpy.props.BoolProperty(
        name="Import morphing shape keys", default=False,
        description="Import and morph character using shape keys")
    import_expressions: bpy.props.BoolProperty(
        name="Import expression shape keys", default=False,
        description="Import and morph character using shape keys")
    alt_topo: bpy.props.EnumProperty(
        name="Alt topo",
        default="<Base>",
        description="Select alternative topology to use",
        items=[
            ("<Base>", "<Base>", "Use base character topology"),
            ("<Custom>", "<Custom>", "Use custom local object as alt topo")]
        )
    alt_topo_obj: bpy.props.PointerProperty(
        name="Custom alt topo",
        type=bpy.types.Object,
        description="Select custom object to use as alternative topology")

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
        ui = context.window_manager.charmorph_ui
        l.operator('charmorph.reload_library')
        l.separator()
        if data_dir == "":
            l.label(text="Data dir is not found. Importing is not available.")
            return
        if not chars:
            l.label(text="No characters found at {}. Nothing to import.".format(data_dir))
            return
        l.prop(ui, "base_model")
        char = chars.get(ui.base_model)
        if char:
            r = l.row()
            c = r.column()
            c.label(text="Author:")
            c.label(text="License:")
            c = r.column()
            c.alignment = "LEFT"
            c.label(text=char.author)
            c.label(text=char.license)
        l.prop(ui, "material_mode")
        l.prop(ui, "import_cursor_z")
        c = l.column()
        c.prop(ui, "use_sk")
        c = c.column()
        c.enabled = ui.use_sk and ui.alt_topo == "<Base>"
        c.prop(ui, "import_morphs")
        c.prop(ui, "import_expressions")
        l.prop(ui, "alt_topo")
        if ui.alt_topo == "<Custom>":
            l.prop(ui, "alt_topo_obj")

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
