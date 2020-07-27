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

import logging
import bpy

logger = logging.getLogger(__name__)

# convert array of min/max binary representation
def convertSigns(signs):
    d = {"min":0,"max":1}
    try:
        return sum(d[sign]<<i for i, sign in enumerate(signs))
    except KeyError:
        return -1

# scan object shape keys and convert them to dictionary
# each morph corresponds to array of shape keys
def get_obj_morphs(obj):
    if obj.type != "MESH":
        return None
    result={}
    for sk in obj.data.shape_keys.key_blocks:
        if not sk.name.startswith("L2__"):
            continue
        nameParts = sk.name[4:].split("_")
        if len(nameParts) != 3:
            logger.error("Invalid L2 morph name: {}, skipping".format(sk.name))
            continue

        names = nameParts[1].split("-")
        signArr = nameParts[2].split("-")
        signIdx = convertSigns(signArr)

        if len(names)==0 or len(names) != len(signArr):
            logger.error("Invalid L2 morph name: {}, skipping".format(sk.name))
            continue

        morph_name = nameParts[0]+"_"+nameParts[1]
        cnt = 2 ** len(names)

        if morph_name in result:
            morph = result[morph_name]
            if len(morph) != cnt:
                logger.error("L2 combo morph conflict: different dimension count on {}, skipping".format(sk.name))
                continue
        else:
            morph = [None] * cnt
            result[morph_name] = morph

        morph[signIdx]=sk

    return result

# create simple prop that drives one min and one max shapekey
def morph_prop_simple(name, skmin, skmax):
    def setter(self, value):
        if value<0:
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
    return sum(0 if sk is None else sk.value * ((arr_idx>>idx&1)*2-1) for arr_idx, sk in enumerate(arr))

# create a bunch of props from combo shape keys
def morph_props_combo(name, arr):
    nameParts = name.split("_")
    names = nameParts[1].split("-")
    dims = len(names)
    coeff = 2 / len(arr)

    values = [ get_combo_value(arr, val_idx) for val_idx in range(dims) ]

    def getterfunc(idx):
        def getter(self):
            val = get_combo_value(arr, idx)
            values[idx]=val
            return val

        return getter

    def setterfunc(idx):
        def setter(self, value):
            values[idx] = value
            for arr_idx, sk in enumerate(arr):
                sk.value = sum(val*((arr_idx>>val_idx&1)*2-1)*coeff for val_idx, val in enumerate(values))
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

# Create a property group with all morphs
def create_charmorphs(morphs):
    del_charmorphs()
    if not morphs:
        return
    propGroup = type("CharMorpher_Dyn_PropGroup",
        (bpy.types.PropertyGroup,),
        {"__annotations__":
            dict(("prop_"+name, prop) for sublist in (morph_props(k,v) for k,v in morphs.items()) for name, prop in sublist)})
    bpy.utils.register_class(propGroup)
    bpy.types.Scene.charmorphs = bpy.props.PointerProperty(type=propGroup,options={"SKIP_SAVE"})

# Delete morphs property group
def del_charmorphs():
    if not hasattr(bpy.types.Scene,"charmorphs"):
        return
    propGroup = bpy.types.Scene.charmorphs[1]['type']
    del bpy.types.Scene.charmorphs
    bpy.utils.unregister_class(propGroup)
