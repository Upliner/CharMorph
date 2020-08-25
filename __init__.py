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

import os, logging
import bpy

from . import library, morphing, randomize, file_io, materials, hair, fitting, finalize, editing

rootLogger = logging.getLogger(None)
if not rootLogger.hasHandlers():
    rootLogger.setLevel(10)
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(name)s - %(funcName)s - %(lineno)s - %(message)s'))
    rootLogger.addHandler(ch)

logger = logging.getLogger(__name__)

bl_info = {
    "name": "CharMorph",
    "author": "Michael Vigovsky",
    "version": (0, 0, 5),
    "blender": (2, 83, 0),
    "location": "View3D > Tools > CharMorph",
    "description": "Character creation and morphing (MB-Lab based)",
    "warning": "Requires Rigify addon to rig characters!",
    'wiki_url': "",
    'tracker_url': 'https://github.com/Upliner/CharMorph/issues',
    "category": "Characters"
}

owner = object()

class VIEW3D_PT_CharMorph(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_CharMorph"
    bl_label = "CharMorph {0}.{1}.{2}".format(bl_info["version"][0], bl_info["version"][1], bl_info["version"][2])
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"
    bl_order = 1

    @classmethod
    def poll(self, context):
        if morphing.last_object is None and context.active_object is not None and not hasattr(context.scene,'charmorphs'):
            bpy.msgbus.publish_rna(key=(bpy.types.LayerObjects, "active"))
        return True

    def draw(self, context):
        pass

def get_meshes(ui, context):
    result = [(o.name,o.name,"") for o in bpy.data.objects if o.type == "MESH"]
    if not result:
        return [("","<None>","")]
    return result

#TODO: Use multiple inheritance to move props to corresponding modules?
class CharMorphUIProps(bpy.types.PropertyGroup):
    # Creation
    base_model: bpy.props.EnumProperty(
        name = "Base",
        items = lambda ui, context: [(char[0],char[1].config.get("title",char[0] + " (no config)"),"") for char in library.chars.items()],
        description = "Choose a base model")
    material_mode: bpy.props.EnumProperty(
        name = "Materials",
        default = "TS",
        items = [
            ("NS", "Non-Shared","Use unique material for each character"),
            ("TS", "Shared textures only","Use same texture for all characters"),
            ("MS", "Shared","Use same materials for all characters")],
        description = "Choose a base model")
    material_local: bpy.props.BoolProperty(
        name = "Use local materials", default=True,
        description = "Use local copies of materials for faster loading")

    # Morphing
    preset_mix: bpy.props.BoolProperty(
        name="Mix with current",
        description="Mix selected preset with current morphs",
        default=False)
    clamp_combos: bpy.props.BoolProperty(
        name="Clamp combo props",
        description="Clamp combo properties to (-1..1) so they remain in realistic range",
        default=True)
    relative_meta: bpy.props.BoolProperty(
        name="Relative meta props",
        description="Adjust meta props relatively",
        default=True)
    export_format: bpy.props.EnumProperty(
        name="Format",
        description="Export format",
        default="yaml",
        items=[
            ("yaml","CharMorph (yaml)",""),
            ("json","MB-Lab (json)","")
        ])

    # Randomize
    randomize_morphs: bpy.props.BoolProperty(
        name = "Morphs", default=True,
        description = "Randomize morphs")
    randomize_mats: bpy.props.BoolProperty(
        name = "Materials", default=False,
        description = "Randomize materials")
    randomize_incl: bpy.props.StringProperty(
        name = "Incl. regex")
    randomize_excl: bpy.props.StringProperty(
        name = "Excl. regex", default=r"^Fantasy\_")
    randomize_segs: bpy.props.IntProperty(
        name = "Segments",
        default=7,
        min=2, soft_max=25,
        description = "Segment count for segmented randomization"
    )
    randomize_mode: bpy.props.EnumProperty(
        name="Mode",
        default = "RL1",
        items = [
            ("OVR","Overwrite current", "Overwrite current morphing"),
            ("RL1","Relative to non-random", "Relative to last hand-edited morphing"),
            ("RL2","Relative to current", "Relative to current morphing"),
            ("SEG","Segmented", "Split every property to segments and remain within them"),
        ],
        description = "Randomization mode (doesn't affect material colors)")
    randomize_strength: bpy.props.FloatProperty(
        name = "Strength", min=0, max=1, default=0.2, precision=2, description = "Randomization strength", subtype = "FACTOR")

    # Fitting
    fitting_char: bpy.props.EnumProperty(
        name="Char",
        description="Character for fitting",
        items=get_meshes)
    fitting_asset: bpy.props.EnumProperty(
        name="Local asset",
        description="Asset for fitting",
        items=get_meshes)
    fitting_mask: bpy.props.EnumProperty(
        name="Mask",
        default = "COMB",
        items = [
            ("NONE", "No mask","Don't mask character at all"),
            ("SEPR", "Separate","Use separate mask vertex groups and modifiers for each asset"),
            ("COMB", "Combined","Use combined vertex group and modifier for all character assets"),
        ],
        description="Mask parts of character that are invisible under clothing")
    fitting_transforms: bpy.props.BoolProperty(
        name="Apply transforms",
        default=True,
        description="Apply object transforms before fitting")
    fitting_weights: bpy.props.BoolProperty(
        name="Transfer weights",
        default=True,
        description="Transfer armature weights to the asset")
    fitting_armature: bpy.props.BoolProperty(
        name="Transfer armature",
        default=True,
        description="Transfer character armature modifiers to the asset")
    fitting_library_asset: bpy.props.EnumProperty(
        name="Library asset",
        description="Select asset from library",
        items = library.get_fitting_assets)
    fitting_library_dir: bpy.props.StringProperty(
        name = "Library dir",
        description = "Additional library directory",
        update = library.update_fitting_assets,
        subtype = 'DIR_PATH')

    # Hair
    hair_scalp: bpy.props.BoolProperty(
        name="Use scalp mesh",
        description="Use scalp mesh as emitter instead of whole body")
    hair_deform: bpy.props.BoolProperty(
        name="Live deform",
        description="Refit hair in real time (slower than clothing)")
    hair_color: bpy.props.EnumProperty(
        name="Hair color",
        description="Hair color",
        items = [("","<Not implemented yet>","")])
    hair_style: bpy.props.EnumProperty(
        name="Hairstyle",
        description="Hairstyle",
        items = hair.get_hairstyles)

    # Finalize
    fin_morph: bpy.props.EnumProperty(
        name="Apply morphs",
        default = "SK",
        items = [
            ("NO", "Don't apply","Keep all morphing shape keys"),
            ("SK", "Keep original basis","Keep original basis shape key (recommended if you plan to fit more assets)"),
            ("AL", "Full apply", "Apply current mix as new basis and remove all shape keys"),
        ],
        description="Apply current shape key mix")
    fin_rig: bpy.props.EnumProperty(
        name="Rig",
        default = "RG",
        items = [
            ("NO", "None", "Don't generate armature"),
            ("MR", "Metarig only", "Generate metarig only"),
            ("RG", "Rigify", "Use rigify to generate full rig (Rigify addon must be enabled!)"),
        ],
        description="Rigging options")
    fin_subdivision: bpy.props.EnumProperty(
        name="Subdivision",
        default = "RO",
        items = [
            ("NO", "No", "No subdivision surface"),
            ("RO", "Render only", "Use subdivision only for rendering"),
            ("RV", "Render+Viewport", "Use subdivision for rendering and viewport (may be slow on old hardware)"),
        ],
        description="Use subdivision surface for smoother look")
    fin_csmooth: bpy.props.BoolProperty(
        name="Corrective smooth",
        default = True,
        description="Use corrective smooth to fix armature deform artifacts")
    fin_vg_cleanup: bpy.props.BoolProperty(
        name="Cleanup vertex groups",
        default = True,
        description="Remove unused vertex groups after finalization")

class CharMorphPrefs(bpy.types.AddonPreferences):
    bl_idname = __package__

    adult_mode: bpy.props.BoolProperty(
        name="Adult mode",
        description="No censors, enable adult assets (genitails, pubic hair)",
    )

    def draw(self, context):
        self.layout.prop(self,"adult_mode")

def on_select_object():
    obj = bpy.context.active_object
    if obj is None:
        return
    if obj.type == "MESH":
        asset = None
        if (obj.parent and obj.parent.type == "MESH" and
                "charmorph_fit_id" in obj.data and
                "charmorph_template" not in obj.data):
            asset = obj
            obj = obj.parent
        try:
            ui = bpy.context.scene.charmorph_ui
            if asset:
                ui.fitting_char = obj.name
                ui.fitting_asset = asset.name
            elif library.obj_char(obj).name:
                ui.fitting_char = obj.name
            else:
                ui.fitting_asset = obj.name
        except:
            pass
    if obj == morphing.last_object:
        return
    morphing.create_charmorphs(obj)

@bpy.app.handlers.persistent
def load_handler(dummy):
    morphing.del_charmorphs()
    on_select_object()

bpy.app.handlers.load_post.append(load_handler)

classes = [CharMorphPrefs, CharMorphUIProps, VIEW3D_PT_CharMorph]

for module in [library, morphing, randomize, file_io, materials, fitting, hair, finalize]:
    classes.extend(module.classes)

class_register, class_unregister = bpy.utils.register_classes_factory(classes)

def register():
    print("Charmorph register")
    library.load_library()
    class_register()
    bpy.types.Scene.charmorph_ui = bpy.props.PointerProperty(type=CharMorphUIProps, options={"SKIP_SAVE"})

    bpy.msgbus.subscribe_rna(
        owner=owner,
        key = (bpy.types.LayerObjects, "active"),
        args=(),
        notify = on_select_object)
    editing.register()

def unregister():
    print("Charmorph unregister")
    editing.unregister()
    bpy.msgbus.clear_by_owner(owner)
    del bpy.types.Scene.charmorph_ui
    morphing.del_charmorphs()

    class_unregister()

if __name__ == "__main__":
    register()
