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

import numpy, time
import bpy, mathutils

dist_thresh = 0.1

def do_fit(char, asset):
    dg = bpy.context.evaluated_depsgraph_get()
    bvh = mathutils.bvhtree.BVHTree.FromObject(char, dg, deform=False)
    cverts = char.data.vertices
    cfaces = char.data.polygons
    weights = []
    offs = asset.location
    t1 = time.monotonic()
    for i, avert in enumerate(asset.data.vertices):
        co = avert.co + offs
        loc, norm, idx, dist = bvh.find_nearest(co)

        if not loc or ((co-loc).dot(norm)<=0 and dist > dist_thresh):
            #point is deep inside the mesh, skip it
            weights.append(([],[]))
            continue

        f = cfaces[idx]
        arr = numpy.empty(len(f.vertices))
        total = 0.0
        for i, vi in enumerate(f.vertices):
            dist = (avert.co-cverts[vi].co).length
            total += dist
            arr[i] = dist
        arr /= total
        weights.append((f.vertices, arr))

    t2 = time.monotonic()
    print("bvh: {}".format(t2-t1))

    char_shapekey = char.shape_key_add(from_mix=True)
    char_data = char_shapekey.data
    t3 = time.monotonic()
    if asset.data.shape_keys:
        asset_fitkey = asset.data.shape_keys.key_blocks["fitting"]
    else:
        asset.shape_key_add(name="Basis", from_mix=False)
        asset_fitkey = asset.shape_key_add(name="fitting", from_mix=False)
    asset_data = asset_fitkey.data
    for i, avert in enumerate(asset.data.vertices):
        verts, vweights = weights[i]
        asset_data[i].co = avert.co + sum(((char_data[vi].co-cverts[vi].co)*vweights[j] for j, vi in enumerate(verts)),mathutils.Vector())

    char.shape_key_remove(char_shapekey)
    asset_fitkey.value = 1
    print("fit: {}".format(time.monotonic()-t2))

class CHARMORPH_PT_Fitting(bpy.types.Panel):
    bl_label = "Fitting"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 7

    def draw(self, context):
        self.layout.operator("charmorph.fit")

class OpFit(bpy.types.Operator):
    bl_idname = "charmorph.fit"
    bl_label = "Fit an asset"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return "char" in bpy.data.objects and "asset" in bpy.data.objects

    def execute(self, context):
        do_fit(bpy.data.objects["char"], bpy.data.objects["asset"])
        return {"FINISHED"}

classes = [OpFit, CHARMORPH_PT_Fitting]