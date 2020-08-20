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
import mathutils

logger = logging.getLogger(__name__)

def get_joints(context, is_all):
    joints = []
    for bone in context.object.data.edit_bones:
        if is_all or bone.select_head:
            joints.append(("joint_"+bone.name+"_head", bone.head, bone, "head"))
        if is_all or bone.select_tail:
            joints.append(("joint_"+bone.name+"_tail", bone.tail, bone, "tail"))
    return joints

def all_joints(context):      return get_joints(context, True)
def selected_joints(context): return get_joints(context, False)

def get_vg_data(char, new, accumulate):
    data = {}
    for vid, v in enumerate(char.data.vertices):
        for gw in v.groups:
            vg = char.vertex_groups[gw.group]
            if not vg.name.startswith("joint_"):
                continue
            data_item = data.get(vg.name)
            if not data_item:
                data_item = new()
                data[vg.name] = data_item
            accumulate(data_item, vid, v, gw)
    return data

def get_vg_avg(char):
    def accumulate(data_item, vid, v, gw):
        data_item[0] += gw.weight
        data_item[1] += v.co*gw.weight
    return get_vg_data(char, lambda: [0, mathutils.Vector()], accumulate)

def joints_to_vg(char, lst):
    avg = get_vg_avg(char)
    result = True
    for name, _, bone, attr in lst:
        item = avg.get(name)
        if item:
            pos = item[1]/item[0]
            offs = bone.get("charmorph_offs_" + attr)
            if offs and len(offs) == 3:
                pos += mathutils.Vector(tuple(offs))
            setattr(bone, attr, pos)
        else:
            logger.error("No vg for " + name)
            result = False
    return result

def rigify_add_deform(context, char):
    for vg in char.vertex_groups:
        if vg.name.startswith("ORG-") or vg.name.startswith("MCH-"):
            context.object.data.edit_bones[vg.name].use_deform = True
