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

import json, yaml
import bpy, bpy_extras

from . import morphing

class CHARMORPH_PT_ImportExport(bpy.types.Panel):
    bl_label = "Import/Export"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 5

    @classmethod
    def poll(cls, context):
        return hasattr(context.scene,'charmorphs')

    def draw(self, context):
        ui = context.scene.charmorph_ui

        self.layout.label(text = "Export format:")
        self.layout.prop(ui, "export_format", expand=True)
        self.layout.separator()
        col = self.layout.column(align=True)
        if ui.export_format=="json":
            col.operator("charmorph.export_json")
        elif ui.export_format=="yaml":
            col.operator("charmorph.export_yaml")
        col.operator("charmorph.import")

def morphs_to_data(cm):
    morphs={}
    meta={}
    for prop in dir(cm):
        if prop.startswith("prop_"):
            morphs[prop[5:]] = getattr(cm, prop)
        elif prop.startswith("meta_"):
            meta[prop[5:]] = getattr(cm, prop)
    return {"morphs":morphs,"meta": meta}

class OpExportJson(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "charmorph.export_json"
    bl_label = "Export morphs"
    bl_description = "Export current morphs to MB-Lab compatible json file"
    filename_ext = ".json"

    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return hasattr(context.scene, 'charmorphs')

    def execute(self, context):
        with open(self.filepath, "w") as f:
            json.dump(morphing.charmorph_to_mblab(morphs_to_data(context.scene.charmorphs)),f, indent=4, sort_keys=True)
        return {"FINISHED"}

class OpExportYaml(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "charmorph.export_yaml"
    bl_label = "Export morphs"
    bl_description = "Export current morphs to yaml file"
    filename_ext = ".yaml"

    filter_glob: bpy.props.StringProperty(default="*.yaml", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return hasattr(context.scene, 'charmorphs')

    def execute(self, context):
        with open(self.filepath, "w") as f:
            yaml.dump(morphs_to_data(context.scene.charmorphs),f)
        return {"FINISHED"}

class OpImport(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    bl_idname = "charmorph.import"
    bl_label = "Import morphs"
    bl_description = "Import morphs from yaml or json file"
    bl_options = {"UNDO"}

    filter_glob: bpy.props.StringProperty(default="*.yaml;*.json", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return hasattr(context.scene, 'charmorphs')

    def execute(self, context):
        data = morphing.load_morph_data(self.filepath)
        if data == None:
            self.report({'ERROR'}, "Can't recognize format")
            return {"CANCELLED"}

        morphing.apply_morph_data(context.scene.charmorphs, data, False)
        return {"FINISHED"}

classes = [OpImport, OpExportJson, OpExportYaml, CHARMORPH_PT_ImportExport]
