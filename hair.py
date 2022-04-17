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

import logging, random
import bpy, bmesh # pylint: disable=import-error

from .lib import charlib, utils
from . import assets, morphing

logger = logging.getLogger(__name__)

def create_hair_material(name, hair_color):
    mat = bpy.data.materials.new(name)
    apply_hair_color(mat, hair_color)
    return mat

def apply_hair_color(mat, hair_color):
    if not mat:
        return
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    output_node = tree.nodes.new("ShaderNodeOutputMaterial")
    hair_node = tree.nodes.new("ShaderNodeBsdfHairPrincipled")
    tree.links.new(hair_node.outputs[0], output_node.inputs[0])
    settings = charlib.hair_colors.get(hair_color)
    if settings and settings["type"] == "ShaderNodeBsdfHairPrincipled":
        hair_node.parametrization = settings.get("parametrization", "MELANIN")
        hair_node.inputs[0].default_value = utils.parse_color(settings.get("color", [0, 0, 0]))
        hair_node.inputs[1].default_value = settings.get("melanin", 0)
        hair_node.inputs[2].default_value = settings.get("melanin_redness", 0)
        hair_node.inputs[3].default_value = utils.parse_color(settings.get("tint", [1, 1, 1]))
        hair_node.inputs[4].default_value = settings.get("absorption_coeff", [0, 0, 0])
        hair_node.inputs[5].default_value = settings.get("roughness", 0)
        hair_node.inputs[6].default_value = settings.get("radial_roughness", 0)
        hair_node.inputs[7].default_value = settings.get("coat", 0)
        hair_node.inputs[8].default_value = settings.get("ior", 1)
        hair_node.inputs[9].default_value = settings.get("offset", 0)
        hair_node.inputs[10].default_value = settings.get("random_color", 0)
        hair_node.inputs[11].default_value = settings.get("random_roughness", 0)
        mat.diffuse_color = utils.parse_color(settings.get("viewport_color", [0.01, 0.01, 0.01]))
    else:
        mat.diffuse_color = (0.01, 0.01, 0.01, 1)

def get_material_slot(obj, name, hair_color):
    mats = obj.data.materials
    for i, mtl in enumerate(mats):
        if mtl.name == name or mtl.name.startswith(name+"."):
            return i + 1
    mats.append(create_hair_material(name, hair_color))
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
    assets.get_fitter(char).fit_new(obj)

def create_scalp(name, char, vgi):
    vmap = {}
    verts = []
    for mv, bv in zip(char.data.vertices, morphing.get_basis(char)):
        for g in mv.groups:
            if g.group == vgi:
                vmap[mv.index] = len(verts)
                verts.append(bv)
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
    m.from_pydata(verts, edges, faces)
    obj = bpy.data.objects.new(name, m)
    attach_scalp(char, obj)
    return obj

def create_default_hair(context, obj, char, scalp):
    l1 = ""
    wm = context.window_manager
    if hasattr(wm, "chartype"):
        l1 = wm.chartype
    vg = None
    if "hair_" + l1 in obj.vertex_groups:
        vg = "hair_" + l1
    elif "hair" in obj.vertex_groups:
        vg = "hair"
    else:
        for g in obj.vertex_groups:
            if g.name.startswith("hair_"):
                vg = g.name
                break

    if scalp and vg:
        obj = create_scalp("hair_default", obj, obj.vertex_groups[vg].index)

    hair = obj.modifiers.new("hair_default", 'PARTICLE_SYSTEM').particle_system

    s = hair.settings
    s.hair_length = char.default_hair_length
    s.type = 'HAIR'
    s.child_type = 'INTERPOLATED'
    s.create_long_hair_children = True
    s.root_radius = 0.01
    s.material = get_material_slot(obj, "hair_default", wm.charmorph_ui.hair_color)
    if vg:
        hair.vertex_group_density = vg
        hair.vertex_group_length = vg
    return s

def fit_all_hair(char):
    t = utils.Timer()
    fitter = assets.get_fitter(char)
    has_fit = False
    has_fit |= fitter.fit_obj_hair(char)
    for asset in fitter.get_assets():
        has_fit |= fitter.fit_obj_hair(asset)
    t.time("hair_fit")
    return has_fit

def make_scalp(obj, name):
    vg = obj.vertex_groups.get("scalp_" + name)
    if not vg:
        vg = obj.vertex_groups.get("scalp")
    if not vg:
        #logger.error("Scalp vertex group is not found! Using full object as scalp mesh")
        return
    vgi = vg.index
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        d = bm.verts.layers.deform.active
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if vgi not in v[d]])
        bm.to_mesh(obj.data)
    finally:
        bm.free()

class OpRefitHair(bpy.types.Operator):
    bl_idname = "charmorph.hair_refit"
    bl_label = "Refit hair"
    bl_description = "Refit hair to match changed character geometry (discards manual combing, won't work if you added/removed particles)"
    bl_options = {"UNDO"}
    @classmethod
    def poll(cls, context):
        obj = context.object
        if not obj:
            return False
        return context.mode in ["OBJECT", "POSE"] and obj.type in ["MESH", "ARMATURE"]

    def execute(self, context):
        obj = context.object
        if obj.type == "ARMATURE":
            children = obj.children
            if len(children) == 1:
                obj = children[0]
        if obj.type != "MESH":
            self.report({"ERROR"}, "Character is not found")
            return {"CANCELLED"}
        if "charmorph_fit_id" in obj.data and obj.parent and obj.parent.type == "MESH":
            has_fit = assets.get_fitter(obj.parent).fit_obj_hair(obj)
        else:
            has_fit = fit_all_hair(obj)
        if not has_fit:
            self.report({"ERROR"}, "No hair fitting data found")
            return {"CANCELLED"}
        return {"FINISHED"}

class OpCreateHair(bpy.types.Operator):
    bl_idname = "charmorph.hair_create"
    bl_label = "Create hair"
    bl_description = "Create hair"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and context.object and context.object.type == "MESH"
    def execute(self, context):
        ui = context.window_manager.charmorph_ui
        style = ui.hair_style
        char = context.object
        char_conf = charlib.obj_char(char)
        if style == "default":
            create_default_hair(context, char, char_conf, ui.hair_scalp)
            return {"FINISHED"}
        lib = char_conf.hair_library
        if not lib:
            self.report({"ERROR"}, "Hair library is not found")
            return {"CANCELLED"}

        do_scalp = ui.hair_scalp or char_conf.force_hair_scalp
        do_shrinkwrap = ui.hair_shrinkwrap and char_conf.hair_shrinkwrap

        obj = utils.import_obj(char_conf.path(lib), char_conf.hair_obj, link=do_scalp)
        if not obj:
            self.report({"ERROR"}, "Failed to import hair")
            return {"CANCELLED"}
        override = {"object": obj}
        idx = -1
        src_psys = None
        for idx, src_psys in enumerate(obj.particle_systems):
            if src_psys.name == style:
                break
        else:
            self.report({"ERROR"}, "Hairstyle is not found")
            return {"CANCELLED"}

        fitter = assets.get_fitter(char)
        restore_modifiers = []
        if do_scalp:
            obj.particle_systems.active_index = idx
            bpy.ops.particle.disconnect_hair(override)
            make_scalp(obj, style)
            dst_obj = bpy.data.objects.new(f"{char.name}_hair_{style}", obj.data)
            attach_scalp(char, dst_obj)
        else:
            restore_modifiers = utils.disable_modifiers(char)
            dst_obj = char
            fitter.fit(obj)
            obj.parent = char
        restore_modifiers.extend(utils.disable_modifiers(dst_obj, lambda _: True))
        override["selected_editable_objects"] = [dst_obj]
        override["particle_system"] = src_psys
        bpy.ops.particle.copy_particle_systems(override, remove_target_particles=False, use_active=True)
        dst_psys = dst_obj.particle_systems[len(dst_obj.particle_systems)-1]
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
        s["charmorph_fit_id"] = f"{random.getrandbits(64):016x}"
        s.material = get_material_slot(dst_obj, "hair_" + style, ui.hair_color)

        override["object"] = dst_obj
        cnt = len(dst_obj.modifiers)
        for m in list(dst_obj.modifiers):
            cnt -= 1
            if utils.is_obstructive_modifier(m):
                for _ in range(cnt):
                    if bpy.ops.object.modifier_move_down.poll(override):
                        bpy.ops.object.modifier_move_down(override, modifier=m.name)
                cnt += 1

        if do_scalp:
            bpy.ops.particle.connect_hair(override)

        for m in restore_modifiers:
            m.show_viewport = True

        fitter.fit_hair(dst_obj, len(dst_obj.particle_systems)-1)

        if do_shrinkwrap and dst_obj is not char:
            mod = dst_obj.modifiers.new("charmorph_shrinkwrap", "SHRINKWRAP")
            mod.wrap_method="TARGET_PROJECT"
            mod.target = char
            mod.wrap_mode = "OUTSIDE_SURFACE"
            mod.offset = char_conf.hair_shrinkwrap_offset
            utils.reposition_modifier(dst_obj, 0)

        return {"FINISHED"}

class OpRecolorHair(bpy.types.Operator):
    bl_idname = "charmorph.hair_recolor"
    bl_label = "Change hair color"
    bl_description = "Change hair color to selected one"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.particle_systems.active

    def execute(self, context): # pylint: disable=no-self-use
        obj = context.object
        s = obj.particle_systems.active.settings
        slot = s.material
        if 0 <= slot <= len(obj.data.materials):
            apply_hair_color(obj.data.materials[slot-1], context.window_manager.charmorph_ui.hair_color)
        else:
            s.material = get_material_slot(context, obj, "hair")
        return {"FINISHED"}

def get_hair_colors(_ui, _context):
    return [(k, k, "") for k in charlib.hair_colors.keys()]

def get_hairstyles(_, context):
    char = charlib.obj_char(context.object)
    result = [("default", "Default hair", "")]
    if not char.name:
        return result
    result.extend([(name, name, "") for name in char.hairstyles])
    return result

class UIProps:
    hair_scalp: bpy.props.BoolProperty(
        name="Use scalp mesh",
        description="Use scalp mesh as emitter instead of whole body")
    hair_shrinkwrap: bpy.props.BoolProperty(
        name="Use shrinkwrap",
        description="Use shrinkwrap modifier for scalp mesh",
        default=True)
    hair_deform: bpy.props.BoolProperty(
        name="Live deform",
        description="Refit hair in real time (slower than clothing)")
    hair_color: bpy.props.EnumProperty(
        name="Hair color",
        description="Hair color",
        items=get_hair_colors)
    hair_style: bpy.props.EnumProperty(
        name="Hairstyle",
        description="Hairstyle",
        items=get_hairstyles)

class CHARMORPH_PT_Hair(bpy.types.Panel):
    bl_label = "Hair"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 8

    def draw(self, context):
        ui = context.window_manager.charmorph_ui
        _, char = morphing.get_obj_char(context)
        if not char:
            char = charlib.empty_char
        l = self.layout
        for prop in UIProps.__annotations__: # pylint: disable=no-member
            if (prop == "hair_shrinkwrap" and not char.hair_shrinkwrap) or (
                prop == "hair_scalp" and char.force_hair_scalp):
                continue
            l.prop(ui, prop)
        l.operator("charmorph.hair_create")
        l.operator("charmorph.hair_refit")
        l.operator("charmorph.hair_recolor")

classes = [OpCreateHair, OpRefitHair, OpRecolorHair, CHARMORPH_PT_Hair]
