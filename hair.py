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
from .materials import parse_color

logger = logging.getLogger(__name__)

obj_cache = {}

def get_hairstyles(ui, context):
    char = fitting.get_char()
    if not char:
        return [("","<None>","")]
    result = [("default","Default hair","")]
    char_conf = library.obj_char(char)
    result.extend([(name, name, "") for name in char_conf.config.get("hair_styles", {}).keys()])
    return result

def create_hair_material(context, name):
    mat = bpy.data.materials.new(name)
    apply_hair_color(context, mat)
    return mat

def apply_hair_color(context, mat):
    if not mat:
        return
    mat.use_nodes=True
    tree = mat.node_tree
    tree.nodes.clear()
    output_node = tree.nodes.new("ShaderNodeOutputMaterial")
    hair_node = tree.nodes.new("ShaderNodeBsdfHairPrincipled")
    tree.links.new(hair_node.outputs[0],output_node.inputs[0])
    settings = library.hair_colors.get(context.scene.charmorph_ui.hair_color)
    if settings and settings["type"] == "ShaderNodeBsdfHairPrincipled":
        hair_node.parametrization = settings.get("parametrization", "MELANIN")
        hair_node.inputs[0].default_value = parse_color(settings.get("color", [0,0,0]))
        hair_node.inputs[1].default_value = settings.get("melanin", 0)
        hair_node.inputs[2].default_value = settings.get("melanin_redness", 0)
        hair_node.inputs[3].default_value = parse_color(settings.get("tint", [1,1,1]))
        hair_node.inputs[4].default_value = settings.get("absorption_coeff", [0,0,0])
        hair_node.inputs[5].default_value = settings.get("roughness", 0)
        hair_node.inputs[6].default_value = settings.get("radial_roughness", 0)
        hair_node.inputs[7].default_value = settings.get("coat", 0)
        hair_node.inputs[8].default_value = settings.get("ior", 1)
        hair_node.inputs[9].default_value = settings.get("offset", 0)
        hair_node.inputs[10].default_value = settings.get("random_color", 0)
        hair_node.inputs[11].default_value = settings.get("random_roughness", 0)
        mat.diffuse_color = parse_color(settings.get("viewport_color", [0.01,0.01,0.01]))
    else:
        mat.diffuse_color = (0.01,0.01,0.01,1)

def get_material_slot(context, obj, name):
    mats = obj.data.materials
    for i, mtl in enumerate(mats):
        if mtl.name == name or mtl.name.startswith(name+"."):
            return i + 1
    mats.append(create_hair_material(context, name))
    return len(mats)

def attach_scalp(char, obj):
    obj.data["charmorph_fit_mask"] = "false"
    obj.show_instancer_for_viewport = False
    obj.show_instancer_for_render = False
    collections = char.users_collection
    active_collection = bpy.context.collection
    for c in collections:
        if c is active_collection:
            c.objects.link(obj)
            break
    else:
        for c in collections:
            c.objects.link(obj)
    fitting.fit_new(char, obj)

def create_scalp(name, char, vgi):
    vmap = {}
    verts = []
    for v in char.data.vertices:
        for g in v.groups:
            if g.group == vgi:
                vmap[v.index] = len(verts)
                verts.append(v.co)
    edges = [(v1, v2) for v1, v2 in ((vmap.get(e.vertices[0]), vmap.get(e.vertices[1])) for e in char.data.edges) if v1 is not None and v2 is not None]
    faces = []
    for f in char.data.polygons:
        face = []
        for v in f.vertices:
            i = vmap.get(v)
            if i is None:
                break
            face.append(i)
        else:
            faces.append(face)

    m = bpy.data.meshes.new(name)
    m.from_pydata(verts,edges,faces)
    obj = bpy.data.objects.new(name, m)
    attach_scalp(char, obj)
    return obj

def create_default_hair(context, obj, char, scalp):
    l1 = ""
    if hasattr(context.scene,"chartype"):
        l1 = context.scene.chartype
    vg = None
    if "hair_" + l1 in obj.vertex_groups:
        vg = "hair_" + l1
    elif "hair" in obj.vertex_groups:
        vg = "hair"

    if scalp and vg:
        obj = create_scalp("hair_default", obj, obj.vertex_groups[vg].index)

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
    s.material = get_material_slot(context, obj, "hair_default")
    if vg:
        hair.vertex_group_density = vg
        hair.vertex_group_length = vg
    return s


def calc_weights(char, arr):
    t = fitting.Timer()

    char_verts = char.data.vertices
    char_faces = char.data.polygons

    # calculate weights based on n nearest vertices
    kd_char = fitting.kdtree_from_verts(char_verts)
    weights = [[{ idx: dist**2 for loc, idx, dist in kd_char.find_n(co, 32) } for co in keys ] for keys in arr]

    t.time("hair_kdtree")

    bvh_char = mathutils.bvhtree.BVHTree.FromPolygons([v.co for v in char_verts], [f.vertices for f in char_faces])
    for i, keys in enumerate(arr):
        for j, co in enumerate(keys):
            loc, norm, idx, fdist = bvh_char.find_nearest(co)

            fdist = max(fdist, fitting.epsilon)

            if not loc or ((co-loc).dot(norm)<=0 and fdist > fitting.dist_thresh):
                continue

            d = weights[i][j]
            for vi in char_faces[idx].vertices:
                d[vi] = d.get(vi,0) + 1/max(((mathutils.Vector(co)-char_verts[vi].co).length * fdist), fitting.epsilon)

    t.time("hair_bvh")

    for arr in weights:
        for i, d in enumerate(arr):
            thresh = max(d.values())/16
            d = [(k,v) for k, v in d.items() if v>thresh ] # prune small weights
            total = sum(w[1] for w in d)
            arr[i] = [(k, v/total) for k, v in d]
    t.time("hair_normalize")

    return weights

def invalidate_cache():
    obj_cache.clear()

def get_data(char, psys, new):
    if not psys.is_edited:
        return False
    if "charmorph_fit_id" not in psys.settings and new:
        psys.settings["charmorph_fit_id"] = "{:016x}".format(random.getrandbits(64))

    id = psys.settings.get("charmorph_fit_id")
    data = obj_cache.get(id)
    if data:
        return data

    if not new:
        return None, None

    char_conf = library.obj_char(char)
    style = psys.settings.get("charmorph_hairstyle")
    if not char_conf or not style:
        return None, None

    try:
        arr = numpy.load(library.char_file(char_conf.name, "hair_styles/%s.npy" % style), allow_pickle=True)
    except Exception as e:
        logger.error(str(e))
        return None, None

    if len(arr) != len(psys.particles):
        logger.error("Mismatch between current hairsyle and .npy!")
        invalidate_cache()
        return None, None

    weights = calc_weights(char, arr)
    obj_cache[id] = (arr, weights)
    return arr, weights

def fit_all_hair(context, char, diff_arr, new):
    t = fitting.Timer()
    for psys in char.particle_systems:
        fit_hair(context, char, char, psys, diff_arr, new)

    for asset in fitting.get_assets(char):
        for psys in asset.particle_systems:
            fit_hair(context, char, asset, psys, diff_arr, new)

    t.time("hair_fit")

def has_hair(char):
    for psys in char.particle_systems:
        arr, weights = get_data(char, psys, False)
        if arr is not None and weights:
           return True
    return False

def fit_hair(context, char, obj, psys, diff_arr, new):
    arr, weights = get_data(char, psys, new)
    if arr is None or not weights:
        return False

    mat = obj.matrix_world
    override = context.copy()
    override["object"] = obj
    override["particle_system"] = psys
    have_mismatch = False
    bpy.ops.particle.disconnect_hair(override)
    try:
        for p, keys, pweights in zip(psys.particles, arr, weights):
            if len(p.hair_keys)-1 != len(keys):
                if not have_mismatch:
                    logger.error("Particle mismatch")
                    have_mismatch = True
                continue
            for kdst, ksrc, weightsd in zip(p.hair_keys[1:], keys, pweights):
                vsrc = mathutils.Vector(ksrc)
                kdst.co_local = mat @ (vsrc + sum((diff_arr[vi]*weight for vi, weight in weightsd), mathutils.Vector()))
    except Exception as e:
        logger.error(str(e))
        invalidate_cache()
    bpy.ops.particle.connect_hair(override)
    return True

class OpRefitHair(bpy.types.Operator):
    bl_idname = "charmorph.hair_refit"
    bl_label = "Refit hair"
    bl_description = "Refit hair to match changed character geometry (discards manual combing, won't work if you added/removed particles)"
    bl_options = {"UNDO"}
    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and fitting.get_char()

    def execute(self, context):
        char = fitting.get_char()
        fit_all_hair(context, char, fitting.diff_array(char), True)
        return {"FINISHED"}

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
            create_default_hair(context, char, char_conf, ui.hair_scalp)
            return {"FINISHED"}
        obj_name = char_conf.config.get("hair_styles", {}).get(style)
        if not obj_name:
            self.report({"ERROR"}, "Hairstyle is not found")
            return {"CANCELLED"}
        obj = library.import_obj(library.char_file(char_conf.name, char_conf.config.get("hair_library","hair.blend")),obj_name,link=False)
        if not obj:
            self.report({"ERROR"}, "Failed to import hair")
            return {"CANCELLED"}
        override = context.copy()
        override["object"] = obj
        src_psys = obj.particle_systems[ui.hair_style]
        override["particle_system"] = src_psys
        if ui.hair_scalp:
            dst_obj = bpy.data.objects.new("hair_" + style, obj.data)
            attach_scalp(char, dst_obj)
        else:
            dst_obj = char
        override["selected_editable_objects"] = [dst_obj]
        bpy.ops.particle.copy_particle_systems(override)
        dst_psys = dst_obj.particle_systems[len(char.particle_systems)-1]
        for attr in dir(src_psys):
            if not attr.startswith("vertex_group_"):
                continue
            val = getattr(src_psys, attr)
            if val:
                if not val in dst_obj.vertex_groups:
                    val = ""
                setattr(dst_psys, attr, val)
        bpy.data.objects.remove(obj)
        s = dst_psys.settings
        s["charmorph_hairstyle"] = style
        s.material = get_material_slot(context, dst_obj, "hair_" + style)
        fit_hair(context, char, dst_obj, dst_psys, fitting.diff_array(char), True)

        context.view_layer.objects.active = dst_obj

        return {"FINISHED"}

class OpRecolorHair(bpy.types.Operator):
    bl_idname = "charmorph.hair_recolor"
    bl_label = "Change hair color"
    bl_description = "Change hair color to selected one"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.particle_systems.active
    def execute(self, context):
        obj = context.object
        s = obj.particle_systems.active.settings
        slot = s.material
        if slot >= 0 and slot <= len(obj.data.materials):
            apply_hair_color(context, obj.data.materials[slot-1])
        else:
            s.material = get_material_slot(context, obj, "hair")
        return {"FINISHED"}

class CHARMORPH_PT_Hair(bpy.types.Panel):
    bl_label = "Hair"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 8

    def draw(self, context):
        ui = context.scene.charmorph_ui
        self.layout.prop(ui, "hair_scalp")
        self.layout.prop(ui, "hair_deform")
        self.layout.prop(ui, "hair_color")
        self.layout.prop(ui, "hair_style")
        self.layout.operator("charmorph.hair_create")
        self.layout.operator("charmorph.hair_refit")
        self.layout.operator("charmorph.hair_recolor")

classes = [OpCreateHair, OpRefitHair, OpRecolorHair, CHARMORPH_PT_Hair]