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
import bpy, bpy_extras

from . import yaml, morphing, materials, utils

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
    def poll(cls, context):
        return bool(morphing.morpher)

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
    m = morphing.morpher
    typ = []

    if m.L1:
        typ.append(m.L1)
        alt_name = m.char.types.get(m.L1, {}).get("title")
        if alt_name:
            typ.append(alt_name)

    return {
        "type":   typ,
        "morphs": {k: m.prop_get(k) for k in m.morphs_l2},
        "meta":   {k: m.meta_get(k) for k in m.meta_dict()},
        "materials": materials.prop_values()
    }

def mblab_to_charmorph(data):
    return {
        "morphs": {k:v*2-1 for k, v in data.get("structural", {}).items()},
        "materials": data.get("materialproperties", {}),
        "meta": {(k[10:] if k.startswith("character_") else k):v for k, v in data.get("metaproperties", {}).items() if not k.startswith("last_character_")},
        "type": data.get("type", []),
    }

def charmorph_to_mblab(data):
    return {
        "structural": {k:(v+1)/2 for k, v in data.get("morphs", {}).items()},
        "metaproperties": {k:v for sublist, v in (([("character_"+k), ("last_character_"+k)], v) for k, v in data.get("meta", {}).items()) for k in sublist},
        "materialproperties": data.get("materials"),
        "type": data.get("type", []),
    }

def load_morph_data(fn):
    with open(fn, "r") as f:
        if fn[-5:] == ".yaml":
            return yaml.safe_load(f)
        if fn[-5:] == ".json":
            return mblab_to_charmorph(json.load(f))
    return None

class OpExportJson(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "charmorph.export_json"
    bl_label = "Export morphs"
    bl_description = "Export current morphs to MB-Lab compatible json file"
    filename_ext = ".json"

    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return bool(morphing.morpher)

    def execute(self, context):
        with open(self.filepath, "w") as f:
            json.dump(charmorph_to_mblab(morphs_to_data()), f, indent=4, sort_keys=True)
        return {"FINISHED"}


class OpExportYaml(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "charmorph.export_yaml"
    bl_label = "Export morphs"
    bl_description = "Export current morphs to yaml file"
    filename_ext = ".yaml"

    filter_glob: bpy.props.StringProperty(default="*.yaml", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return bool(morphing.morpher)

    def execute(self, context):
        with open(self.filepath, "w") as f:
            yaml.dump(morphs_to_data(), f, Dumper=utils.MyDumper)
        return {"FINISHED"}

class OpImport(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    bl_idname = "charmorph.import"
    bl_label = "Import morphs"
    bl_description = "Import morphs from yaml or json file"
    bl_options = {"UNDO"}

    filter_glob: bpy.props.StringProperty(default="*.yaml;*.json", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return hasattr(context.window_manager, "chartype") and morphing.morpher

    def execute(self, _):
        data = load_morph_data(self.filepath)
        if data is None:
            self.report({'ERROR'}, "Can't recognize format")
            return {"CANCELLED"}

        typenames = data.get("type", [])
        if isinstance(typenames, str):
            typenames = [typenames]

        m = morphing.morpher
        typemap = {v["title"]:k for k, v in m.char.types.items() if "title" in v}
        m.lock()
        try:
            for name in (name for sublist in ([name, typemap.get(name)] for name in typenames) for name in sublist):
                if not name:
                    continue
                if m.set_L1(name):
                    break

            m.apply_morph_data(data, False)
        except:
            m.unlock()
            raise
        return {"FINISHED"}

classes = [OpImport, OpExportJson, OpExportYaml, CHARMORPH_PT_ImportExport]
