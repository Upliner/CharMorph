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

import logging, numpy
import bpy, mathutils, gpu, gpu_extras # pylint: disable=import-error
from bpy_extras import view3d_utils    # pylint: disable=import-error, no-name-in-module

from .lib import morpher, fit_calc, utils
from .common import manager

logger = logging.getLogger(__name__)


class OpResetChar(bpy.types.Operator):
    bl_idname = "charmorph.reset_char"
    bl_label = "Reset character"
    bl_description = "Reset all unavailable character morphs"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and manager.morpher.core.char

    def execute(self, _):
        mcore = manager.morpher.core
        mcore.cleanup_asset_morphs()
        mcore.obj.data["cm_morpher"] = "ext"

        new_morpher = morpher.get(mcore.obj)
        if new_morpher.error or not new_morpher.core.has_morphs():
            if new_morpher.error:
                self.report({'ERROR'}, new_morpher.error)
            else:
                self.report({'ERROR'}, "Still no morphs found")
            del mcore.obj.data["cm_morpher"]
            return {"CANCELLED"}
        manager.update_morpher(new_morpher)
        return {"FINISHED"}


class OpProceedSlowMorphing(bpy.types.Operator):
    bl_idname = "charmorph.proceed_slow"
    bl_label = "Proceed"
    bl_description = "Proceed to morphing desite it will be very slow"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and manager.morpher and manager.morpher.is_slow

    def execute(self, _):
        manager.morpher.is_slow = False
        return {"FINISHED"}


class OpBuildAltTopo(bpy.types.Operator):
    bl_idname = "charmorph.build_alt_topo"
    bl_label = "Build alt topo"
    bl_description = "Build alt topo from modified character mesh"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, _):
        return manager.morpher and manager.morpher.core.alt_topo_buildable and manager.morpher.core.has_morphs()

    def execute(self, context):  # pylint: disable=no-self-use
        ui = context.window_manager.charmorph_ui
        mcore = manager.morpher.core
        obj = mcore.obj
        btype = ui.alt_topo_build_type
        sk = obj.data.shape_keys
        has_sk = bool(sk and sk.key_blocks)
        if btype == "K" and has_sk:
            obj.data["cm_alt_topo"] = "sk"
            manager.update_morpher(morpher.get(obj))
            return {"FINISHED"}
        result = fit_calc.FitCalculator(fit_calc.geom_morpher_final(mcore))\
            .get_binding(obj).fit(mcore.full_basis - mcore.get_final())
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

        manager.update_morpher(morpher.get(obj))
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
        get=lambda _: manager.morpher.core.clamp,
        set=lambda _, value: manager.morpher.set_clamp(value),
        update=lambda _ui, _: manager.morpher.update())
    morph_l1: bpy.props.EnumProperty(
        name="Type",
        description="Choose character type",
        items=lambda _ui, _: manager.morpher.L1_list,
        get=lambda _: manager.morpher.L1_idx,
        set=lambda _, value: manager.morpher.set_L1_by_idx(value),
        options={"SKIP_SAVE"})
    morph_category: bpy.props.EnumProperty(
        name="Category",
        items=lambda _ui, _:
            [("<None>", "<None>", "Hide all morphs"), ("<All>", "<All>", "Show all morphs")]
            + manager.morpher.categories,
        description="Select morphing categories to show")
    morph_preset: bpy.props.EnumProperty(
        name="Presets",
        items=lambda _ui, _: manager.morpher.presets_list,
        description="Choose morphing preset",
        update=lambda ui, _: manager.morpher.apply_morph_data(
            manager.morpher.presets.get(ui.morph_preset), ui.morph_preset_mix))
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
        if context.mode != "OBJECT":
            if manager.morpher and not manager.morpher.error:
                manager.last_object = None
                manager.morpher.error = "Please re-select character"
            return False
        return manager.morpher

    def draw(self, context):
        mm = manager.morpher
        m = mm.core
        ui = context.window_manager.charmorph_ui

        if mm.is_slow:
            col = self.layout.column()
            col.label(text="Warning:")
            col.label(text="Morphing a rigged character")
            col.label(text="with this rig type")
            col.label(text="can be very slow")
            col.label(text="Proceed with caution")
            self.layout.operator("charmorph.proceed_slow")
            return

        if mm.error:
            self.layout.label(text="Morphing is impossible:")
            col = self.layout.column()
            for line in mm.error.split("\n"):
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

        self.layout.operator('charmorph.interactive')
        self.layout.label(text="Character type")
        col = self.layout.column(align=True)
        if m.morphs_l1:
            col.prop(ui, "morph_l1")

        col = self.layout.column(align=True)
        col.prop(ui, "morph_preset")
        col.prop(ui, "morph_preset_mix")

        col.separator()

        morphs = context.window_manager.charmorphs
        meta_morphs = m.char.morphs_meta.keys()
        if meta_morphs:
            self.layout.label(text="Meta morphs")
            col = self.layout.column(align=True)
            col.prop(ui, "meta_materials")
            col.prop(ui, "relative_meta")

            for prop in meta_morphs:
                col.prop(morphs, "meta_" + prop, slider=True)

        self.layout.prop(ui, "morph_clamp")

        self.layout.separator()

        if mm.categories:
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
        return manager.morpher and manager.morpher.materials and manager.morpher.materials.props

    def draw(self, _):
        for _, prop in manager.morpher.materials.get_node_outputs():
            self.layout.prop(prop, "default_value", text=prop.node.label)


classes = [OpResetChar, OpBuildAltTopo, OpProceedSlowMorphing, CHARMORPH_PT_Morphing, CHARMORPH_PT_Materials]
