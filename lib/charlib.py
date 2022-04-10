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

from . import utils

logger = logging.getLogger(__name__)

try:
    from yaml import load as yload, CSafeLoader as SafeLoader
except ImportError:
    from .yaml import load as yload, SafeLoader
    logger.debug("Using bundled yaml library!")

data_dir = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "data"))

chars = {}
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
            result[name] = parse_file(full_path, json.load, {})
    return result

_empty_dict = object()

def mblab_to_charmorph(data):
    return {
        "morphs": {k:v*2-1 for k, v in data.get("structural", {}).items()},
        "materials": data.get("materialproperties", {}),
        "meta": {(k[10:] if k.startswith("character_") else k):v for k, v in data.get("metaproperties", {}).items() if not k.startswith("last_character_")},
        "type": data.get("type", []),
    }

def charmorph_to_mblab(data):
    return {
        "structural": {k:(v+1)/2 for k, v in data.get("morphs", {}).items()},
        "metaproperties": {k:v for sublist, v in (([("character_"+k), ("last_character_"+k)], v) for k, v in data.get("meta", {}).items()) for k in sublist},
        "materialproperties": data.get("materials"),
        "type": data.get("type", []),
    }

def load_morph_data(fn):
    with open(fn, "r") as f:
        if fn[-5:] == ".yaml":
            return load_yaml(f)
        if fn[-5:] == ".json":
            return mblab_to_charmorph(json.load(f))
    return None

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
    hairstyles = []
    materials = []
    material_lib = None
    default_assets = []
    underwear = []
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
        return parse_file(self.path(file), load_yaml, default)

    @utils.lazyproperty
    def morphs_meta(self):
        return self.get_yaml("morphs_meta.yaml")

    @utils.lazyproperty
    def fitting_subset(self):
        return self.get_np("fitting_subset.npz")

    @utils.lazyproperty
    def faces(self):
        npy = self.get_np("faces.npy")
        # Use regular python array instead of numpy for compatibility with BVHTree
        return None if npy is None else npy.tolist()

    @utils.lazyproperty
    def assets(self):
        return load_data_dir(self.path("assets"), ".blend")

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
                    data = load_morph_data(fpath)
                    if data is not None:
                        result[os.path.splitext(file)[0]] = data
        except Exception as e:
            logger.error(e)
        return result

    @utils.lazyproperty
    def np_basis(self):
        return self.get_np("morphs/L1/%s.npy" % self.basis)

    @utils.lazyproperty
    def sliding_joints(self):
        return {"_".join((rig.name, jname)): j for rig in self.armature.values() for jname, j in rig.sliding_joints.items()}

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
    tweaks = []
    ik_limits = {}
    sliding_joints = {}
    mixin = ""
    weights: str = None
    arp_reference_layer = 17

    def __init__(self, char: Character, name : str, conf : dict):
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
        obj = ui.fitting_char
        char = obj_char(obj)
        return char.assets.get(item[5:])
    if item.startswith("add_"):
        return additional_assets.get(item[4:])
    return None

def load_library():
    global hair_colors
    t = utils.Timer()
    logger.debug("Loading character library at %s", data_dir)
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

# Morphs handling

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
