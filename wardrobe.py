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
# Wardrobe browser - Daz3D-style one-click asset management

import logging
import bpy  # pylint: disable=import-error

from .lib import fitting, utils
from .lib.charlib import library
from .common import manager as mm
from . import assets as assets_module

logger = logging.getLogger(__name__)

# ─── Categories ───

CATEGORIES = [
    ("ALL", "All", "Show all assets"),
    ("Tops", "Tops", "Upper body clothing"),
    ("Bottoms", "Bottoms", "Lower body clothing"),
    ("Dresses", "Dresses", "Full body clothing"),
    ("Shoes", "Shoes", "Footwear"),
    ("Accessories", "Accessories", "Hats, jewelry, etc."),
    ("Hair", "Hair", "Hair assets"),
    ("Other", "Other", "Uncategorized"),
]


# ─── Helpers ───

def get_wardrobe_char(context):
    """Get the character object for wardrobe operations."""
    ui = context.window_manager.charmorph_ui
    obj = ui.fitting_char
    if not obj or obj.type != "MESH":
        return None
    if 'charmorph_fit_id' in obj.data and 'cm_alt_topo' not in obj.data:
        return None
    return obj


def get_fitted_assets(char_obj):
    """Find all fitted assets as (name, obj) tuples."""
    if not char_obj:
        return
    for child in char_obj.children:
        if child.type != "MESH" or 'charmorph_fit_id' not in child.data:
            continue
        name = child.data.get("charmorph_asset") or child.data.get("charmorph_wardrobe_asset")
        if name:
            yield (name, child)


def get_available_assets(char_obj, category="ALL", search=""):
    """Get library assets not yet fitted, filtered by category and search."""
    char = library.obj_char(char_obj)
    if not char:
        return

    fitted_names = {name for name, _ in get_fitted_assets(char_obj)}
    search_lower = search.lower()

    for name, asset_conf in sorted(char.assets.items()):
        if name in fitted_names:
            continue

        # Category filter
        if category != "ALL":
            asset_cat = getattr(asset_conf, 'category', 'Other')
            if asset_cat != category:
                continue

        # Search filter
        if search_lower:
            search_target = name.lower()
            tags = getattr(asset_conf, 'tags', [])
            tag_str = " ".join(t.lower() for t in tags) if tags else ""
            if search_lower not in search_target and search_lower not in tag_str:
                continue

        yield (name, asset_conf)


# ─── Material Presets ───

def get_material_presets_items(asset_name, char_obj):
    """Get EnumProperty items for material presets of an asset."""
    char = library.obj_char(char_obj)
    if not char:
        return []
    asset_conf = char.assets.get(asset_name)
    if not asset_conf:
        return []
    presets = asset_conf.material_presets
    if not presets:
        return []
    items = [("_none_", "(default)", "No preset applied")]
    for key, preset in presets.items():
        label = preset.get("label", key) if isinstance(preset, dict) else key
        items.append((key, label, ""))
    return items


def apply_material_preset(asset_obj, asset_conf, preset_name):
    """Apply a material preset to a fitted asset object."""
    presets = asset_conf.material_presets
    if not presets or preset_name not in presets:
        return False

    preset = presets[preset_name]
    materials_data = preset.get("materials", {})

    for mtl in asset_obj.data.materials:
        if not mtl or not mtl.node_tree:
            continue
        mtl_key = mtl.name.split(".")[-1] if "." in mtl.name else mtl.name
        mtl_presets = materials_data.get(mtl_key) or materials_data.get(mtl.name)
        if not mtl_presets:
            continue

        for node in mtl.node_tree.nodes:
            if node.type != "BSDF_PRINCIPLED":
                continue
            for input_name, value in mtl_presets.items():
                inp = node.inputs.get(input_name)
                if inp is None:
                    continue
                if isinstance(value, list) and len(value) >= 3:
                    # Color value
                    if len(value) == 3:
                        value = value + [1.0]
                    inp.default_value = value
                else:
                    inp.default_value = value

    return True


# ─── Per-Asset Parameters ───

def apply_asset_offset(asset_obj, offset_value):
    """Apply normal-direction offset to a fitted asset via Displace modifier."""
    mod_name = "cm_wardrobe_offset"
    mod = asset_obj.modifiers.get(mod_name)
    if offset_value == 0.0:
        if mod:
            asset_obj.modifiers.remove(mod)
        return
    if not mod:
        mod = asset_obj.modifiers.new(mod_name, "DISPLACE")
        mod.direction = 'NORMAL'
        mod.mid_level = 0.0
    mod.strength = offset_value


def apply_asset_smoothing(asset_obj, smooth_value):
    """Apply corrective smooth to a fitted asset."""
    mod_name = "cm_wardrobe_smooth"
    mod = asset_obj.modifiers.get(mod_name)
    if smooth_value == 0.0:
        if mod:
            asset_obj.modifiers.remove(mod)
        return
    if not mod:
        mod = asset_obj.modifiers.new(mod_name, "CORRECTIVE_SMOOTH")
        mod.use_only_smooth = True
        mod.use_pin_boundary = True
    mod.factor = smooth_value
    mod.iterations = max(1, int(smooth_value * 10))


# ─── Visibility Zones ───

def set_visibility_zone(asset_obj, zone_name, visible):
    """Toggle a visibility zone on an asset using MASK modifier."""
    mod_name = f"cm_zone_{zone_name}"
    vg = asset_obj.vertex_groups.get(f"zone_{zone_name}")
    if not vg:
        return False

    mod = asset_obj.modifiers.get(mod_name)
    if visible:
        if mod:
            asset_obj.modifiers.remove(mod)
    else:
        if not mod:
            mod = asset_obj.modifiers.new(mod_name, "MASK")
            mod.vertex_group = vg.name
            mod.invert_vertex_group = False
    return True


# ─── UI Properties ───

class UIProps:
    wardrobe_category: bpy.props.EnumProperty(
        name="Category",
        items=CATEGORIES,
        default="ALL",
        description="Filter assets by category")
    wardrobe_search: bpy.props.StringProperty(
        name="Search",
        default="",
        description="Search assets by name or tags")


# ─── Operators ───

class OpWardrobeAdd(bpy.types.Operator):
    bl_idname = "charmorph.wardrobe_add"
    bl_label = "Add"
    bl_description = "Fit asset from library"
    bl_options = {"UNDO"}

    asset_name: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and get_wardrobe_char(context) is not None

    def execute(self, context):
        char_obj = get_wardrobe_char(context)
        if not char_obj:
            self.report({'ERROR'}, "No character selected")
            return {"CANCELLED"}

        char = library.obj_char(char_obj)
        asset_conf = char.assets.get(self.asset_name)
        if asset_conf is None:
            self.report({'ERROR'}, f"Asset '{self.asset_name}' not found")
            return {"CANCELLED"}

        fitter = assets_module.get_fitter(char_obj)
        if not fitter.fit_import((asset_conf,)):
            self.report({'ERROR'}, "Import failed")
            return {"CANCELLED"}

        # Tag the fitted object so wardrobe can track it
        for child in char_obj.children:
            if child.type == "MESH" and child.data.get("charmorph_asset") == self.asset_name:
                child.data["charmorph_wardrobe_asset"] = self.asset_name
                break

        return {"FINISHED"}


class OpWardrobeRemove(bpy.types.Operator):
    bl_idname = "charmorph.wardrobe_remove"
    bl_label = "Remove"
    bl_description = "Remove fitted asset"
    bl_options = {"UNDO"}

    asset_name: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and get_wardrobe_char(context) is not None

    def execute(self, context):
        char_obj = get_wardrobe_char(context)
        if not char_obj:
            self.report({'ERROR'}, "No character selected")
            return {"CANCELLED"}

        ui = context.window_manager.charmorph_ui
        for name, obj in get_fitted_assets(char_obj):
            if name == self.asset_name:
                assets_module.unfit_asset(obj, char_obj, ui.fitting_transforms)
                bpy.data.objects.remove(obj, do_unlink=True)
                return {"FINISHED"}

        self.report({'ERROR'}, f"Fitted asset '{self.asset_name}' not found")
        return {"CANCELLED"}


class OpWardrobeMaterialPreset(bpy.types.Operator):
    bl_idname = "charmorph.wardrobe_preset"
    bl_label = "Apply Preset"
    bl_description = "Apply material preset to fitted asset"
    bl_options = {"UNDO"}

    asset_name: bpy.props.StringProperty()
    preset_name: bpy.props.StringProperty()

    def execute(self, context):
        char_obj = get_wardrobe_char(context)
        if not char_obj:
            self.report({'ERROR'}, "No character selected")
            return {"CANCELLED"}

        char = library.obj_char(char_obj)
        asset_conf = char.assets.get(self.asset_name)
        if not asset_conf:
            self.report({'ERROR'}, f"Asset config for '{self.asset_name}' not found")
            return {"CANCELLED"}

        for name, obj in get_fitted_assets(char_obj):
            if name == self.asset_name:
                if apply_material_preset(obj, asset_conf, self.preset_name):
                    return {"FINISHED"}
                self.report({'ERROR'}, "Preset application failed")
                return {"CANCELLED"}

        self.report({'ERROR'}, f"Fitted asset '{self.asset_name}' not found")
        return {"CANCELLED"}


class OpWardrobeOffset(bpy.types.Operator):
    bl_idname = "charmorph.wardrobe_offset"
    bl_label = "Surface Offset"
    bl_description = "Adjust surface offset for fitted asset"
    bl_options = {"UNDO"}

    asset_name: bpy.props.StringProperty()
    offset: bpy.props.FloatProperty(default=0.001, min=-0.01, max=0.05, step=0.01)

    def execute(self, context):
        char_obj = get_wardrobe_char(context)
        if not char_obj:
            return {"CANCELLED"}
        for name, obj in get_fitted_assets(char_obj):
            if name == self.asset_name:
                apply_asset_offset(obj, self.offset)
                return {"FINISHED"}
        return {"CANCELLED"}


class OpWardrobeSmooth(bpy.types.Operator):
    bl_idname = "charmorph.wardrobe_smooth"
    bl_label = "Smoothing"
    bl_description = "Adjust corrective smoothing for fitted asset"
    bl_options = {"UNDO"}

    asset_name: bpy.props.StringProperty()
    factor: bpy.props.FloatProperty(default=0.0, min=0.0, max=1.0, step=0.1)

    def execute(self, context):
        char_obj = get_wardrobe_char(context)
        if not char_obj:
            return {"CANCELLED"}
        for name, obj in get_fitted_assets(char_obj):
            if name == self.asset_name:
                apply_asset_smoothing(obj, self.factor)
                return {"FINISHED"}
        return {"CANCELLED"}


class OpWardrobeZone(bpy.types.Operator):
    bl_idname = "charmorph.wardrobe_zone"
    bl_label = "Toggle Zone"
    bl_description = "Toggle visibility zone on fitted asset"
    bl_options = {"UNDO"}

    asset_name: bpy.props.StringProperty()
    zone_name: bpy.props.StringProperty()
    visible: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        char_obj = get_wardrobe_char(context)
        if not char_obj:
            return {"CANCELLED"}
        for name, obj in get_fitted_assets(char_obj):
            if name == self.asset_name:
                set_visibility_zone(obj, self.zone_name, self.visible)
                return {"FINISHED"}
        return {"CANCELLED"}


# ─── Panel ───

class CHARMORPH_PT_Wardrobe(bpy.types.Panel):
    bl_label = "Wardrobe"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 6

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def draw(self, context):
        ui = context.window_manager.charmorph_ui
        layout = self.layout

        # Character selector
        layout.prop(ui, "fitting_char")

        char_obj = get_wardrobe_char(context)
        if not char_obj:
            layout.label(text="Select a character", icon='INFO')
            return

        char = library.obj_char(char_obj)
        if not char:
            layout.label(text="No CharMorph character", icon='ERROR')
            return

        # Filters
        row = layout.row(align=True)
        row.prop(ui, "wardrobe_category", text="")
        row.prop(ui, "wardrobe_search", text="", icon='VIEWZOOM')

        # ─── Available Assets ───
        available = list(get_available_assets(
            char_obj, ui.wardrobe_category, ui.wardrobe_search))

        if available:
            box = layout.box()
            box.label(text=f"Available ({len(available)})", icon='ASSET_MANAGER')
            for name, asset_conf in available:
                row = box.row(align=True)
                op = row.operator("charmorph.wardrobe_add", text=name, icon='ADD')
                op.asset_name = name
                # Show category tag
                cat = getattr(asset_conf, 'category', 'Other')
                if cat != "Other":
                    row.label(text=cat)

        # ─── Fitted Assets ───
        fitted = list(get_fitted_assets(char_obj))

        if fitted:
            box = layout.box()
            box.label(text=f"Fitted ({len(fitted)})", icon='CHECKMARK')
            for name, obj in fitted:
                # Asset row with remove button
                row = box.row(align=True)
                row.label(text=name, icon='MESH_DATA')
                op = row.operator("charmorph.wardrobe_remove", text="", icon='X')
                op.asset_name = name

                # Material presets (if available)
                asset_conf = char.assets.get(name)
                if asset_conf and asset_conf.material_presets:
                    preset_row = box.row(align=True)
                    preset_row.label(text="", icon='MATERIAL')
                    for preset_key, preset_data in asset_conf.material_presets.items():
                        label = preset_data.get("label", preset_key) if isinstance(preset_data, dict) else preset_key
                        op = preset_row.operator("charmorph.wardrobe_preset", text=label)
                        op.asset_name = name
                        op.preset_name = preset_key

                # Per-asset parameters (offset, smoothing)
                if asset_conf and asset_conf.parameters:
                    params_col = box.column(align=True)
                    for param_key, param_def in asset_conf.parameters.items():
                        if param_key == "offset":
                            op = params_col.operator("charmorph.wardrobe_offset",
                                text=param_def.get("label", "Offset"))
                            op.asset_name = name
                            op.offset = param_def.get("default", 0.001)
                        elif param_key == "smoothing":
                            op = params_col.operator("charmorph.wardrobe_smooth",
                                text=param_def.get("label", "Smoothing"))
                            op.asset_name = name
                            op.factor = param_def.get("default", 0.0)

                # Visibility zones
                if asset_conf and asset_conf.visibility_zones:
                    zone_row = box.row(align=True)
                    zone_row.label(text="", icon='HIDE_OFF')
                    for zone_key, zone_def in asset_conf.visibility_zones.items():
                        label = zone_def.get("label", zone_key) if isinstance(zone_def, dict) else zone_key
                        op = zone_row.operator("charmorph.wardrobe_zone", text=label)
                        op.asset_name = name
                        op.zone_name = zone_key
                        op.visible = True

        if not available and not fitted:
            layout.label(text="No assets found for this character")


classes = [
    OpWardrobeAdd,
    OpWardrobeRemove,
    OpWardrobeMaterialPreset,
    OpWardrobeOffset,
    OpWardrobeSmooth,
    OpWardrobeZone,
    CHARMORPH_PT_Wardrobe,
]
