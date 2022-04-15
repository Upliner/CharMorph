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

import os, logging

import bpy, bpy_extras  # pylint: disable=import-error

from .lib import charlib, fit_calc, morphers, utils
from . import morphing

logger = logging.getLogger(__name__)

fitter = None

def get_fitter(target):
    global fitter
    if isinstance(target, morphers.Morpher):
        morpher = target
    elif isinstance(target, bpy.types.Object):
        morpher = morphing.morpher if morphing.morpher and morphing.morpher.obj == target else morphers.get_morpher(target)
    else:
        raise Exception("Fitter: invalid target")

    if not fitter or fitter.morpher != morpher:
        fitter = fit_calc.Fitter(morpher)

    return fitter

morphers.Morpher.handlers.append(lambda self: get_fitter(self).refit_all())

def get_asset_conf(context):
    ui = context.window_manager.charmorph_ui
    item = ui.fitting_library_asset
    if item.startswith("char_"):
        obj = ui.fitting_char
        char = charlib.obj_char(obj)
        return char.assets.get(item[5:])
    if item.startswith("add_"):
        return charlib.additional_assets.get(item[4:])
    return None

def get_fitting_assets(ui, _):
    char = charlib.obj_char(ui.fitting_char)
    return [("char_" + k, k, '') for k in sorted(char.assets.keys())] + [("add_" + k, k, '') for k in sorted(charlib.additional_assets.keys())]

class UIProps:
    fitting_char: bpy.props.PointerProperty(
        name="Char",
        description="Character for fitting",
        type=bpy.types.Object,
        poll=lambda ui, obj: utils.visible_mesh_poll(ui, obj) and ("charmorph_fit_id" not in obj.data or 'cm_alt_topo' in obj.data))
    fitting_asset: bpy.props.PointerProperty(
        name="Local asset",
        description="Asset for fitting",
        type=bpy.types.Object,
        poll=lambda ui, obj: utils.visible_mesh_poll(ui, obj) and ("charmorph_template" not in obj.data))
    fitting_mask: bpy.props.EnumProperty(
        name="Mask",
        default="COMB",
        items=[
            ("NONE", "No mask", "Don't mask character at all"),
            ("SEPR", "Separate", "Use separate mask vertex groups and modifiers for each asset"),
            ("COMB", "Combined", "Use combined vertex group and modifier for all character assets"),
        ],
        description="Mask parts of character that are invisible under clothing")
    fitting_transforms: bpy.props.BoolProperty(
        name="Apply transforms",
        default=True,
        description="Apply object transforms before fitting")
    fitting_weights: bpy.props.EnumProperty(
        name="Weights",
        default="ORIG",
        items= [
            ("NONE", "None", "Don't transfer weights and armature modifiers to the asset"),
            ("ORIG", "Original", "Use original weights from character library"),
            ("OBJ", "Object", "Use weights directly from object (use it if you manually weight-painted the character before fitting the asset)"),
        ],
        description="Select source for armature deform weights")
    fitting_weights_ovr: bpy.props.BoolProperty(
        name="Weights overwrite",
        default=False,
        description="Overwrite existing asset weights")
    fitting_library_asset: bpy.props.EnumProperty(
        name="Library asset",
        description="Select asset from library",
        items=get_fitting_assets)
    fitting_library_dir: bpy.props.StringProperty(
        name="Library dir",
        description="Additional library directory",
        update=charlib.update_fitting_assets,
        subtype='DIR_PATH')

def get_char(context):
    obj = mesh_obj(context.window_manager.charmorph_ui.fitting_char)
    if not obj or ('charmorph_fit_id' in obj.data and 'cm_alt_topo' not in obj.data):
        return None
    return obj

def get_asset_obj(context):
    return mesh_obj(context.window_manager.charmorph_ui.fitting_asset)

class EmptyAsset:
    author=""
    license=""

class CHARMORPH_PT_Fitting(bpy.types.Panel):
    bl_label = "Assets"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 7

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" # is it neccesary?

    def draw(self, context):
        ui = context.window_manager.charmorph_ui
        l = self.layout
        col = l.column(align=True)
        col.prop(ui, "fitting_char")
        col.prop(ui, "fitting_asset")
        l.prop(ui, "fitting_mask")
        col = l.column(align=True)
        col.prop(ui, "fitting_weights")
        col.prop(ui, "fitting_weights_ovr")
        col.prop(ui, "fitting_transforms")
        l.separator()
        if ui.fitting_asset and 'charmorph_fit_id' in ui.fitting_asset.data:
            l.operator("charmorph.unfit")
        else:
            l.operator("charmorph.fit_local")
        l.separator()
        l.operator("charmorph.fit_external")
        asset = get_asset_conf(context) or EmptyAsset
        col = l.column(align=True)
        col.label(text="Author: " + asset.author)
        col.label(text="License: " + asset.license)
        l.prop(ui, "fitting_library_asset")

        l.operator("charmorph.fit_library")
        l.prop(ui, "fitting_library_dir")
        l.separator()

def mesh_obj(obj):
    if obj and obj.type == "MESH":
        return obj
    return None

class OpFitLocal(bpy.types.Operator):
    bl_idname = "charmorph.fit_local"
    bl_label = "Fit local asset"
    bl_description = "Fit selected local asset to the character"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        if context.mode != "OBJECT":
            return False
        char = get_char(context)
        if not char:
            return False
        asset = get_asset_obj(context)
        if not asset or asset == char:
            return False
        return True

    def execute(self, context): #pylint: disable=no-self-use
        get_fitter(get_char(context)).fit_new(get_asset_obj(context))
        return {"FINISHED"}

def fitExtPoll(context):
    return context.mode == "OBJECT" and get_char(context)

def fit_import(char, lst):
    if len(lst) == 0:
        return True
    result = True
    f = get_fitter(char)
    f.lock_comb_mask()
    try:
        for asset in lst:
            obj = utils.import_obj(asset.blend_file, asset.name)
            if obj is None:
                result = False
            obj["charmorph_asset"] = asset.name
            f.fit_new(obj)
    finally:
        f.unlock_comb_mask()
    ui = bpy.context.window_manager.charmorph_ui
    ui.fitting_char = char # For some reason combo box value changes after importing, fix it
    if len(lst) == 1:
        ui.fitting_asset = obj
    return result

class OpFitExternal(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    bl_idname = "charmorph.fit_external"
    bl_label = "Fit from file"
    bl_description = "Import and fit an asset from external .blend file"
    bl_options = {"UNDO"}

    filename_ext = ".blend"
    filter_glob: bpy.props.StringProperty(default="*.blend", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return fitExtPoll(context)

    def execute(self, context):
        name, _ = os.path.splitext(self.filepath)
        if fit_import(get_char(context), (charlib.Asset(name, self.filepath),)):
            return {"FINISHED"}
        self.report({'ERROR'}, "Import failed")
        return {"CANCELLED"}

class OpFitLibrary(bpy.types.Operator):
    bl_idname = "charmorph.fit_library"
    bl_label = "Fit from library"
    bl_description = "Import and fit an asset from library"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return fitExtPoll(context)

    def execute(self, context):
        asset_data = get_asset_conf(context)
        if asset_data is None:
            self.report({'ERROR'}, "Asset is not found")
            return {"CANCELLED"}
        if fit_import(get_char(context), (asset_data,)):
            return {"FINISHED"}
        self.report({'ERROR'}, "Import failed")
        return {"CANCELLED"}

class OpUnfit(bpy.types.Operator):
    bl_idname = "charmorph.unfit"
    bl_label = "Unfit"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        asset = get_asset_obj(context)
        return context.mode == "OBJECT" and asset and 'charmorph_fit_id' in asset.data

    def execute(self, context): # pylint: disable=no-self-use
        ui = context.window_manager.charmorph_ui
        asset = get_asset_obj(context)

        del asset.data['charmorph_fit_id']
        mask = fit_calc.mask_name(asset)
        for char in [asset.parent, ui.fitting_char]:
            if not char or char == asset or 'charmorph_fit_id' in char.data:
                continue
            if mask in char.modifiers:
                char.modifiers.remove(char.modifiers[mask])
            if mask in char.vertex_groups:
                char.vertex_groups.remove(char.vertex_groups[mask])
            if "cm_mask_combined" in char.modifiers:
                f = get_fitter(char)
                f.children = None
                f.recalc_comb_mask()
        if asset.parent:
            asset.parent = asset.parent.parent
            if asset.parent and asset.parent.type == "ARMATURE":
                asset.parent = asset.parent.parent
        if asset.data.shape_keys and "charmorph_fitting" in asset.data.shape_keys.key_blocks:
            asset.shape_key_remove(asset.data.shape_keys.key_blocks["charmorph_fitting"])

        return {"FINISHED"}

classes = [OpFitLocal, OpUnfit, OpFitExternal, OpFitLibrary, CHARMORPH_PT_Fitting]
