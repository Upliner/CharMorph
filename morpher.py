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

import os
import json
import logging
import bpy

from . import utils

logger = logging.getLogger(__name__)

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
# each morph corresponds to array of shape keys
def get_morphs_L1(obj):
    if obj.type != "MESH":
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
    if obj.type != "MESH":
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

# create simple prop that drives one min and one max shapekey
def morph_prop_simple(name, skmin, skmax):
    def setter(self, value):
        if value < 0:
            if skmax != None: skmax.value = 0
            if skmin != None: skmin.value = -value
        else:
            if skmin != None: skmin.value = 0
            if skmax != None: skmax.value = value
    return bpy.props.FloatProperty(name=name,
        soft_min = -1.0, soft_max = 1.0,
        precision = 3,
        subtype = "FACTOR",
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
            if self.clamp_combos:
                if value<-1:
                    value=-1
                if value>1:
                    value=1
            values[idx] = value
            for arr_idx, sk in enumerate(arr):
                sk.value = sum(val*((arr_idx >> val_idx & 1)*2-1) * coeff for val_idx, val in enumerate(values))
        return setter

    return [(name, bpy.props.FloatProperty(name=name,
            soft_min = -1.0, soft_max = 1.0,
            precision = 3,
            subtype = "FACTOR",
            get = getterfunc(i),
            set = setterfunc(i),
        )) for i, name in ((i, nameParts[0]+"_"+name) for i, name in enumerate(names))]

def morph_props(name, arr):
    if len(arr) == 2:
        return [(name, morph_prop_simple(name, arr[0], arr[1]))]
    else:
        return morph_props_combo(name, arr)

def load_meta(char):
    if char == "":
        return
    try:
        with open(os.path.join(utils.data_dir, "characters/{}/morphs_meta.json".format(char)), "r") as f:
            return json.load(f)
    except Exception as e:
        print(e)
        return {}

def load_presets(char, L1):
    result = {}
    def load_dir(path):
        path = os.path.join(utils.data_dir, path)
        if not os.path.isdir(path):
            return {}
        for fn in os.listdir(path):
            if fn[-5:] == ".json" and os.path.isfile(os.path.join(path, fn)):
                with open(os.path.join(path, fn), "r") as f:
                    result[fn[:-5]] = json.load(f)
    try:
        load_dir("characters/{}/presets".format(char))
        load_dir("characters/{}/presets/{}".format(char, L1))
    except Exception as e:
        print(e)
    return result

def meta_prop(name, data):
    def update(self, context):
        value = getattr(self, "meta_" + name)
        for prop in data:
            propname = "prop_"+prop[0]
            if not hasattr(self, propname):
                continue
            if value < 0:
                propval = prop[1]*-value
            else:
                propval = prop[2]*value
            setattr(self, propname, propval*2)

    return bpy.props.FloatProperty(name=name,
        soft_min = -1.0, soft_max = 1.0,
        precision = 3,
        subtype = "FACTOR",
        update = update)


def clear_old_L2(obj, new_L1):
    for sk in obj.data.shape_keys.key_blocks:
        if sk.name.startswith("L2_") and not sk.name.startswith("L2__") and not sk.name.startswith("L2_{}_".format(new_L1)):
            sk.value = 0

def create_charmorphs(obj):
    L1, morphs = get_morphs_L1(obj)
    if len(morphs) == 0:
        return

    char = obj.get("charmorph_template", "")
    items = [("", "(empty)", "")] + [(name, name, "") for name in morphs.keys()]
    L1_idx = 0
    for i in range(1, len(items)-1):
        if items[i][0] == L1:
            L1_idx = i
            break

    def chartype_setter(self, value):
        nonlocal L1_idx
        L1_idx = value
        L1 = items[L1_idx][0]
        updateL1(morphs, L1)
        clear_old_L2(obj, L1)
        create_charmorphs_L2(obj, char, L1)

    if hasattr(bpy.types.Scene, "chartype"):
        del bpy.types.Scene.chartype

    bpy.types.Scene.chartype = bpy.props.EnumProperty(
        name="Type",
        items=items,
        description="Choose character type",
        get=lambda self: L1_idx,
        set=chartype_setter)

    create_charmorphs_L2(obj, char, L1)


def preset_props(char, L1):
    mix_prop = ("preset_mix", bpy.props.BoolProperty(
        name="Mix with current",
        description="Mix selected preset with current morphs",
        default=False))

    clamp_prop = ("clamp_combos", bpy.props.BoolProperty(
        name="Clamp combo props",
        description="Clamp combo properties to (-1..1) so they remain in realistic range",
        default=True))

    if char == "":
        return [clamp_prop]
    presets = load_presets(char, L1)

    def update(self, context):
        if not self.preset:
            return
        data = presets.get(self.preset, {})
        preset_props = data.get("structural",{})
        for prop in dir(self):
            if prop.startswith("prop_"):
                value = preset_props.get(prop[5:], 0.5)*2-1
                if self.preset_mix:
                    value = (value+getattr(self, prop))/2
                setattr(self, prop, value)

    items = [("_", "(empty)", "")] +\
        [(name, name, "") for name in sorted(presets.keys())]
    return [mix_prop, clamp_prop,
        ("preset", bpy.props.EnumProperty(
        name="Presets",
        default="_",
        items=items,
        description="Choose morphing preset",
        update=update))]

def morph_categories_prop(morphs):
    return [("category",bpy.props.EnumProperty(
        name="Category",
        items=[("<None>","<None>",""),("<All>","<All>","")] +
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
            dict(preset_props(char, L1) + morph_categories_prop(morphs) +
                [("prop_"+name, prop) for sublist in (morph_props(k, v) for k, v in morphs.items()) for name, prop in sublist] +
                [("meta_"+name, meta_prop(name, data)) for name, data in load_meta(char).items()])})

    bpy.utils.register_class(propGroup)

    bpy.types.Scene.charmorphs = bpy.props.PointerProperty(
        type=propGroup, options={"SKIP_SAVE"})

# Delete morphs property group
def del_charmorphs_L2():
    if not hasattr(bpy.types.Scene, "charmorphs"):
        return
    propGroup = bpy.types.Scene.charmorphs[1]['type']
    del bpy.types.Scene.charmorphs
    bpy.utils.unregister_class(propGroup)

def del_charmorphs():
    if hasattr(bpy.types.Scene, "chartype"):
        del bpy.types.Scene.chartype
    del_charmorphs_L2()
