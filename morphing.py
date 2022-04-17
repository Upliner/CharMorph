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
# Copyright (C) 2020-2022 Michael Vigovsky

import logging
import bpy # pylint: disable=import-error

from .lib import charlib, morphers, materials, fitting, utils

logger = logging.getLogger(__name__)

last_object = None

def get_obj_char(context):
    if morpher:
        return morpher.obj, morpher.char
    obj = context.object
    if obj:
        if obj.type == "ARMATURE":
            children = obj.children
            if len(children) == 1:
                obj = children[0]
        if obj.type == "MESH":
            char = charlib.obj_char(obj)
            if char:
                return obj, char
    return (None, None)

def get_basis(data):
    return morphers.get_basis(data, morpher, True)

null_morpher = morphers.Morpher(None)
null_morpher.lock()

morpher = null_morpher

def get_morpher(obj, storage = None):
    logger.debug("switching object to %s", obj.name if obj else "")

    result = morphers.get_morpher(obj, storage)
    result.materials = materials.Materials(obj)
    return result

def update_morpher(m : morphers.Morpher):
    global last_object, morpher
    morpher = m
    last_object = m.obj

    ui = bpy.context.window_manager.charmorph_ui

    if ui.fin_rig not in m.char.armature:
        if m.char.default_armature:
            ui.fin_rig = m.char.default_armature
        else:
            ui.fin_rig = "-"

    if not m.L1 and m.char.default_type:
        m.set_L1(m.char.default_type)

    ui.morph_category = "<None>"

    if not m.morphs_l2:
        m.create_charmorphs_L2()

def recreate_charmorphs():
    global morpher
    if not morpher:
        return
    morpher = get_morpher(morpher.obj)
    morpher.create_charmorphs_L2()

def create_charmorphs(obj):
    global last_object
    last_object = obj
    if obj.type != "MESH":
        return
    if morpher.obj == obj:
        return

    update_morpher(get_morpher(obj))

def del_charmorphs():
    global last_object, morpher
    last_object = None
    morpher = null_morpher
    morphers.del_charmorphs_L2()

def bad_object():
    if not morpher:
        return False
    try:
        return bpy.data.objects.get(morpher.obj.name) is not morpher.obj
    except ReferenceError:
        logger.warning("Current morphing object is bad, resetting...")
        return True

class OpResetChar(bpy.types.Operator):
    bl_idname = "charmorph.reset_char"
    bl_label = "Reset character"
    bl_description = "Reset all unavailable character morphs"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and morpher.char

    def execute(self, _):
        obj = morpher.obj
        obj.data["cm_morpher"] = "ext"
        new_morpher = get_morpher(obj)
        if new_morpher.error or not new_morpher.has_morphs():
            if new_morpher.error:
                self.report({'ERROR'}, new_morpher.error)
            else:
                self.report({'ERROR'}, "Still no morphs found")
            del obj.data["cm_morpher"]
            return {"CANCELLED"}
        update_morpher(new_morpher)
        return {"FINISHED"}

class OpBuildAltTopo(bpy.types.Operator):
    bl_idname = "charmorph.build_alt_topo"
    bl_label = "Build alt topo"
    bl_description = "Build alt topo from modified character mesh"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, _):
        return morpher and morpher.alt_topo_buildable and morpher.has_morphs()

    def execute(self, context):
        ui = context.window_manager.charmorph_ui
        obj = morpher.obj
        btype = ui.alt_topo_build_type
        sk = obj.data.shape_keys
        has_sk = bool(sk and sk.key_blocks)
        if btype == "K" and has_sk:
            obj.data["cm_alt_topo"] = "sk"
            update_morpher(get_morpher(obj))
            return {"FINISHED"}
        weights = fitting.ReverseFitCalculator(morpher).get_weights(obj)
        result = fitting.calc_fit(morpher.full_basis - morpher.get_final(), *weights)
        result += utils.get_morphed_numpy(obj)
        result = result.reshape(-1)
        if btype == "K":
            basis = obj.shape_key_add(name="Basis", from_mix=False)
            final = obj.shape_key_add(name="charmorph_final", from_mix=False)
            basis.data.foreach_set("co", result)
            obj.data["cm_alt_topo"] = "sk"
            final.value = 1
        else:
            mesh = obj.data.copy()
            obj.data["cm_alt_topo"] = mesh
            if has_sk:
                old_mesh = obj.data
                obj.data = mesh
                while mesh.shape_keys and mesh.shape_keys.key_blocks:
                    obj.shape_key_remove(mesh.shape_keys.key_blocks[0])
                obj.data = old_mesh
            mesh.vertices.foreach_set("co", result)

        update_morpher(get_morpher(obj))
        return {"FINISHED"}

class UIProps:
    relative_meta: bpy.props.BoolProperty(
        name="Relative meta props",
        description="Adjust meta props relatively",
        default=True)
    meta_materials: bpy.props.EnumProperty(
        name="Materials",
        description="How changing meta properties will affect materials",
        default="A",
        items=[
            ("N", "None", "Don't change materials"),
            ("A", "Absolute", "Change materials according to absolute value of meta property"),
            ("R", "Relative", "Change materials according to relative value of meta property")])
    morph_filter: bpy.props.StringProperty(
        name="Filter",
        description="Show only morphs mathing this name",
        options={"TEXTEDIT_UPDATE"},
    )
    morph_clamp: bpy.props.BoolProperty(
        name="Clamp props",
        description="Clamp properties to (-1..1) so they remain in realistic range",
        get=lambda _: morpher.clamp,
        set=lambda _, value: morpher.set_clamp(value),
        update=lambda ui, _: morpher.update())
    morph_l1: bpy.props.EnumProperty(
        name="Type",
        description="Choose character type",
        items=lambda ui, _: morpher.L1_list,
        get=lambda _: morpher.L1_idx,
        set=lambda _, value: morpher.set_L1_by_idx(value),
        options={"SKIP_SAVE"})
    morph_category: bpy.props.EnumProperty(
        name="Category",
        items=lambda ui, _: [("<None>", "<None>", "Hide all morphs"), ("<All>", "<All>", "Show all morphs")] + morpher.categories,
        description="Select morphing categories to show")
    morph_preset: bpy.props.EnumProperty(
        name="Presets",
        items=lambda ui, _: morpher.presets_list,
        description="Choose morphing preset",
        update=lambda ui, _: morpher.apply_morph_data(morpher.presets.get(ui.morph_preset, {}), ui.morph_preset_mix))
    morph_preset_mix: bpy.props.BoolProperty(
        name="Mix with current",
        description="Mix selected preset with current morphs",
        default=False)
    alt_topo_build_type: bpy.props.EnumProperty(
        name="Alt topo type",
        description="Type of alt topo to build",
        default="P",
        items=[
            ("K", "Shapekey", "Store alt topo basis in shapekey"),
            ("P", "Separate mesh", "Store alt topo basis in separate mesh")])


class CHARMORPH_PT_Morphing(bpy.types.Panel):
    bl_label = "Morphing"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 2

    @classmethod
    def poll(cls, context):
        global last_object, morpher
        if context.mode != "OBJECT":
            if morpher:
                last_object = None
                morpher = null_morpher
            return False
        return morpher

    def draw(self, context):
        m = morpher
        ui = context.window_manager.charmorph_ui

        if m.error:
            self.layout.label(text="Morphing is impossible:")
            col = self.layout.column()
            for line in m.error.split("\n"):
                col.label(text=line)
            if m.alt_topo_buildable:
                col = self.layout.column()
                col.label(text="It seems you've changed object's topology")
                col.label(text="You can try to build alt topo")
                col.label(text="to continue morphing")
                self.layout.operator("charmorph.build_alt_topo")
                self.layout.prop(ui, "alt_topo_build_type")

            return

        if not hasattr(context.window_manager, "charmorphs") or not m.has_morphs():
            if m.char:
                col = self.layout.column(align=True)
                col.label(text="Object is detected as")
                col.label(text="valid CharMorph character,")
                col.label(text="but the morphing data was removed")
                if m.obj.data.get("cm_morpher") == "ext":
                    return
                col.separator()
                col.label(text="You can reset the character")
                col.label(text="to resume morphing")
                col.separator()
                col.operator('charmorph.reset_char')
            else:
                self.layout.label(text="No morphing data found")
            return

        self.layout.label(text="Character type")
        col = self.layout.column(align=True)
        if m.morphs_l1:
            col.prop(ui, "morph_l1")

        col = self.layout.column(align=True)
        col.prop(ui, "morph_preset")
        col.prop(ui, "morph_preset_mix")

        col.separator()

        morphs = context.window_manager.charmorphs
        meta_morphs = m.meta_dict().keys()
        if meta_morphs:
            self.layout.label(text="Meta morphs")
            col = self.layout.column(align=True)
            col.prop(ui, "meta_materials")
            col.prop(ui, "relative_meta")

            for prop in meta_morphs:
                col.prop(morphs, "meta_" + prop, slider=True)

        self.layout.prop(ui, "morph_clamp")

        self.layout.separator()

        if m.categories:
            self.layout.label(text="MORE MORPHS HERE:")
            self.layout.prop(ui, "morph_category")
            if ui.morph_category == "<None>":
                return

        self.layout.prop(ui, "morph_filter")
        col = self.layout.column(align=True)
        for morph in m.morphs_l2:
            prop = morph.name
            if not prop:
                col.separator()
            elif ui.morph_category == "<All>" or prop.startswith(ui.morph_category):
                if ui.morph_filter.lower() in prop.lower():
                    col.prop(morphs, "prop_" + prop, slider=True)

class CHARMORPH_PT_Materials(bpy.types.Panel):
    bl_label = "Materials"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 6

    @classmethod
    def poll(cls, _):
        return morpher and morpher.materials and morpher.materials.props

    def draw(self, _):
        for prop in morpher.materials.props.values():
            if prop.node:
                self.layout.prop(prop, "default_value", text=prop.node.label)

classes = [OpResetChar, OpBuildAltTopo, CHARMORPH_PT_Morphing, CHARMORPH_PT_Materials]
