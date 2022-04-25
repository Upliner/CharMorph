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
import bpy  # pylint: disable=import-error
from bpy_extras.wm_utils.progress_report import ProgressReport  # pylint: disable=import-error, no-name-in-module

from . import morphing
from .lib import charlib, morpher, materials, morphs, utils

logger = logging.getLogger(__name__)


class OpReloadLib(bpy.types.Operator):
    bl_idname = "charmorph.reload_library"
    bl_label = "Reload library"
    bl_description = "Reload character library"

    def execute(self, _):  # pylint: disable=no-self-use
        charlib.load_library()
        morphing.manager.recreate_charmorphs()
        return {"FINISHED"}


def _import_moprhs(wm, obj, char):
    ui = wm.charmorph_ui
    if not ui.use_sk:
        ui.import_morphs = False
        ui.import_expressions = False

    steps = int(ui.import_morphs) + int(ui.import_expressions)
    if not steps:
        return None

    storage = morphs.MorphStorage(char)
    importer = morphs.MorphImporter(storage, obj)

    with ProgressReport(wm) as progress:
        progress.enter_substeps(steps, "Importing shape keys...")
        if ui.import_morphs:
            importer.import_morphs(progress)
            progress.step("Morphs imported")
        if ui.import_expressions:
            importer.import_expressions(progress)
            progress.step("Expressions imported")
        progress.leave_substeps("Shape keys done")

    return storage


class OpImport(bpy.types.Operator):
    bl_idname = "charmorph.import_char"
    bl_label = "Import character"
    bl_description = "Import character"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def execute(self, context):
        ui = context.window_manager.charmorph_ui
        if not ui.base_model:
            self.report({'ERROR'}, "Please select base model")
            return {"CANCELLED"}

        char = charlib.chars[ui.base_model]

        if ui.alt_topo != "<Base>" and char.faces is None:
            ui.alt_topo = "<Base>"
            return {"CANCELLED"}

        if ui.alt_topo == "<Custom>":
            if not ui.alt_topo_obj or ui.alt_topo_obj.type != "MESH":
                self.report({'ERROR'}, "Please select correct custom alternative topology object")
                return {"CANCELLED"}

            orig_mesh = ui.alt_topo_obj.data
            mesh = orig_mesh.copy()
            mesh.name = char.name
            # TODO: cleanup shape keys
            mesh["cm_alt_topo"] = orig_mesh

            obj = bpy.data.objects.new(char.name, mesh)
            context.collection.objects.link(obj)
            storage = None
        else:
            obj = utils.import_obj(char.blend_file(), char.char_obj)
            if obj is None:
                self.report({'ERROR'}, "Import failed")
                return {"CANCELLED"}

            storage = _import_moprhs(context.window_manager, obj, char)

            if not ui.import_morphs and os.path.isdir(char.path("morphs")):
                obj.data["cm_morpher"] = "ext"

            materials.init_materials(obj, char)

        obj.location = context.scene.cursor.location
        if ui.import_cursor_z:
            obj.rotation_mode = "XYZ"
            obj.rotation_euler = (0, 0, context.scene.cursor.rotation_euler[2])

        obj.data["charmorph_template"] = ui.base_model

        if (ui.use_sk or char.np_basis is None) and (not obj.data.shape_keys or not obj.data.shape_keys.key_blocks):
            obj.shape_key_add(name="Basis", from_mix=False)

        m = morpher.get(obj, storage)
        morphing.manager.update_morpher(m)

        context.view_layer.objects.active = obj
        ui.fitting_char = obj

        if char.default_armature and ui.fin_rig == '-':
            ui.fin_rig = char.default_armature

        asset_list = []

        def add_assets(lst):
            asset_list.extend((char.assets[name] for name in lst))
        add_assets(char.default_assets)
        if not utils.is_adult_mode():
            add_assets(char.underwear)

        m.fitter.fit_import(asset_list)
        m.update()

        return {"FINISHED"}


def char_default_tex_set(char):
    if not char:
        return "/"
    if not char.default_tex_set:
        return char.texture_sets[0]
    return char.default_tex_set


def update_base_model(ui, _):
    ui.tex_set = char_default_tex_set(charlib.chars.get(ui.base_model))


class UIProps:
    base_model: bpy.props.EnumProperty(
        name="Base",
        items=lambda _ui, _: [(name, char.title, char.description) for name, char in charlib.chars.items()],
        update=update_base_model,
        description="Choose a base model")
    material_mode: bpy.props.EnumProperty(
        name="Materials",
        default="TS",
        description="Share materials between different Charmorph characters or not",
        items=[
            ("NS", "Non-Shared", "Use unique material for each character"),
            ("TS", "Shared textures only", "Use same texture for all characters"),
            ("MS", "Shared", "Use same materials for all characters")]
    )
    # TODO: copy materials from custom object
    material_local: bpy.props.BoolProperty(
        name="Use local materials", default=True,
        description="Use local copies of materials for faster loading")
    tex_set: bpy.props.EnumProperty(
        name="Texture set",
        description="Select texture set for the character",
        items=lambda ui, _: [
            (name, "<Default>" if name == "/" else name, "")
            for name in charlib.chars.get(ui.base_model, charlib.empty_char).texture_sets
        ],
    )
    tex_downscale: bpy.props.EnumProperty(
        name="Downscale textures",
        description="Downscale large textures to avoid memory overflows",
        default="UL",
        items=[("1K", "1K", ""), ("2K", "2K", ""), ("4K", "4K", ""), ("UL", "No limit", "")]
    )
    import_cursor_z: bpy.props.BoolProperty(
        name="Use Z cursor rotation", default=True,
        description="Take 3D cursor Z rotation into account when creating the character")
    use_sk: bpy.props.BoolProperty(
        name="Use shape keys for morphing", default=False,
        description="Use shape keys during morphing (should be on if you plan to resume morphing later, maybe with other versions of CharMorph)")
    import_morphs: bpy.props.BoolProperty(
        name="Import morphing shape keys", default=False,
        description="Import and morph character using shape keys")
    import_expressions: bpy.props.BoolProperty(
        name="Import expression shape keys", default=False,
        description="Import and morph character using shape keys")
    alt_topo: bpy.props.EnumProperty(
        name="Alt topo",
        default="<Base>",
        description="Select alternative topology to use",
        items=[
            ("<Base>", "<Base>", "Use base character topology"),
            ("<Custom>", "<Custom>", "Use custom local object as alt topo")]
    )
    alt_topo_obj: bpy.props.PointerProperty(
        name="Custom alt topo",
        type=bpy.types.Object,
        description="Select custom object to use as alternative topology",
        poll=utils.visible_mesh_poll)


class CHARMORPH_PT_Library(bpy.types.Panel):
    bl_label = "Character library"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def draw(self, context):
        l = self.layout
        ui = context.window_manager.charmorph_ui
        l.operator('charmorph.reload_library')
        l.separator()
        if charlib.data_dir == "":
            l.label(text="Data dir is not found. Importing is not available.")
            return
        if not charlib.chars:
            l.label(text=f"No characters found at {charlib.data_dir}. Nothing to import.")
            return
        l.prop(ui, "base_model")
        char = charlib.chars.get(ui.base_model)
        if char:
            r = l.row()
            c = r.column()
            c.alignment = "RIGHT"
            c.ui_units_x = 2.5
            c.label(text="Author:")
            c.label(text="License:")
            c = r.column()
            c.label(text=char.author)
            c.label(text=char.license)

        l.prop(ui, "material_mode")
        l.prop(ui, "material_local")
        l.prop(ui, "tex_set")
        l.prop(ui, "tex_downscale")
        l.prop(ui, "import_cursor_z")
        c = l.column()
        c.prop(ui, "use_sk")
        c = c.column()
        c.enabled = ui.use_sk and ui.alt_topo == "<Base>"
        c.prop(ui, "import_morphs")
        c.prop(ui, "import_expressions")
        c = l.column()
        c.enabled = char and char.basis and char.has_faces
        c.prop(ui, "alt_topo")
        if ui.alt_topo == "<Custom>":
            c.prop(ui, "alt_topo_obj")

        l.operator('charmorph.import_char', icon='ARMATURE_DATA')

        l.alignment = "CENTER"
        c = l.column(align=True)
        if utils.is_adult_mode():
            labels = ["Adult mode is on", "The character will be naked"]
        else:
            labels = ["Adult mode is off", "Default underwear will be added"]
        for text in labels:
            r = c.row()
            r.alignment = "CENTER"
            r.label(text=text)


classes = [OpReloadLib, OpImport, CHARMORPH_PT_Library]
