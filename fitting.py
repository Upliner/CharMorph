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

import time
import bpy, mathutils

dist_thresh = 0.1
epsilon = 1e-30

def do_fit(char, asset):
    t1 = time.monotonic()
    dg = bpy.context.evaluated_depsgraph_get()

    char_verts = char.data.vertices
    char_faces = char.data.polygons
    asset_verts = asset.data.vertices
    asset_faces = asset.data.polygons

    weights = []
    offs = asset.location-char.location

    # calculate weights based on distance from asset vertices to character faces
    bvh_char = mathutils.bvhtree.BVHTree.FromObject(char, dg, deform=False)
    for i, avert in enumerate(asset_verts):
        co = avert.co + offs
        loc, norm, idx, fdist = bvh_char.find_nearest(co)

        fdist = max(fdist, epsilon)

        if not loc or ((co-loc).dot(norm)<=0 and fdist > dist_thresh):
            #point is deep inside the mesh, skip it
            weights.append({})
            continue

        weights.append({vi:1/((co-char_verts[vi].co).length * fdist) for vi in char_faces[idx].vertices})

    t2 = time.monotonic()
    print("bvh direct: {}".format(t2-t1))

    # calculate weights based on distance from character vertices to assset faces
    bvh_asset = mathutils.bvhtree.BVHTree.FromObject(asset, dg, deform=False)
    for i, cvert in enumerate(char_verts):
        co = cvert.co - offs
        loc, norm, idx, fdist = bvh_asset.find_nearest(co, dist_thresh)
        if idx == None:
            continue

        fdist = max(fdist, epsilon)

        verts = asset_faces[idx].vertices
        dists = [max((co-asset_verts[vi].co).length, epsilon) for vi in verts]
        if min(dists)*0.9999<=fdist:
            continue

        for vi, dist in zip(verts, dists):
            d = weights[vi]
            d[i] = d.get(i, 0) + 1/(dist*fdist)

    t3 = time.monotonic()
    print("bvh reverse: {}".format(t3-t2))

    for i, d in enumerate(weights):
        #fnorm = sum((char_verts[vi].normal*v for vi, v in d.items()), mathutils.Vector())
        #fnorm.normalize()
        total = sum(d.values())
        #coeff = sum((fnorm.dot(char_verts[vi].normal) ** 2) * v  for vi, v in d.items())/total
        #total *= coeff
        weights[i] = [(k, v/total) for k, v in d.items()]

    t4 = time.monotonic()
    print("normalize: {}".format(t4-t3))

    char_shapekey = char.shape_key_add(from_mix=True)
    char_data = char_shapekey.data
    t3 = time.monotonic()
    if asset.data.shape_keys:
        asset_fitkey = asset.data.shape_keys.key_blocks["charmorph_fitting"]
    else:
        asset.shape_key_add(name="Basis", from_mix=False)
        asset_fitkey = asset.shape_key_add(name="charmorph_fitting", from_mix=False)
    asset_data = asset_fitkey.data
    for i, avert in enumerate(asset.data.vertices):
        asset_data[i].co = avert.co + sum(((char_data[vi].co-char_verts[vi].co)*weight for vi, weight in weights[i]),mathutils.Vector())

    char.shape_key_remove(char_shapekey)
    asset_fitkey.value = 1
    print("fit: {}".format(time.monotonic()-t4))

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