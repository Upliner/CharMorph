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

import os, json, yaml, logging
import bpy

from . import library, materials, fitting

logger = logging.getLogger(__name__)

last_object = None
cur_object = None
asset_lock = False
meta_lock = False

# convert array of min/max binary representation
def convertSigns(signs):
    d = {"min": 0, "max": 1}
    try:
        return sum(d[sign] << i for i, sign in enumerate(signs))
    except KeyError:
        return -1

def updateL1(L1data, newkey):
    for name, sk in L1data.items():
        sk.value = 1 if name == newkey else 0

# scan object shape keys and convert them to dictionary
# each morph corresponds to one or more shape keys
def get_morphs_L1(obj):
    if not obj.data.shape_keys:
        return "", {}
    maxkey = ""
    result = {}
    maxval = 0
    for sk in obj.data.shape_keys.key_blocks:
        if len(sk.name) < 4 or not sk.name.startswith("L1_"):
            continue
        name = sk.name[3:]
        if sk.value > maxval:
            maxkey = name
            maxval = sk.value
        result[name] = sk

    updateL1(result, maxkey)

    return (maxkey, result)

def get_morphs_L2(obj, L1):
    if not obj.data.shape_keys:
        return {}

    result = {}

    def handle_shapekey(sk, keytype):
        if not sk.name.startswith("L2_{}_".format(keytype)):
            return
        nameParts = sk.name[3:].split("_")
        if len(nameParts) != 4:
            logger.error("Invalid L2 morph name: {}, skipping".format(sk.name))
            return

        names = nameParts[2].split("-")
        signArr = nameParts[3].split("-")
        signIdx = convertSigns(signArr)

        if len(names) == 0 or len(names) != len(signArr):
            logger.error("Invalid L2 morph name: {}, skipping".format(sk.name))
            return

        morph_name = nameParts[1]+"_"+nameParts[2]
        cnt = 2 ** len(names)

        if morph_name in result:
            morph = result[morph_name]
            if len(morph) != cnt:
                logger.error("L2 combo morph conflict: different dimension count on {}, skipping".format(sk.name))
                return
        else:
            morph = [None] * cnt
            result[morph_name] = morph

        morph[signIdx] = sk

    for sk in obj.data.shape_keys.key_blocks:
        handle_shapekey(sk, "")

    if L1 != "":
        for sk in obj.data.shape_keys.key_blocks:
            handle_shapekey(sk, L1)

    return result

def refit_assets():
    if asset_lock or not cur_object:
        return
    fitting.refit_char_assets(cur_object)

# create simple prop that drives one min and one max shapekey
def morph_prop_simple(name, skmin, skmax):
    def setter(self, value):
        self.version += 1
        if value < 0:
            if skmax != None: skmax.value = 0
            if skmin != None: skmin.value = -value
        else:
            if skmin != None: skmin.value = 0
            if skmax != None: skmax.value = value
        refit_assets()
    return bpy.props.FloatProperty(name=name,
        soft_min = -1.0, soft_max = 1.0,
        precision = 3,
        get = lambda self: (0 if skmax==None else skmax.value) - (0 if skmin==None else skmin.value),
        set = setter)

def get_combo_value(arr, idx):
    return sum(0 if sk is None else sk.value * ((arr_idx >> idx & 1)*2-1) for arr_idx, sk in enumerate(arr))

# create a bunch of props from combo shape keys
def morph_props_combo(name, arr):
    nameParts = name.split("_")
    names = nameParts[1].split("-")
    dims = len(names)
    coeff = 2 / len(arr)

    values = [get_combo_value(arr, val_idx) for val_idx in range(dims)]

    def getterfunc(idx):
        def getter(self):
            val = get_combo_value(arr, idx)
            values[idx] = val
            return val

        return getter

    def setterfunc(idx):
        def setter(self, value):
            if bpy.context.scene.charmorph_ui.clamp_combos:
                value = max(min(value, 1), -1)
            self.version += 1
            values[idx] = value
            for arr_idx, sk in enumerate(arr):
                sk.value = sum(val*((arr_idx >> val_idx & 1)*2-1) * coeff for val_idx, val in enumerate(values))
        return setter

    return [(name, bpy.props.FloatProperty(name=name,
            soft_min = -1.0, soft_max = 1.0,
            precision = 3,
            get = getterfunc(i),
            set = setterfunc(i),
        )) for i, name in ((i, nameParts[0]+"_"+name) for i, name in enumerate(names))]

def morph_props(name, arr):
    if len(arr) == 2:
        return [(name, morph_prop_simple(name, arr[0], arr[1]))]
    else:
        return morph_props_combo(name, arr)

def mblab_to_charmorph(data):
    return {
        "morphs": { k:v*2-1 for k,v in data.get("structural",{}).items() },
        "materials": data.get("materialproperties",{}),
        "meta": { (k[10:] if k.startswith("character_") else k):v for k,v in data.get("metaproperties",{}).items() if not k.startswith("last_character_")},
        "type": data.get("type",[]),
    }

def charmorph_to_mblab(data):
    return {
        "structural": { k:(v+1)/2 for k,v in data.get("morphs",{}).items() },
        "metaproperties": { k:v for sublist, v in (([("character_"+k),("last_character_"+k)],v) for k,v in data.get("meta",{}).items()) for k in sublist },
        "materialproperties": data.get("materials"),
        "type": data.get("type",[]),
    }

def load_morph_data(fn):
    with open(fn, "r") as f:
        if fn[-5:] == ".yaml":
            return yaml.safe_load(f)
        elif fn[-5:] == ".json":
            return  mblab_to_charmorph(json.load(f))
    return None

def load_presets(char, L1):
    result = {}
    def load_dir(path):
        path = os.path.join(library.data_dir, path)
        if not os.path.isdir(path):
            return {}
        for fn in os.listdir(path):
            if os.path.isfile(os.path.join(path, fn)):
                data = load_morph_data(os.path.join(path, fn))
                if data != None:
                    result[fn[:-5]] = data
    try:
        load_dir("characters/{}/presets".format(char))
        load_dir("characters/{}/presets/{}".format(char, L1))
    except Exception as e:
        logger.error(e)
    return result

def meta_props(name, data):
    def update(self, context):
        global asset_lock
        if meta_lock:
            return
        prev_value = getattr(self, "metaprev_" + name)
        value = getattr(self, "meta_" + name)
        if value == prev_value:
            return
        setattr(self, "metaprev_" + name, value)
        relative_meta = context.scene.charmorph_ui.relative_meta
        def calc_val(val):
            return coeffs[1]*val if val > 0 else -coeffs[0]*val
        asset_lock = True
        for k, coeffs in data.get("morphs",{}).items():
            propname = "prop_" + k
            if not hasattr(self, propname):
                continue

            if not relative_meta:
                setattr(self, propname, calc_val(value))
                continue

            propval = getattr(self, propname)

            val_prev = calc_val(prev_value)
            val_cur = calc_val(value)

            # assign absolute prop value if current property value is out of range
            # or add a delta if it is within (-0.999 .. 0.999)
            sign = -1 if val_cur-val_prev < 0 else 1
            if propval*sign<-0.999 and val_prev*sign < -1:
                 propval = val_cur
            else:
                propval += val_cur-val_prev
            setattr(self, propname, propval)

        asset_lock = False

        for k, coeffs in data.get("materials",{}).items():
            if materials.props and k in materials.props:
                materials.props[k].default_value = calc_val(value)

        refit_assets()

    return [("metaprev_"+name,bpy.props.FloatProperty()),
        ("meta_"+name,bpy.props.FloatProperty(name=name,
        min = -1.0, max = 1.0,
        precision = 3,
        update = update))]


def clear_old_L2(obj, new_L1):
    for sk in obj.data.shape_keys.key_blocks:
        if sk.name.startswith("L2_") and not sk.name.startswith("L2__") and not sk.name.startswith("L2_{}_".format(new_L1)):
            sk.value = 0

def create_charmorphs(obj):
    global last_object, cur_object
    last_object = obj
    if obj.type != "MESH":
        return

    L1, morphs = get_morphs_L1(obj)

    char = library.obj_char(obj)
    items = [(name, char.config.get("types",{}).get(name, {}).get("title",name), "") for name in morphs.keys()]

    cur_object = obj
    materials.update_props(obj)
    mtl_props = materials.props

    def update_char():
        updateL1(morphs, L1)
        materials.apply_props(char.config.get("types", {}).get(L1, {}).get("mtl_props"), mtl_props)
        clear_old_L2(obj, L1)
        refit_assets()

    if L1=="" and "default_type" in char.config:
        L1 = char.config["default_type"]
        update_char()

    L1_idx = 0
    for i in range(1, len(items)-1):
        if items[i][0] == L1:
            L1_idx = i
            break

    def chartype_setter(self, value):
        nonlocal L1_idx, L1
        if value == L1_idx:
            return
        L1_idx = value
        L1 = items[L1_idx][0]
        update_char()
        create_charmorphs_L2(obj, char, L1)

    bpy.types.Scene.chartype = bpy.props.EnumProperty(
        name="Type",
        items=items,
        description="Choose character type",
        get=lambda self: L1_idx,
        set=chartype_setter,
        options={"SKIP_SAVE"})

    create_charmorphs_L2(obj, char, L1)


def option_props():
    return [("version", bpy.props.IntProperty())]

def apply_morph_data(charmorphs, data, preset_mix):
    global meta_lock, asset_lock
    morph_props = data.get("morphs", {})
    meta_props = data.get("meta", {})
    meta_lock = True
    asset_lock = True
    for prop in dir(charmorphs):
        if prop.startswith("prop_"):
            value = morph_props.get(prop[5:], 0)
            if preset_mix:
                value = (value+getattr(charmorphs, prop))/2
            setattr(charmorphs, prop, value)
        elif prop.startswith("meta"):
            # TODO handle preset_mix?
            setattr(charmorphs, prop, meta_props.get(prop[prop.find("_")+1:],0))
    asset_lock = False
    meta_lock = False
    refit_assets()
    materials.apply_props(data.get("materials"))

def preset_prop(char, L1):
    if char == "":
        return []
    presets = load_presets(char, L1)

    def update(self, context):
        if not self.preset:
            return
        apply_morph_data(self, presets.get(self.preset, {}), context.scene.charmorph_ui.preset_mix)

    return [("preset", bpy.props.EnumProperty(
        name="Presets",
        default="_",
        items=[("_", "(reset)", "")] + [(name, name, "") for name in sorted(presets.keys())],
        description="Choose morphing preset",
        update=update))]

def morph_categories_prop(morphs):
    return [("category",bpy.props.EnumProperty(
        name="Category",
        items=[("<None>","<None>","Hide all morphs"), ("<All>","<All>","Show all morphs")] +
            [(name,name,"") for name in sorted(set(morph[:morph.find("_")] for morph in morphs.keys()))],
        description="Select morphing categories to show"))]

# Create a property group with all L2 morphs
def create_charmorphs_L2(obj, char, L1):

    del_charmorphs_L2()
    morphs = get_morphs_L2(obj, L1)
    if not morphs:
        return

    propGroup = type("CharMorpher_Dyn_PropGroup",
        (bpy.types.PropertyGroup,),
        {"__annotations__":
            dict(option_props() + preset_prop(char.name, L1) + morph_categories_prop(morphs) +
                [("prop_"+name, prop) for sublist in (morph_props(k, v) for k, v in morphs.items()) for name, prop in sublist] +
                [item for sublist in (meta_props(name, data) for name, data in char.morphs_meta.items()) for item in sublist])})

    bpy.utils.register_class(propGroup)

    bpy.types.Scene.charmorphs = bpy.props.PointerProperty(
        type=propGroup, options={"SKIP_SAVE"})

# Reset all meta properties to 0
def reset_meta(charmorphs):
    global meta_lock
    meta_lock = True
    for prop in dir(charmorphs):
        if prop.startswith("meta"):
            setattr(charmorphs, prop, 0)
    meta_lock = False

# Delete morphs property group
def del_charmorphs_L2():
    global asset_lock, meta_lock
    if not hasattr(bpy.types.Scene, "charmorphs"):
        return
    asset_lock = False
    meta_lock = False
    propGroup = bpy.types.Scene.charmorphs[1]['type']
    del bpy.types.Scene.charmorphs
    bpy.utils.unregister_class(propGroup)
    fitting.invalidate_cache()

def del_charmorphs():
    global last_object, cur_object
    last_object = None
    cur_object = None
    if hasattr(bpy.types.Scene, "chartype"):
        del bpy.types.Scene.chartype
    del_charmorphs_L2()

class CHARMORPH_PT_Morphing(bpy.types.Panel):
    bl_label = "Morphing"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 2

    @classmethod
    def poll(cls, context):
        return hasattr(context.scene,'charmorphs')

    def draw(self, context):
        morphs = context.scene.charmorphs
        ui = context.scene.charmorph_ui
        propList = sorted(dir(morphs))
        self.layout.label(text= "Character type")
        col = self.layout.column(align=True)

        col.prop(context.scene,"chartype")
        if hasattr(morphs,"preset"):
            col.prop(morphs, "preset")
            col.prop(ui, "preset_mix")

        col.separator()

        meta_morphs = [p for p in propList if p.startswith("meta_")]
        if len(meta_morphs) > 0:
            self.layout.label(text = "Meta morphs")
            col = self.layout.column(align=True)
            col.prop(ui, "relative_meta")

            for prop in meta_morphs:
                col.prop(morphs, prop, slider=True)

        self.layout.prop(ui, "clamp_combos")

        self.layout.separator()

        self.layout.prop(morphs, "category")
        if morphs.category != "<None>":
            col = self.layout.column(align=True)
            for prop in (p for p in propList if p.startswith("prop_" + ("" if morphs.category == "<All>" else morphs.category + "_"))):
                col.prop(morphs, prop, slider=True)

classes = [CHARMORPH_PT_Morphing]
