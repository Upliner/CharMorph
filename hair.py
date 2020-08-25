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

import logging, random, numpy
import bpy, mathutils

from . import library, fitting

logger = logging.getLogger(__name__)

obj_cache = {}

def get_hairstyles(ui, context):
    char = fitting.get_char()
    if not char:
        return [("","<None>","")]
    result = [("default","Default hair","")]
    char_conf = library.obj_char(char)
    result.extend([(name, name, "") for name in char_conf.config.get("hair_styles", [])])
    return result

def create_hair_material(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes=True
    tree = mat.node_tree
    tree.nodes.clear()
    output_node = tree.nodes.new("ShaderNodeOutputMaterial")
    hair_node = tree.nodes.new("ShaderNodeBsdfHairPrincipled")
    tree.links.new(hair_node.outputs[0],output_node.inputs[0])
    mat.diffuse_color = (0.01,0.01,0.01,1)
    return mat

def get_material_slot(obj,name):
    mats = obj.data.materials
    for i, mtl in enumerate(mats):
        if mtl.name == name or mtl.name.startswith(name+"."):
            return i
    mats.append(create_hair_material("hair_default"))
    return len(mats)-1

def create_default_hair(context, obj, char):
    l1 = ""
    if hasattr(context.scene,"chartype"):
        l1 = context.scene.chartype
    vg = None
    if "hair_" + l1 in obj.vertex_groups:
        vg = "hair_" + l1
    elif "hair" in obj.vertex_groups:
        vg = "hair"
    override = context.copy()
    override["object"] = obj
    override["active_object"] = obj
    hair = obj.modifiers.new("hair_default", 'PARTICLE_SYSTEM').particle_system

    s = hair.settings
    s.hair_length = char.config.get("default_hair_length", 0.1)
    s.type = 'HAIR'
    s.child_type = 'INTERPOLATED'
    s.create_long_hair_children = True
    s.root_radius = 0.01
    s.material = get_material_slot(obj, "hair_default")
    if vg:
        hair.vertex_group_density = vg
        hair.vertex_group_length = vg
    return s


def calc_weights(char, arr):
    t = fitting.Timer()

    char_verts = char.data.vertices
    char_faces = char.data.polygons

    mat = char.matrix_world.inverted()
    # calculate weights based on 16 nearest vertices
    kd_char = fitting.kdtree_from_verts(char_verts)
    weights = [[{ idx: dist**2 for loc, idx, dist in kd_char.find_n(mat @ key.co_local, 16) } for key in keys ] for keys in arr]

    t.time("hair_kdtree")

    bvh_char = mathutils.bvhtree.BVHTree.FromPolygons([v.co for v in char_verts], [f.vertices for f in char_faces])
    for i, keys in enumerate(arr):
        for j, k in enumerate(keys):
            co = mat @ k.co_local
            loc, norm, idx, fdist = bvh_char.find_nearest(co)

            fdist = max(fdist, fitting.epsilon)

            if not loc or ((co-loc).dot(norm)<=0 and fdist > fitting.dist_thresh):
                continue

            d = weights[i][j]
            for vi in char_faces[idx].vertices:
                d[vi] = d.get(vi,0) + 1/max(((co-char_verts[vi].co).length * fdist), fitting.epsilon)

    t.time("hair_bvh")

    for arr in weights:
        for i, d in enumerate(arr):
            thresh = max(d.values())/16
            d = [(k,v) for k, v in d.items() if v>thresh ] # prune small weights
            total = sum(w[1] for w in d)
            arr[i] = [(k, v/total) for k, v in d]

    return weights

def invalidate_cache():
    obj_cache.clear()

def get_weights(char, psys, new):
    if "charmorph_fit_id" not in psys and new:
        psys["charmorph_fit_id"] = "{:016x}".format(random.getrandbits(64))

    id = psys.get("charmorph_fit_id")
    weights = obj_cache.get(id)
    if weights:
        return weights
    if not new:
        return None

    weights = calc_weights(char, psys)
    obj_cache[id] = weights
    return weights

def fit_hair(char, morphed_shapekey, new=False):
    override = bpy.context.copy()
    override["object"] = char
    bpy.ops.particle.disconnect_hair(override, all=True)
    for psys in char.particle_systems:
        weights = get_weights(char, psys, new)
        #TODO the rest
    bpy.ops.particle.connect_hair(override, all=True)

def fit_new_hair(char):
    morphed_shapekey = char.shape_key_add(from_mix=True)
    fit_hair(char, morphed_shapekey, True)
    char.shape_key_remove(morphed_shapekey)

class OpRefitHair(bpy.types.Operator):
    bl_idname = "charmorph.hair_refit"
    bl_label = "Refit hair"
    bl_description = "Refit hair to match changed character geometry (discards any manual grooming!)"
    bl_options = {"UNDO"}
    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and fitting.get_char()

    def execute(self, context):
        fit_new_hair(fitting.get_char())

class OpCreateHair(bpy.types.Operator):
    bl_idname = "charmorph.hair_create"
    bl_label = "Create hair"
    bl_description = "Create hair"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and fitting.get_char()
    def execute(self, context):
        ui = context.scene.charmorph_ui
        style = ui.hair_style
        char = fitting.get_char()
        char_conf = library.obj_char(char)
        if style=="default":
            create_default_hair(context, char, char_conf)
            return {"FINISHED"}
        obj = library.import_obj(library.char_file(char_conf.name, char_conf.config.get("hair_library","hair.blend")),"hair",link=False)
        if not obj:
            self.report({"ERROR"}, "Failed to import hair")
            return {"CANCELLED"}
        override = context.copy()
        override["object"] = obj
        src_psys = obj.particle_systems[ui.hair_style]
        override["particle_system"] = src_psys
        override["selected_editable_objects"] = [char]
        #bpy.ops.particle.disconnect_hair(override)
        #bpy.ops.particle.connect_hair(override)
        bpy.ops.particle.copy_particle_systems(override)
        dst_psys = char.particle_systems[len(char.particle_systems)-1]
        for attr in dir(src_psys):
            if not attr.startswith("vertex_group_"):
                continue
            val = getattr(src_psys, attr)
            if val:
                if not val in char.vertex_groups:
                    val = ""
                setattr(dst_psys, attr, val)
        #override["object"] = char
        #override["particle_system"] = dst_psys
        #bpy.ops.particle.disconnect_hair(override)
        #bpy.ops.particle.particle_edit_toggle(override)
        #for sp, dp in zip(src_psys.particles,dst_psys.particles):
        #   for sk, dk in zip(sp.hair_keys, dp.hair_keys):
        #        sk.co = dk.co
        #bpy.ops.particle.connect_hair(override)
        bpy.data.objects.remove(obj)
        psys["charmorph_hairstyle"] = style

        return {"FINISHED"}

classes = [OpCreateHair]
