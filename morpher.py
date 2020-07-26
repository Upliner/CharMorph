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

# create n-dimensional array
def ndim_create(n):
    if n==1:
        return [None, None]
    return [ndim_create(n-1), ndim_create(n-1)]

# assign a value in n-dimensional array
def ndim_assign(arr, signs, value):
    if len(signs)==1:
        arr[signs[0]] = value
        return
    ndim_assign(arr[signs[0]], signs[1:], value)

# convert array of min/max to 0/1
def convertSigns(signs):
    d = {"min":0,"max":1}
    try:
        return [d[sign] for sign in signs]
    except KeyError:
        return []

# scan object shape keys and convert them to dictionary
# each morph corresponds to n-dimensional array of shape keys
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
        signs = convertSigns(nameParts[2].split("-"))

        if len(names)==0 or len(names) != len(signs):
            logger.error("Invalid L2 morph name: {}, skipping".format(sk.name))
            continue

        dims = len(names)
        morph_name = nameParts[0]+"_"+nameParts[1]

        if morph_name in result:
            morph = result[morph_name]
            if morph[0] != dims:
                logger.error("L2 combo morph conflict: different dimension count on {}, skipping".format(sk.name))
                continue
        else:
            morph = (dims, ndim_create(dims))
            result[morph_name] = morph

        ndim_assign(morph[1], signs, sk)

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
        min = -1.0, max = 1.0,
        soft_min = -1.0, soft_max = 1.0,
        precision = 3,
        subtype="FACTOR",
        get=lambda self: (0 if skmax==None else skmax.value) - (0 if skmin==None else skmin.value),
        set=setter)

def walk_ndim(indices,arr):
    for i, value in enumerate(arr):
        if isinstance(value, list):
            for i2, val2 in walk_ndim(indices+[i],value):
                yield i2, val2
        else:
            yield indices+[i], value

# create a bunch of props from combo shape keys
def morph_props_combo(name, tup):
    dims = tup[0]
    nameParts = name.split("_")
    names = nameParts[1].split("-")

    for i in range(len(names)):
        names[i] = nameParts[0]+"_"+names[i]

    arr = tup[1]

    # calculate combo values at moment of creation
    values = [0] * dims
    for indices, sk in walk_ndim([], arr):
        for idx, sign in enumerate(indices):
            values[idx] += sk.value*(sign*2-1)

    coeff = 2 ** (1-dims)

    def update(self, context):
        values = [ getattr(self, "prop_"+name) for name in names ]
        for indices, sk in walk_ndim([], arr):
            value = 0
            for idx, sign in enumerate(indices):
                value += values[idx]*(sign*2-1)*coeff
            print(sk.name,value,values, indices)
            sk.value=value

    return [(name, bpy.props.FloatProperty(name=name,
        min = -dims, max = dims,
        soft_min = -1.0, soft_max = 1.0,
        default=values[i],
        precision = 3,
        subtype="FACTOR",
        update=update)) for i, name in enumerate(names)]

def morph_props(name, tup):
    if tup[0] == 1:
        return [(name, morph_prop_simple(name, tup[1][0], tup[1][1]))]
    else:
        return morph_props_combo(name, tup)

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
