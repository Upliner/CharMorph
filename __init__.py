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
import bpy  # pylint: disable=import-error
from bpy.types import Operator, PropertyGroup
from bpy.utils import register_class, unregister_class
from . import common, library, assets, hair, morphing, randomize, file_io, finalize, rig, rigify, pose, prefs, cmedit, toonify, addon_updater_ops
from .lib import charlib, file_preperation, materials
from .global_logger import setup_logger
from .about import bl_info as bl_info_about

# This is a special Bender value. I must be a literal and it must be in the module.
bl_info = {
    "name": "CharMorph",
    "author": "Michael Vigovsky",
    "version": (0, 4, 0),
    "blender": (4, 4, 0),
    "location": "View3D > Tools > CharMorph",
    "description": "Character creation and morphing, cloth fitting and rigging tools",
    'wiki_url': "",
    'tracker_url': 'https://github.com/Upliner/CharMorph/issues',
    "category": "Characters"
}
# Since we also have it defined in about.py, we need to make sure they match. Just in case you forget to update both.
if bl_info != bl_info_about:
    raise ValueError("The bl_info literal value defined in __init__.py does not match the one in 'about.py'.")


logger = setup_logger(log_level=logging.DEBUG)

VERSION_ANNEX = ""

owner = object()

class VIEW3D_PT_CharMorph(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_CharMorph"
    bl_label = "".join(("CharMorph ", ".".join(str(item) for item in bl_info["version"]), VERSION_ANNEX))
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"
    bl_order = 1

    def draw(self, _):
        pass


def on_select():
    common.manager.on_select()


@bpy.app.handlers.persistent
def undoredo_post(_context, _scene):
    common.manager.on_select(undoredo=True)


def subscribe_select_obj():
    bpy.msgbus.clear_by_owner(owner)
    bpy.msgbus.subscribe_rna(
        owner=owner,
        key=(bpy.types.LayerObjects, "active"),
        args=(),
        options={"PERSISTENT"},
        notify=on_select)


@bpy.app.handlers.persistent
def load_handler(_):
    subscribe_select_obj()
    common.manager.del_charmorphs()
    on_select()


@bpy.app.handlers.persistent
def select_handler(_):
    on_select()


# Define registration order
_MODULES = [
    library, 
    morphing, 
    randomize, 
    file_io, 
    assets, 
    hair, 
    rig, 
    rigify, 
    finalize, 
    pose,
    cmedit,
    #lib,
    prefs,
    common
    #toonify
]


# Create a new dynamic 'CharMorphUIProps' type that contains all UIProps from all modules
uiprops = [bpy.types.PropertyGroup]
for module in _MODULES:
    if hasattr(module, "UIProps"):
        uiprops.append(module.UIProps)
CharMorphUIProps = type("CharMorphUIProps", tuple(uiprops), {})


# The classes from this module. Ideally these should be in a separate module and registered the same was as all others
register_classes, unregister_classes = bpy.utils.register_classes_factory([
    CharMorphUIProps,
    VIEW3D_PT_CharMorph
])


def register():
    logger.info("Registering CharMorph add-on...")

    try:
        register_classes()

        for module in _MODULES:
            module.register()

        #addon_updater_ops.register(bl_info)  # Special case, because of the extra param

        charlib.library.load()

        bpy.types.WindowManager.charmorph_ui = bpy.props.PointerProperty(
            type=CharMorphUIProps, options={"SKIP_SAVE"}
        )
        subscribe_select_obj()

        bpy.app.handlers.load_post.append(load_handler)
        bpy.app.handlers.undo_post.append(undoredo_post)
        bpy.app.handlers.redo_post.append(undoredo_post)
        bpy.app.handlers.depsgraph_update_post.append(select_handler)

        logger.info("...successfully registered the CharMorph add-on.")
    except Exception as e:
        logger.error(f"Failed to register the CharMorph add-on.: {e}")
        unregister() 
        raise # Propagate the exception



def unregister():
    logger.info("Un-registering CharMorph add-on...")
    
    try:
        # Remove handlers
        for hlist in bpy.app.handlers:
            if not isinstance(hlist, list):
                continue
            for handler in [load_handler, select_handler, undoredo_post]:
                while handler in hlist:
                    hlist.remove(handler)

        # Clear the message bus
        bpy.msgbus.clear_by_owner(owner)

        # Delete custom properties
        if hasattr(bpy.types.WindowManager, "charmorph_ui"):
            del bpy.types.WindowManager.charmorph_ui

        # Unregister modules
        for module in reversed(_MODULES):
            module.unregister()


       # addon_updater_ops.unregister();

        # Unregister classes
        unregister_classes()

        # Clean up additional resources
        common.manager.del_charmorphs()

        logger.info("...successfully un-registered the CharMorph add-on.")
    except Exception as e:
        logger.error(f"Failed to un-register the CharMorph add-on.: {e}")
        raise # Propagate the exception

if __name__ == "__main__":
    register()
