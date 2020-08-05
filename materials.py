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

from . import library

logger = logging.getLogger(__name__)

def init_materials(obj, char):
    load_materials(obj, char)
    #load_textures(obj)

def load_materials(obj, char):
    if not "materials" in char.config:
        logger.error("no material config for " + char.name)
        return

    mtllist = char.config["materials"]
    if len(obj.data.materials) != len(mtllist):
        logger.error("Material count mismatch in {}: {} != {}".format(char, len(obj.data.materials), len(mtllist)))
        return

    ui = bpy.context.scene.charmorph_ui
    materials_to_load = []
    load_ids = []
    adult_mode = library.is_adult_mode()
    for i, mtl_name in enumerate(mtllist):
        mtl = None
        if ui.material_local or ui.material_mode == "MS":
            mtl = bpy.data.materials.get(mtl_name)
        if mtl:
            if ui.material_mode != "MS":
                mtl = mtl.copy()
            obj.data.materials[i] = mtl
        elif not "_censor" in mtl_name or not adult_mode:
            materials_to_load.append(mtl_name)
            load_ids.append(i)

    if "material_lib" in char.config and materials_to_load:
        with bpy.data.libraries.load(library.char_file(char.name, char.config["material_lib"])) as (_, data_to):
            data_to.materials = materials_to_load
        for i, mtl in enumerate(data_to.materials):
            obj.data.materials[load_ids[i]]=data_to.materials[i]

    if adult_mode:
        for i in range(len(mtllist)-1,0,-1):
            if "_censor" in mtllist[i]:
                obj.data.materials.pop(i)


def material_props(obj):
    if not obj.data.materials:
        return []
    return [] # TODO
