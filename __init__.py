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

import logging
import bpy # pylint: disable=import-error

from . import library, morphing, randomize, file_io, materials, fitting, hair, finalize, rigify, pose, cmedit
from .lib import charlib

logger = logging.getLogger(__name__)

bl_info = {
    "name": "CharMorph",
    "author": "Michael Vigovsky",
    "version": (0, 2, 9),
    "blender": (2, 83, 0),
    "location": "View3D > Tools > CharMorph",
    "description": "Character creation and morphing, cloth fitting and rigging tools",
    'wiki_url': "",
    'tracker_url': 'https://github.com/Upliner/CharMorph/issues',
    "category": "Characters"
}

owner = object()

class VIEW3D_PT_CharMorph(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_CharMorph"
    bl_label = "CharMorph " + ".".join(str(item) for item in bl_info["version"])
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"
    bl_order = 1

    def draw(self, context):
        pass

class CharMorphPrefs(bpy.types.AddonPreferences):
    bl_idname = "CharMorph"

    adult_mode: bpy.props.BoolProperty(
        name="Adult mode",
        description="No censors, enable adult assets (genitails, pubic hair)",
    )
    tex_res: bpy.props.EnumProperty(
        name="Texture res",
        description="Limit texture resolution to avoid memory overflow",
        default="4K",
        items=[("1K", "1K", ""), ("2K", "2K", ""), ("4K", "4K", ""), ("UL", "Unlimited", "")]
    )

    def draw(self, _):
        self.layout.prop(self, "adult_mode")
        self.layout.prop(self, "tex_res")

def on_select_object():
    if morphing.bad_object():
        morphing.del_charmorphs()
    obj = bpy.context.object
    if obj is None:
        return
    ui = bpy.context.window_manager.charmorph_ui

    if obj is morphing.last_object:
        return

    if obj.type == "MESH":
        asset = None
        if (obj.parent and obj.parent.type == "MESH" and
                "charmorph_fit_id" in obj.data and
                "charmorph_template" not in obj.data):
            asset = obj
            obj = obj.parent
        if asset:
            ui.fitting_char = obj
            ui.fitting_asset = asset
        elif charlib.obj_char(obj).name:
            ui.fitting_char = obj
        else:
            ui.fitting_asset = obj

    if obj is morphing.last_object:
        return

    morphing.create_charmorphs(obj)

@bpy.app.handlers.persistent
def load_handler(_):
    morphing.del_charmorphs()
    on_select_object()

@bpy.app.handlers.persistent
def select_handler(_):
    on_select_object()

classes = [None, CharMorphPrefs, VIEW3D_PT_CharMorph]

uiprops = [bpy.types.PropertyGroup]

for module in [library, morphing, randomize, file_io, materials, fitting, hair, finalize, rigify, pose]:
    classes.extend(module.classes)
    if hasattr(module, "UIProps"):
        uiprops.append(module.UIProps)

CharMorphUIProps = type("CharMorphUIProps", tuple(uiprops), {})

classes[0] = CharMorphUIProps

class_register, class_unregister = bpy.utils.register_classes_factory(classes)

def register():
    logger.debug("Charmorph register")
    charlib.load_library()
    class_register()
    bpy.types.WindowManager.charmorph_ui = bpy.props.PointerProperty(type=CharMorphUIProps, options={"SKIP_SAVE"})

    bpy.msgbus.subscribe_rna(
        owner=owner,
        key=(bpy.types.LayerObjects, "active"),
        args=(),
        notify=on_select_object)

    bpy.app.handlers.load_post.append(load_handler)
    bpy.app.handlers.undo_post.append(select_handler)
    bpy.app.handlers.redo_post.append(select_handler)
    bpy.app.handlers.depsgraph_update_post.append(select_handler)

    cmedit.register()

def unregister():
    logger.debug("Charmorph unregister")
    cmedit.unregister()

    for hlist in bpy.app.handlers:
        if not isinstance(hlist, list):
            continue
        for handler in hlist:
            if handler in (load_handler, select_handler):
                hlist.remove(handler)
                break

    bpy.msgbus.clear_by_owner(owner)
    del bpy.types.WindowManager.charmorph_ui
    morphing.del_charmorphs()

    class_unregister()

if __name__ == "__main__":
    register()
