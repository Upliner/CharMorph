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

from . import prefs
from .lib import charlib, morpher

logger = logging.getLogger(__name__)

if "undo_push" in dir(bpy.ops.ed):
    undo_push = bpy.ops.ed.undo_push
else:
    undo_push = None


class UndoHandler:
    dragging = False
    name = None
    value = None

    def __call__(self, name, value):
        self.value = value
        if not self.dragging:
            self.name = name
            self.dragging = True
            bpy.ops.charmorph.on_prop_change("INVOKE_DEFAULT")

    def finish(self):
        self.dragging = False
        if isinstance(self.value, float):
            self.value = f"{self.value:.3}"
        if self.name:
            result = f"{self.name}: {self.value}"
        else:
            result = OpMorphCharacter.bl_label
        self.name = None
        self.value = None
        return result


undo_handler = UndoHandler()


class Manager:
    last_object = None
    old_morpher = None

    def __init__(self):
        self.morpher = morpher.null_morpher

    def get_basis(self, data):
        return charlib.get_basis(data, self.morpher, True)

    def update_morpher(self, m: morpher.Morpher):
        self.old_morpher = None
        self.morpher = m
        self.last_object = m.core.obj

        ui = bpy.context.window_manager.charmorph_ui
        c = m.core.char

        if ui.rig not in c.armature:
            if c.default_armature:
                ui.rig = c.default_armature

        if not m.core.L1 and c.default_type:
            m.set_L1(c.default_type, False)

        if c.randomize_incl_regex is not None:
            ui.randomize_incl = c.randomize_incl_regex
        if c.randomize_excl_regex is not None:
            ui.randomize_excl = c.randomize_excl_regex

        ui.morph_category = "<None>"

        m.create_charmorphs_L2()

    def _get_old_storage(self, obj):
        for m in (self.morpher, self.old_morpher):
            if m and hasattr(m.core, "storage") and m.core.char is charlib.library.obj_char(obj):
                if m.core.storage:
                    return m.core.storage
        return None

    def _get_morpher(self, obj):
        return morpher.get(obj, self._get_old_storage(obj), undo_handler)

    def recreate_charmorphs(self):
        if not self.morpher:
            return
        self.morpher = self._get_morpher(self.morpher.core.obj)
        self.morpher.create_charmorphs_L2()

    def create_charmorphs(self, obj):
        self.last_object = obj
        if obj.type != "MESH":
            return
        if self.morpher.core.obj is obj and not self.morpher.error:
            return

        self.update_morpher(self._get_morpher(obj))

    def del_charmorphs(self):
        self.last_object = None
        self.morpher = morpher.null_morpher
        morpher.del_charmorphs_L2()

    def on_select(self):
        self.old_morpher = None
        if self.morpher is not morpher.null_morpher and not self.morpher.check_obj():
            logger.warning("Current morphing object is bad, resetting...")
            self.old_morpher = self.morpher
            self.del_charmorphs()
        if bpy.context.mode != "OBJECT":
            return
        obj = bpy.context.object
        if obj is None:
            return
        ui = bpy.context.window_manager.charmorph_ui

        if obj is self.last_object:
            return

        if obj.type == "MESH":
            asset = None
            if (obj.parent and obj.parent.type == "MESH"
                    and "charmorph_fit_id" in obj.data
                    and "charmorph_template" not in obj.data):
                asset = obj
                obj = obj.parent
            if asset:
                ui.fitting_char = obj
                ui.fitting_asset = asset
            elif charlib.library.obj_char(obj):
                ui.fitting_char = obj
            else:
                ui.fitting_asset = obj

        if obj is self.last_object:
            return

        self.create_charmorphs(obj)


manager = Manager()


class MorpherCheckOperator(bpy.types.Operator):
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and manager.morpher

    def exec(self, _ctx, _ui):
        return {"CANCELLED"}

    def execute(self, context):
        m = manager.morpher
        if not m.check_obj():
            self.report({"ERROR"}, "Invalid object selected")
            return {"CANCELLED"}
        return self.exec(context, context.window_manager.charmorph_ui)


def _get_undo_mode():
    lprefs = prefs.get_prefs()
    if not lprefs:
        return "S"
    return lprefs.preferences.undo_mode


class OpMorphCharacter(bpy.types.Operator):
    bl_idname = "charmorph.on_prop_change"
    bl_label = "Morph CharMorph character"
    bl_description = "Helper operator to make undo work with CharMorph"

    def modal(self, _, event):
        if not undo_handler.dragging:
            return {'FINISHED'}
        if event.value == 'RELEASE':
            msg = undo_handler.finish()
            if undo_push and _get_undo_mode() == "A":
                undo_push(message=msg)

        return {'PASS_THROUGH'}

    def invoke(self, context, _):
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


def register():
    if undo_push and _get_undo_mode() == "A":
        logger.debug("Advanced undo mode")
        OpMorphCharacter.bl_options = set()
    else:
        logger.debug("Simple undo mode")
        OpMorphCharacter.bl_options = {"UNDO"}
    bpy.utils.register_class(OpMorphCharacter)


def unregister():
    bpy.utils.unregister_class(OpMorphCharacter)


def update_undo_mode():
    unregister()
    register()


prefs.undo_update_hook = update_undo_mode
