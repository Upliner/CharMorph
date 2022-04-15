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

import os, logging, collections
import bpy # pylint: disable=import-error

from .lib import charlib, utils

props = None

logger = logging.getLogger(__name__)

def init_materials(obj, char):
    load_materials(obj, char)
    load_textures(obj, char)

def load_materials(obj, char):
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
    adult_mode = utils.is_adult_mode()

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
        elif not "_censor" in mtl_name or not adult_mode:
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
        for i in range(len(mtllist)-1, 0, -1):
            if "_censor" in mtllist[i]:
                obj.data.materials.pop(index=i)

# Returns a dictionary { texture_short_name: (filename, texture_settings)
def load_texdir(path, settings:dict):
    if not os.path.exists(path):
        return {}
    settings = settings.copy()
    settings.update(charlib.parse_file(os.path.join(path, "settings.yaml"), charlib.load_yaml, {}))
    default_setting = settings.get("*")

    result = {}
    for item in os.listdir(path):
        name = os.path.splitext(item)[0]
        full_path = os.path.join(path, item)
        if name[1] == ".yaml" or not os.path.isfile(full_path):
            continue
        if name in result:
            logger.error("different extensions for texture %s at %s", name, path)
        result[name] = (full_path, settings.get(name, default_setting))

    return result, settings

# Returns a dictionary { texture_short_name: tuple(filename, texture_full_name, texture_settings) }
def load_texmap(char, tex_set):
    result = {}
    char_texes, settings = load_texdir(charlib.char_file(char, "textures"), {})

    for k, v in load_texdir(os.path.join(charlib.data_dir, "textures"), {})[0].items():
        if k not in char_texes:
            result[k] = (v[0], "charmorph--" + k, v[1])

    for k, v in char_texes.items():
        result[k] = (v[0], f"charmorph-{char}-{k}", v[1])

    if tex_set and tex_set != "/":
        for k, v in load_texdir(charlib.char_file(char, os.path.join("textures", tex_set)), settings)[0].items():
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

def apply_tex_settings(img, settings):
    if not settings:
        return
    img.colorspace_settings.name = settings # Currently only colorspace settings are supported

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
                    texmap = load_texmap(char.name, ui.tex_set)

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
        return None
    colors = []
    values = []
    groups = set()
    def scan_nodes(nodes):
        for node in nodes:
            if node.type == "GROUP" and node.name == "charmorph_settings" and node.node_tree.name not in groups:
                groups.add(node.node_tree.name)
                scan_nodes(node.node_tree.nodes.values())
            if node.label == "":
                continue
            if node.type == "RGB" and not node.name.startswith("RGB."):
                colors.append((node.name, node.outputs[0]))
            elif node.type == "VALUE":
                values.append((node.name, node.outputs[0]))
    for mtl in obj.data.materials:
        if not mtl or not mtl.node_tree:
            continue
        scan_nodes(mtl.node_tree.nodes.values())
    return collections.OrderedDict(colors + values)

def update_props(obj):
    global props
    props = get_props(obj)
    return props

def prop_values():
    return {k: (list(v.default_value) if v.node.type == "RGB" else v.default_value) for k, v in props.items()}

def apply_props(data, mtl_props=None):
    if mtl_props is None:
        mtl_props = props
    if not data or not mtl_props:
        return
    for k, v in data.items():
        prop = mtl_props.get(k)
        if not prop:
            continue
        if prop.node.type == "RGB":
            mtl_props[k].default_value = utils.parse_color(v)
        else:
            mtl_props[k].default_value = v

class CHARMORPH_PT_Materials(bpy.types.Panel):
    bl_label = "Materials"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 6

    @classmethod
    def poll(cls, _):
        return bool(props)

    def draw(self, _):
        for prop in props.values():
            if prop.node:
                self.layout.prop(prop, "default_value", text=prop.node.label)

classes = [CHARMORPH_PT_Materials]
