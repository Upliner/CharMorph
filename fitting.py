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

import time, random
import bpy, mathutils

dist_thresh = 0.1
epsilon = 1e-30

obj_cache = {}
char_cache = {}

# As realtime fitting is performance-critical I use timers for performance debugging
class Timer:
    def __init__(self):
        self.t = time.monotonic()
    def time(self, name):
        t2 = time.monotonic()
        print("{}: {}".format(name, t2-self.t))
        self.t = t2

def kdtree_from_verts(verts):
    kd = mathutils.kdtree.KDTree(len(verts))
    for idx, vert in enumerate(verts):
        kd.insert(vert.co, idx)
    kd.balance()
    return kd

def neighbor_map(mesh):
    result = [[] for _ in range(len(mesh.vertices))]
    for edge in mesh.edges:
        verts = edge.vertices
        result[verts[0]].append(verts[1])
        result[verts[1]].append(verts[0])
    return result

def invalidate_cache():
    global obj_cache, char_cache
    obj_cache = {}
    char_cache = {}
    print("Fitting cache is invalidated")

def calc_weights(char, asset, mask):
    t = Timer()

    # dg = bpy.context.view_layer.depsgraph

    char_verts = char.data.vertices
    char_faces = char.data.polygons
    asset_verts = asset.data.vertices
    asset_faces = asset.data.polygons

    offs = asset.location

    # calculate weights based on 16 nearest vertices
    kd_char = kdtree_from_verts(char_verts)
    weights = [{ idx: dist**2 for loc, idx, dist in kd_char.find_n(avert.co + offs, 16) } for avert in asset_verts]

    t.time("kdtree")

    # using FromPolygons because objects can have modifiers and there is no way force FromObject to use undeformed mesh
    # calculate weights based on distance from asset vertices to character faces
    bvh_char = mathutils.bvhtree.BVHTree.FromPolygons([v.co for v in char_verts], [f.vertices for f in char_faces])
    for i, avert in enumerate(asset_verts):
        co = avert.co + offs
        loc, norm, idx, fdist = bvh_char.find_nearest(co)

        fdist = max(fdist, epsilon)

        if not loc or ((co-loc).dot(norm)<=0 and fdist > dist_thresh):
            continue

        d = weights[i]
        for vi in char_faces[idx].vertices:
            d[vi] = d.get(vi,0) + 1/((co-char_verts[vi].co).length * fdist)

    t.time("bvh direct")

    # calculate weights based on distance from character vertices to assset faces
    bvh_asset = mathutils.bvhtree.BVHTree.FromPolygons([v.co for v in asset_verts], [f.vertices for f in asset_faces])
    #bvh_asset = mathutils.bvhtree.BVHTree.FromObject(asset, dg)
    for i, cvert in enumerate(char_verts):
        co = cvert.co - offs
        loc, norm, idx, fdist = bvh_asset.find_nearest(co, dist_thresh)
        if idx is None:
            continue

        fdist = max(fdist, epsilon)

        verts = asset_faces[idx].vertices
        dists = [max((co-asset_verts[vi].co).length, epsilon) for vi in verts]
        if min(dists)*0.9999<=fdist:
            continue

        for vi, dist in zip(verts, dists):
            d = weights[vi]
            d[i] = d.get(i, 0) + 1/(dist*fdist)

    t.time("bvh reverse")

    if mask:
        add_mask(char, asset, bvh_asset, bvh_char, offs)
        t.time("mask")

    #neighbors = neighbor_map(asset.data)

    #for i, d in enumerate(weights):
    #    for ni in neighbors[i]:
    #        for k, v in weights[ni].items():
    #            d[k] = d.get(k, 0) + v

    #timer("smooth")

    for i, d in enumerate(weights):
        thresh = max(d.values())/16
        d = [(k,v) for k, v in d.items() if v>thresh ] # prune small weights
        #fnorm = sum((char_verts[vi].normal*v for vi, v in d), mathutils.Vector())
        #fnorm.normalize()
        total = sum(w[1] for w in d)
        #coeff = (sum(fnorm.dot(char_verts[vi].normal) * v  for vi, v in d)/total)
        #total *= coeff
        weights[i] = [(k, v/total) for k, v in d]

    t.time("normalize")

    return weights

def mask_name(asset):
    return "cm_mask_{}_{}".format(asset.name, asset.data.get("charmorph_fit_id","xxx")[:3])

def add_mask(char, asset, bvh_asset, bvh_char, offs):
    vg_name = mask_name(asset)
    if vg_name in char.vertex_groups:
        return
    bbox_min = mathutils.Vector(asset.bound_box[0])
    bbox_max = mathutils.Vector(asset.bound_box[0])
    for v in asset.bound_box:
        for i in range(3):
            bbox_min[i] = min(bbox_min[i], v[i])
            bbox_max[i] = max(bbox_max[i], v[i])
    def bbox_match(co):
        for i in range(3):
            if co[i]<bbox_min[i] or co[i]>bbox_max[i]:
                return False
        return True

    bbox_center = (bbox_min+bbox_max)/2

    cube_size = max(abs(v[coord]-bbox_center[coord]) for v in [bbox_min, bbox_max] for coord in range(3))
    cube_vector = mathutils.Vector([cube_size] * 3)

    bcube_min = bbox_center-cube_vector
    bcube_max = bbox_center+cube_vector

    bbox_points = [bcube_min, bbox_center, bcube_max]
    cast_points = [ mathutils.Vector((bbox_points[x][0],bbox_points[y][1],bbox_points[z][2])) for x in range(3) for y in range(3) for z in range(3) if x!=1 or y != 1 or z != 1 ]

    def cast_rays(co, direction, max_dist=1e30):
        nonlocal has_cloth
        _, _, idx, _ = bvh_asset.ray_cast(co, direction, max_dist)
        if idx is None:
            # Vertex is not blocked by cloth. Maybe blocked by the body itself?
            _, _, idx, _ = bvh_char.ray_cast(co+offs+direction*0.00001, direction, max_dist*0.99)
            if idx is None:
                #print(i, co, direction, max_dist, cvert.normal)
                return False # No ray hit
        else:
            has_cloth = True
        return True # Have ray hit


    covered_verts = set()

    for i, cvert in enumerate(char.data.vertices):
        co = cvert.co - offs
        if not bbox_match(co):
            continue

        has_cloth = False
        norm = cvert.normal

        #if vertex is too close to cloth, mark it as covered
        _, _, idx, _ = bvh_asset.find_nearest(co, 0.0005)
        if idx is not None:
            #print(i, co, fhit, fdist, "too close")
            covered_verts.add(i)

        # cast one ray along vertex normal and check is there a clothing nearby
        if not cast_rays(co, norm):
            continue

        # cast rays out of 26 outside points to check whether the vertex is visible from any feasible angle
        for cast_point in cast_points:
            direction = co-cast_point
            max_dist = direction.length
            direction.normalize()
            if cvert.normal.dot(direction)>-0.5:
                continue # skip back faces and very sharp view angles
            if not cast_rays(cast_point, direction, max_dist):
                has_cloth = False
                break

        if has_cloth:
            covered_verts.add(i)

    #vg = char.vertex_groups.new(name = "covered")
    #vg.add(list(covered_verts), 1, 'REPLACE')

    boundary_verts = set()
    for f in char.data.polygons:
        for i in f.vertices:
            if i not in covered_verts:
                boundary_verts.update(f.vertices)

    covered_verts.difference_update(boundary_verts)

    if covered_verts:
        vg = char.vertex_groups.new(name = vg_name)
        vg.add(list(covered_verts), 1, 'REPLACE')
        mod = char.modifiers.new(vg_name, "MASK")
        mod.invert_vertex_group = True
        mod.vertex_group = vg.name

def get_obj_weights(char, asset, mask = False):
    if "charmorph_fit_id" not in asset.data:
        asset.data["charmorph_fit_id"] = "{:016x}".format(random.getrandbits(64))

    id = asset.data["charmorph_fit_id"]
    weights = obj_cache.get(id)
    if weights:
        return weights

    weights = calc_weights(char, asset, mask)
    obj_cache[id] = weights
    return weights

def get_weight(mesh, vg, idx):
    try: #TODO: new vertex group handling
        return vg.weight(idx)
    except RuntimeError:
        return 0

def transfer_weights(char, asset):
    t = Timer()
    weights = get_obj_weights(char, asset)

    for vg_src in char.vertex_groups:
        if not vg_src.name.startswith("DEF-") or vg_src.name in asset.vertex_groups:
            continue
        vg_dst = None
        for i, subweights in enumerate(weights):
            weight = sum(get_weight(char.data, vg_src,vi)*subweight for vi, subweight in subweights)
            if weight>0:
                if vg_dst is None:
                    vg_dst = asset.vertex_groups.new(name = vg_src.name)
                vg_dst.add([i], weight, type='REPLACE')

    t.time("weights")

def do_fit(char, assets):
    t = Timer()

    char_basis = char.data.vertices
    char_shapekey = char.shape_key_add(from_mix=True) # Creating mixed shape key every time causes some minor UI glitches. Any better idea?
    char_morphed = char_shapekey.data

    for asset in assets:
        weights = get_obj_weights(char, asset)
        if not asset.data.shape_keys or not asset.data.shape_keys.key_blocks:
            asset.shape_key_add(name="Basis", from_mix=False)
        if "charmorph_fitting" in asset.data.shape_keys.key_blocks:
            asset_fitkey = asset.data.shape_keys.key_blocks["charmorph_fitting"]
        else:
            asset_fitkey = asset.shape_key_add(name="charmorph_fitting", from_mix=False)

        asset_morphed = asset_fitkey.data
        for i, avert in enumerate(asset.data.vertices):
            asset_morphed[i].co = avert.co + sum(((char_morphed[vi].co-char_basis[vi].co)*weight for vi, weight in weights[i]), mathutils.Vector())

        asset_fitkey.value = max(asset_fitkey.value, 1)

    char.shape_key_remove(char_shapekey)
    t.time("fit")

def refit_char_assets(char):
    if char.name in char_cache:
        children = char_cache[char.name]
    else:
        children = [ obj.name for obj in bpy.data.objects if obj.type=="MESH" and obj.parent == char  and 'charmorph_fit_id' in obj.data]
        char_cache[char.name] = children

    assets = [ asset for asset in (bpy.data.objects[name] for name in children) if asset.type=="MESH" and 'charmorph_fit_id' in asset.data ]
    if assets:
        do_fit(char, assets)

dummy_dict = {"data":{}}

class CHARMORPH_PT_Fitting(bpy.types.Panel):
    bl_label = "Fitting"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 7

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def draw(self, context):
        ui = context.scene.charmorph_ui
        self.layout.prop(ui,"fitting_char")
        self.layout.prop(ui,"fitting_asset")
        self.layout.prop(ui,"fitting_mask")
        self.layout.prop(ui,"fitting_weights")
        self.layout.separator()
        if 'charmorph_fit_id' in bpy.data.objects.get(ui.fitting_asset, dummy_dict).data:
            self.layout.operator("charmorph.unfit")
        else:
            self.layout.operator("charmorph.fit")

class OpFit(bpy.types.Operator):
    bl_idname = "charmorph.fit"
    bl_label = "Fit"
    bl_options = {"UNDO"}


    @classmethod
    def poll(cls, context):
        ui = context.scene.charmorph_ui
        char = bpy.data.objects.get(ui.fitting_char)
        asset = bpy.data.objects.get(ui.fitting_asset)
        if not char or char.type != 'MESH' or not asset or asset.type != 'MESH':
            return False
        return (
            context.mode == "OBJECT" and
            ui.fitting_char != '' and
            ui.fitting_asset != '' and
            ui.fitting_char != ui.fitting_asset and
            'charmorph_fit_id' not in char.data and
            'charmorph_fit_id' not in asset.data
        )

    def execute(self, context):
        global char_cache
        char_cache = {}
        ui = context.scene.charmorph_ui
        char = bpy.data.objects[ui.fitting_char]
        asset = bpy.data.objects[ui.fitting_asset]
        if ui.fitting_mask != "NONE":
            # TODO: implement COMB masking
            get_obj_weights(char, asset, True)
        transfer_weights(char, asset)
        do_fit(char, [asset])
        asset.parent = char
        return {"FINISHED"}

class OpUnfit(bpy.types.Operator):
    bl_idname = "charmorph.unfit"
    bl_label = "Unfit"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        ui = context.scene.charmorph_ui
        asset = bpy.data.objects.get(ui.fitting_asset)
        if not asset or asset.type != 'MESH':
            return False
        return (
            context.mode == "OBJECT" and
            ui.fitting_asset != '' and
            'charmorph_fit_id' in asset.data
        )

    def execute(self, context):
        global char_cache
        char_cache = {}
        ui = context.scene.charmorph_ui
        asset_name = ui.fitting_asset
        asset = bpy.data.objects[asset_name]

        mask = mask_name(asset)
        for char in [asset.parent, bpy.data.objects.get(ui.fitting_char)]:
            if not char or char == asset or 'charmorph_fit_id' in char.data:
                continue
            found = False
            if mask in char.modifiers:
                char.modifiers.remove(char.modifiers[mask])
                found = True
            if mask in char.vertex_groups:
                char.vertex_groups.remove(char.vertex_groups[mask])
                found = True
            if found:
                break
        if asset.parent:
            asset.parent = asset.parent.parent
        if asset.data.shape_keys and "charmorph_fitting" in asset.data.shape_keys.key_blocks:
            asset.shape_key_remove(asset.data.shape_keys.key_blocks["charmorph_fitting"])
        del asset.data['charmorph_fit_id']
        return {"FINISHED"}

classes = [OpFit, OpUnfit, CHARMORPH_PT_Fitting]
