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

from . import morphs, utils

logger = logging.getLogger(__name__)

data_dir = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "data"))

char_aliases = {}
additional_assets = {}
hair_colors = {}

if not os.path.isdir(data_dir):
    logger.error("Charmorph data is not found at %s", data_dir)

def char_file(char, file):
    if not char or not file:
        return ""
    if file == ".":
        os.path.join(data_dir, "characters", char)
    return os.path.join(data_dir, "characters", char, file)

def load_data_dir(path, target_ext):
    result = {}
    if not os.path.isdir(path):
        return result
    for file in os.listdir(path):
        name, ext = os.path.splitext(file)
        if ext == target_ext and os.path.isfile(os.path.join(path, file)):
            result[name] = (os.path.join(path, file), name)
    return result

def load_json_dir(path):
    result = {}
    if not os.path.isdir(path):
        return result
    for file in os.listdir(path):
        name, ext = os.path.splitext(file)
        full_path = os.path.join(path, file)
        if ext == ".json" and os.path.isfile(full_path):
            result[name] = utils.parse_file(full_path, json.load, {})
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
    force_hair_scalp = False
    randomize_incl_regex = None
    randomize_excl_regex = None
    default_type = ""
    default_armature = ""
    default_hair_length = 0.1
    default_tex_set = ""
    recurse_materials = False
    armature = {}
    armature_defaults = {}
    bones = {}
    hairstyles = ()
    materials = ()
    material_lib = None
    default_assets = ()
    underwear = ()
    types = {}
    hair_library = None
    hair_obj = None
    hair_shrinkwrap = False
    hair_shrinkwrap_offset = 0.0002

    def __init__(self, name):
        self.title = name
        self.name = name
        self.__dict__.update(self.get_yaml("config.yaml"))
        self.name = name

        if self.material_lib is None:
            self.material_lib = self.char_file

        if not self.default_type and self.basis:
            self.default_type = self.basis

        self.armature = self._parse_armature(self.armature)

    def __bool__(self):
        return bool(self.name)

    def path(self, file):
        return char_file(self.name, file)

    def blend_file(self):
        return self.path(self.char_file)

    def get_np(self, file, readonly=True):
        file = self.path(file)
        if not os.path.isfile(file):
            return None
        result = numpy.load(file)
        if readonly and isinstance(result, numpy.ndarray):
            result.flags.writeable = False
        return result

    def get_yaml(self, file, default=_empty_dict):
        if default is _empty_dict:
            default = {}
        if not self:
            return default
        return utils.parse_file(self.path(file), utils.load_yaml, default)

    @utils.lazyproperty
    def morphs_meta(self):
        return self.get_yaml("morphs_meta.yaml")

    @utils.lazyproperty
    def fitting_subset(self):
        return self.get_np("fitting_subset.npz")

    @utils.lazyproperty
    def has_faces(self):
        return os.path.isfile(self.path("faces.npy"))

    @utils.lazyproperty
    def faces(self):
        npy = self.get_np("faces.npy")
        # Use regular python array instead of numpy for compatibility with BVHTree
        return None if npy is None else npy.tolist()

    @utils.lazyproperty
    def assets(self):
        return load_assets_dir(self.path("assets"))

    @utils.lazyproperty
    def alt_topos(self):
        return load_data_dir(self.path("morphs/alt_topo"), ".npy")

    @utils.lazyproperty
    def poses(self):
        return load_json_dir(self.path("poses"))

    @utils.lazyproperty
    def texture_sets(self):
        path = self.path("textures")
        if os.path.isdir(path):
            result = [item for item in os.listdir(path) if os.path.isdir(os.path.join(path, item))]
            if result:
                result.sort()
                return result
        return ["/"]

    @utils.lazyproperty
    def presets(self):
        return self.load_presets("presets")

    def load_presets(self, path):
        path = self.path(path)
        if not os.path.isdir(path):
            return {}
        result = {}
        try:
            for file in os.listdir(path):
                fpath = os.path.join(path, file)
                if os.path.isfile(fpath):
                    data = morphs.load_morph_data(fpath)
                    if data is not None:
                        result[os.path.splitext(file)[0]] = data
        except Exception as e:
            logger.error(e)
        return result

    @utils.lazyproperty
    def np_basis(self):
        return morphs.np_ro64(self.get_np(f"morphs/L1/{self.basis}.npy"))

    def _parse_armature(self, data):
        if isinstance(data, list):
            return self._parse_armature_list(data)
        return self._parse_armature_dict(data)

    def _parse_armature_list(self, data):
        result = {}
        for i, a in enumerate(data):
            title = a.get("title")
            if title:
                k = title.lower().replace(" ", "_")
            else:
                k = str(i)
                a["title"] = f"<unnamed {k}>"
            if not self.default_armature:
                self.default_armature = k
            result[k] = Armature(self, k, a)
        return result

    def _parse_armature_dict(self, data):
        return {k: Armature(self, k, v) for k,v in data.items()}

# allows to mark some properties of the class as lazy yaml
# if property value is dict or some other value, leave it as is
# if property is a string, treat it as yaml file name, but don't load the yaml file until it's needed
def _lazy_yaml_props(*prop_lst):
    def wrap_class(superclass):
        class Child(superclass):
            def __init__(self, *args):
                super().__init__(*args)
                for prop in prop_lst:
                    value = getattr(self, prop)
                    if isinstance(value, str):
                        setattr(self, "_lazy_yaml_"+prop, value)
                        delattr(self, prop)
        for prop in prop_lst:
            setattr(Child, prop, utils.named_lazyprop(prop, lambda self, name=prop: self.char.get_yaml(getattr(self, "_lazy_yaml_"+name))))
        return Child

    return wrap_class

@_lazy_yaml_props("bones", "mixin_bones")
class Armature:
    type = "regular"
    tweaks = ()
    ik_limits = {}
    sliding_joints = {}
    mixin = ""
    weights: str = None
    arp_reference_layer = 17

    def __init__(self, char: Character, name: str, conf: dict):
        self.title = name
        self.obj_name = name
        self.file = char.char_file
        self.mixin_bones = {}

        self.__dict__.update(char.armature_defaults)
        self.__dict__.update(conf)

        self.char = char

        for item in ("weights", "joints"):
            value = getattr(self, item, None)
            setattr(self, item, char.path(value) if value else char.path(os.path.join(item, name + ".npz")))

        self.name = name

        if "bones" not in self.__dict__:
            self.bones = char.bones # Legacy

    @utils.lazyproperty
    def weights_npz(self):
        return self.char.get_np(self.weights)

class Asset:
    def __init__(self, name, file, path = None):
        self.name = name
        self.blend_file = file
        self.dirpath = path

    def path(self, name):
        if self.dirpath:
            return os.path.join(self.dirpath, name)
        return ""

    @utils.lazyproperty
    def config(self):
        file = self.path("config.yaml")
        return utils.load_yaml(file) if os.path.isfile(file) else {}

    @utils.lazyproperty
    def author(self):
        return self.config.get("author", "")

    @utils.lazyproperty
    def license(self):
        return self.config.get("license", "")

    @utils.lazyproperty
    def morph(self):
        return morphs.load_noext(self.path("morph"))

def get_asset(asset_dir: str, name: str):
    path = os.path.join(asset_dir, name)
    if os.path.isdir(path):
        for fname in (name, "asset"):
            fname = os.path.join(path, fname + ".blend")
            if os.path.isfile(fname):
                return Asset(name, fname, path)
    elif name.endswith(".blend"):
        return Asset(name[:-6], path)
    return None

def load_assets_dir(path):
    result = {}
    if not os.path.isdir(path):
        return result
    for item in os.listdir(path):
        asset = get_asset(path, item)
        if asset:
            result[asset.name] = asset
    item = os.path.join(path, "authors.yaml")
    if os.path.isfile(item):
        for item in utils.parse_file(item, utils.load_yaml, ()):
            assets = item.get("items", ())
            del item["items"]
            for name in assets:
                asset = result.get(name)
                if asset:
                    asset.config.update(item)
    return result

def update_fitting_assets(ui, _):
    global additional_assets
    path = ui.fitting_library_dir
    if not path:
        return
    additional_assets = load_assets_dir(path)

chars: dict[str, Character] = {}
empty_char = Character("")

def char_by_name(name):
    return chars.get(name) or chars.get(char_aliases.get(name)) or empty_char

def obj_char(obj) -> Character:
    if not obj:
        return empty_char
    return char_by_name(obj.data.get("charmorph_template") or obj.get("manuellab_id"))

def load_library():
    global hair_colors
    t = utils.Timer()
    logger.debug("Loading character library at %s", data_dir)
    chars.clear()
    hair_colors = utils.parse_file(os.path.join(data_dir, "hair_colors.yaml"), utils.load_yaml, {})
    chardir = os.path.join(data_dir, "characters")
    if not os.path.isdir(chardir):
        logger.error("Directory %s is not found.", format(chardir))
        return

    aliases = utils.parse_file(os.path.join(chardir, "aliases.yaml"), utils.load_yaml, None)
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

def get_basis(data, mcore = None, use_char=True):
    if isinstance(data, bpy.types.Object):
        data = data.data
    k = data.shape_keys
    if k:
        return utils.verts_to_numpy(k.reference_key.data)

    if mcore and mcore.obj.data == data:
        return mcore.get_basis_alt_topo()

    alt_topo = data.get("cm_alt_topo")
    if isinstance(alt_topo, (bpy.types.Object, bpy.types.Mesh)):
        return get_basis(alt_topo, None, False)

    char = None
    if use_char:
        char = char_by_name(data.get("charmorph_template"))

    if char:
        if not alt_topo:
            return char.np_basis
        elif isinstance(alt_topo, str):
            return char_by_name(data.get("charmorph_template")).get_np("morphs/alt_topo/" + alt_topo)

    return utils.verts_to_numpy(data.vertices)
