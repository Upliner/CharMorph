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

import json
import bpy, bpy_extras # pylint: disable=import-error

from .lib import morphs, utils
from .morphing import manager as mm

class UIProps:
    export_format: bpy.props.EnumProperty(
        name="Format",
        description="Export format",
        default="yaml",
        items=[
            ("yaml", "CharMorph (yaml)", ""),
            ("json", "MB-Lab (json)", "")
        ])

class CHARMORPH_PT_ImportExport(bpy.types.Panel):
    bl_label = "Import/Export"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 5

    @classmethod
    def poll(cls, _):
        return bool(mm.morpher)

    def draw(self, context):
        ui = context.window_manager.charmorph_ui

        self.layout.label(text="Export format:")
        self.layout.prop(ui, "export_format", expand=True)
        self.layout.separator()
        col = self.layout.column(align=True)
        if ui.export_format == "json":
            col.operator("charmorph.export_json")
        elif ui.export_format == "yaml":
            col.operator("charmorph.export_yaml")
        col.operator("charmorph.import")

def morphs_to_data():
    m = mm.morpher
    typ = []

    if m.L1:
        typ.append(m.L1)
        alt_name = m.char.types.get(m.L1, {}).get("title")
        if alt_name:
            typ.append(alt_name)

    return {
        "type":   typ,
        "morphs": {m.name: m.core.prop_get(m.name) for m in m.core.morphs_l2 if m.name},
        "meta":   {k: m.meta_get(k) for k in m.core.char.morphs_meta},
        "materials": m.materials.as_dict()
    }

class OpExportJson(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "charmorph.export_json"
    bl_label = "Export morphs"
    bl_description = "Export current morphs to MB-Lab compatible json file"
    filename_ext = ".json"

    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    @classmethod
    def poll(cls, _):
        return bool(mm.morpher)

    def execute(self, _):
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(morphs.charmorph_to_mblab(morphs_to_data()), f, indent=4, sort_keys=True)
        return {"FINISHED"}

class OpExportYaml(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "charmorph.export_yaml"
    bl_label = "Export morphs"
    bl_description = "Export current morphs to yaml file"
    filename_ext = ".yaml"

    filter_glob: bpy.props.StringProperty(default="*.yaml", options={'HIDDEN'})

    @classmethod
    def poll(cls, _):
        return bool(mm.morpher)

    def execute(self, _):
        with open(self.filepath, "w", encoding="utf-8") as f:
            utils.dump_yaml(morphs_to_data(), f)
        return {"FINISHED"}

class OpImport(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    bl_idname = "charmorph.import"
    bl_label = "Import morphs"
    bl_description = "Import morphs from yaml or json file"
    bl_options = {"UNDO"}

    filter_glob: bpy.props.StringProperty(default="*.yaml;*.json", options={'HIDDEN'})

    @classmethod
    def poll(cls, _):
        return bool(mm.morpher)

    def execute(self, _):
        data = morphs.load_morph_data(self.filepath)
        if data is None:
            self.report({'ERROR'}, "Can't recognize format")
            return {"CANCELLED"}

        typenames = data.get("type", [])
        if isinstance(typenames, str):
            typenames = [typenames]

        m = mm.morpher
        typemap = {v["title"]:k for k, v in m.core.char.types.items() if "title" in v}
        for name in (name for sublist in ([name, typemap.get(name)] for name in typenames) for name in sublist):
            if not name:
                continue
            if m.set_L1(name, False):
                break

        m.apply_morph_data(data, False)
        return {"FINISHED"}

classes = [OpImport, OpExportJson, OpExportYaml, CHARMORPH_PT_ImportExport]
