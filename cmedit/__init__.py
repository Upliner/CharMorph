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

import logging

import bpy  # pylint: disable=import-error

from ..lib import utils
from . import assets, file_io, rigging, vg_calc, symmetry

logger = logging.getLogger(__name__)


class VIEW3D_PT_CMEdit(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_CMEdit"
    bl_label = "Character editing"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 2

    def draw(self, _):  # pylint: disable=no-self-use
        pass


class CMEditUIProps(bpy.types.PropertyGroup, vg_calc.UIProps, assets.UIProps):
    char_obj: bpy.props.PointerProperty(
        name="Char",
        description="Character mesh for rigging and asset fitting",
        type=bpy.types.Object,
        poll=utils.visible_mesh_poll,
    )


classes = [CMEditUIProps, VIEW3D_PT_CMEdit]

for module in assets, rigging, vg_calc, symmetry, file_io:
    classes.extend(module.classes)

register_classes, unregister_classes = bpy.utils.register_classes_factory(classes)


def register():
    register_classes()
    bpy.types.WindowManager.cmedit_ui = bpy.props.PointerProperty(type=CMEditUIProps, options={"SKIP_SAVE"})


def unregister():
    del bpy.types.WindowManager.cmedit_ui
    unregister_classes()


if __name__ == "__main__":
    register()
