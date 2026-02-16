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
# Material preset engine for wardrobe assets

import logging

logger = logging.getLogger(__name__)


class AssetMaterialManager:
    """Manages material presets for a fitted asset.

    Preset config format in asset config.yaml:
        material_presets:
          default:
            label: "Creme/Ivory"
            materials:
              cloth_main:
                Base Color: [0.95, 0.90, 0.82, 1.0]
                Roughness: 0.5
          gold:
            label: "Gold"
            materials:
              cloth_main:
                Base Color: [0.82, 0.68, 0.38, 1.0]
                Metallic: 0.85
    """

    def __init__(self, asset_obj, presets_config):
        self.obj = asset_obj
        self.presets = presets_config or {}

    def get_preset_names(self):
        return list(self.presets.keys())

    def apply_preset(self, preset_name):
        if preset_name not in self.presets:
            logger.warning("Material preset '%s' not found", preset_name)
            return False

        preset = self.presets[preset_name]
        materials_data = preset.get("materials", {})

        for mtl in self.obj.data.materials:
            if not mtl or not mtl.node_tree:
                continue

            mtl_conf = self._match_material(mtl.name, materials_data)
            if not mtl_conf:
                continue

            self._apply_to_material(mtl, mtl_conf)

        return True

    def _match_material(self, mtl_name, materials_data):
        """Match a Blender material name to preset config key."""
        if mtl_name in materials_data:
            return materials_data[mtl_name]
        # Try without .001 suffix
        base = mtl_name.rsplit(".", 1)[0] if "." in mtl_name else mtl_name
        if base in materials_data:
            return materials_data[base]
        # Try partial match
        for key, conf in materials_data.items():
            if key in mtl_name:
                return conf
        return None

    def _apply_to_material(self, mtl, conf):
        """Apply preset values to a single material's Principled BSDF."""
        for node in mtl.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                self._apply_to_principled(node, conf)
            elif node.type == "GROUP":
                self._apply_to_group(node, conf)

    def _apply_to_principled(self, node, conf):
        for input_name, value in conf.items():
            inp = node.inputs.get(input_name)
            if inp is None:
                continue
            if isinstance(value, list):
                if len(value) == 3:
                    value = value + [1.0]
                inp.default_value = value
            else:
                inp.default_value = value

    def _apply_to_group(self, node, conf):
        """Apply values to matching inputs on a node group."""
        for input_name, value in conf.items():
            inp = node.inputs.get(input_name)
            if inp is None:
                continue
            if isinstance(value, list):
                if len(value) == 3:
                    value = value + [1.0]
                inp.default_value = value
            else:
                inp.default_value = value
