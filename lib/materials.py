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

import os, re, logging

import bpy  # pylint: disable=import-error

from .charlib import Character
from . import utils
from .. import prefs

logger = logging.getLogger(__name__)

colorspaces = {
    item.name for item in
    bpy.types.ColorManagedInputColorspaceSettings.bl_rna.properties.get("name").enum_items
}


def init_materials(obj, char: Character):
    load_materials(obj, char)
    load_textures(obj, char)


def load_materials(obj, char: Character):
    mtllist = char.materials
    if not mtllist:
        return
    if len(obj.data.materials) != len(mtllist):
        logger.error("Material count mismatch in %s: %d != %d", char, len(obj.data.materials), len(mtllist))
        return

    settings_tree = None

    def copy_material(mtl):
        mtl = mtl.copy()
        if not mtl.node_tree:
            return mtl

        nonlocal settings_tree
        for node in mtl.node_tree.nodes:
            if node.type == "GROUP" and node.name == "charmorph_settings" and node.node_tree:
                if not settings_tree:
                    settings_tree = node.node_tree.copy()
                node.node_tree = settings_tree
        return mtl

    ui = bpy.context.window_manager.charmorph_ui
    materials_to_load = set()
    load_ids = []
    adult_mode = prefs.is_adult_mode()

    for i, mtl_name in enumerate(mtllist):
        if not mtl_name:
            continue
        mtl = None
        if ui.material_local or ui.material_mode == "MS":
            mtl = bpy.data.materials.get(mtl_name)
        if mtl:
            if ui.material_mode != "MS":
                mtl = copy_material(mtl)
            obj.data.materials[i] = mtl
        elif "_censor" not in mtl_name or not adult_mode:
            materials_to_load.add(mtl_name)
            load_ids.append(i)

    if materials_to_load:
        materials_to_load = list(materials_to_load)
        with bpy.data.libraries.load(char.path(char.material_lib)) as (_, data_to):
            data_to.materials = materials_to_load.copy()
        material_dict = {}
        for i, mtl in enumerate(data_to.materials):
            material_dict[materials_to_load[i]] = mtl
        for i in load_ids:
            obj.data.materials[i] = material_dict.get(mtllist[i])

    if adult_mode:
        for i in range(len(mtllist) - 1, 0, -1):
            if "_censor" in mtllist[i]:
                obj.data.materials.pop(index=i)


def is_udim(s: str):
    return "<UDIM>" in s or "<UVTILE>" in s


def ud_to_regex(s: str):
    return re.compile(re.escape(s).replace("<UDIM>", r"\d+").replace("<UVTILE>", r"u\d+_v\d+"))


# Returns a dictionary { texture_short_name: (filename, texture_settings)
def load_texdir(path, settings: dict) -> tuple[dict[str, tuple[str, str]], dict]:
    if not os.path.exists(path):
        return {}, settings
    settings = settings.copy()
    settings.update(utils.parse_file(os.path.join(path, "settings.yaml"), utils.load_yaml, {}))
    default_setting = settings.get("*")
    ud_map = [(ud_to_regex(s), s) for s in settings.keys() if is_udim(s)]

    result = {}
    for item in os.listdir(path):
        name, ext = os.path.splitext(item)
        full_path = os.path.join(path, item)
        if ext == ".yaml" or not os.path.isfile(full_path):
            continue
        for regex, val in ud_map:
            if regex.fullmatch(name):
                name = val
                full_path = os.path.join(path, val + ext)
                break
        old = result.get(name)
        if old is not None:
            if os.path.splitext(old[0])[1] != ext:
                logger.error("different extensions for texture %s at %s", name, path)
            continue
        result[name] = (full_path, settings.get(name, default_setting))

    return result, settings


# Returns a dictionary { texture_short_name: tuple(filename, texture_full_name, texture_settings) }
def load_texmap(char: Character, tex_set) -> dict[str, tuple[str, str, str]]:
    result = {}
    char_texes, settings = load_texdir(char.path("textures"), {})

    for k, v in load_texdir(char.lib.path("textures"), {})[0].items():
        if k not in char_texes:
            result[k] = (v[0], "charmorph--" + k, v[1])

    for k, v in char_texes.items():
        result[k] = (v[0], f"charmorph-{char}-{k}", v[1])

    if tex_set and tex_set != "/":
        for k, v in load_texdir(char.path("textures", tex_set), settings)[0].items():
            result[k] = (v[0], f"charmorph-{char}-{tex_set}-{k}", v[1])
    return result


def tex_try_names(char, tex_set, names):
    for name in names:
        if name.startswith("tex_"):
            name = name[4:]
        if tex_set and tex_set != "/":
            yield f"charmorph-{char}-{tex_set}-{name}"
        yield f"charmorph-{char}-{name}"
        yield "charmorph--" + name


# Currently only colorspace settings are supported
def apply_tex_settings(img, settings):
    if not settings:
        return
    if settings in colorspaces:
        img.colorspace_settings.name = settings
        return
    if settings == "Non-Color":
        if "Linear" in colorspaces:
            img.colorspace_settings.name = "Linear"
        img.colorspace_settings.is_data = True
        return
    logger.error("Color settings %s is not available!", settings)
    if settings != "sRGB" and "Linear" in colorspaces:
        img.colorspace_settings.name = "Linear"


def texture_max_res(ui):
    val = ui.tex_downscale
    if val == "UL":
        return 1024 * 1024
    return 1024 * int(val[0])


def load_textures(obj, char):
    if not obj.data.materials:
        return

    ui = bpy.context.window_manager.charmorph_ui
    texmap = None

    groups = set()

    def scan_nodes(nodes):
        nonlocal texmap
        for node in nodes:
            if char.recurse_materials and node.type == "GROUP" and node.node_tree.name not in groups:
                groups.add(node.node_tree.name)
                scan_nodes(node.node_tree.nodes.values())
            if node.type != "TEX_IMAGE":
                continue
            img = None
            if ui.material_local or ui.material_mode in ["MS", "TS"]:
                for name in tex_try_names(char.name, ui.tex_set, [node.name, node.label]):
                    img = bpy.data.images.get(name)
                    if img is not None:
                        break

            if img is None:
                if texmap is None:
                    texmap = load_texmap(char, ui.tex_set)

                img_tuple = None
                for name in [node.name, node.label]:
                    if name.startswith("tex_"):
                        name = name[4:]
                    else:
                        continue
                    img_tuple = texmap.get(name)
                    if img_tuple is not None:
                        break
                if img_tuple is not None:
                    img = bpy.data.images.load(img_tuple[0], check_existing=True)
                    if is_udim(img_tuple[0]):
                        img.source = 'TILED'
                    img.name = img_tuple[1]
                    apply_tex_settings(img, img_tuple[2])
                    if not img.has_data:
                        img.reload()
                    max_res = texture_max_res(ui)
                    width, height = img.size
                    if width > max_res or height > max_res:
                        logger.debug("resizing image %s", img_tuple[0])
                        img.scale(min(width, max_res), min(height, max_res))

            if img is not None:
                node.image = img

    for mtl in obj.data.materials:
        if not mtl or not mtl.node_tree:
            continue
        scan_nodes(mtl.node_tree.nodes.values())


def get_props(obj):
    if not obj.data.materials:
        return {}
    colors = []
    values = []
    groups = set()

    def scan_nodes(data_type, name, nodes):
        for node in nodes:
            if node.type == "GROUP" and node.name == "charmorph_settings" and node.node_tree.name not in groups:
                groups.add(node.node_tree.name)
                scan_nodes(1, node.node_tree.name, node.node_tree.nodes.values())
            if node.label == "":
                continue
            if node.type == "RGB" and not node.name.startswith("RGB."):
                colors.append((node.name, (data_type, name)))
            elif node.type == "VALUE":
                values.append((node.name, (data_type, name)))

    for mtl in obj.data.materials:
        if not mtl or not mtl.node_tree:
            continue
        scan_nodes(0, mtl.name, mtl.node_tree.nodes.values())
    return dict(colors + values)


tree_types = (
    lambda name: bpy.data.materials[name].node_tree,
    lambda name: bpy.data.node_groups[name]
)


class Materials:
    props: dict = {}

    def __init__(self, obj):
        if obj:
            self.props = get_props(obj)

    def as_dict(self):
        return {k: (list(v.default_value) if v.node.type == "RGB" else v.default_value) for k, v in self.get_node_outputs()}

    def get_node_outputs(self):
        return ((k, self.get_node_output(k, v)) for k, v in self.props.items())

    def get_node_output(self, node_name: str, tree_data: tuple[str, str] = None):
        try:
            if tree_data is None:
                tree_data = self.props[node_name]
            return tree_types[tree_data[0]](tree_data[1]).nodes[node_name].outputs[0]
        except KeyError:
            return None

    def apply(self, data):
        if not data:
            return
        if isinstance(data, dict):
            data = data.items()
        for k, v in data:
            prop = self.get_node_output(k)
            if not prop:
                continue
            if prop.node.type == "RGB":
                prop.default_value = utils.parse_color(v)
            else:
                prop.default_value = v
