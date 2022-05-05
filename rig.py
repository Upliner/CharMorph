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

from .lib import rigging, utils
from .morphing import manager as mm

logger = logging.getLogger(__name__)


def add_rig(ui):
    m = mm.morpher
    conf = m.core.char.armature.get(ui.rig)
    if not conf:
        raise rigging.RigException("Rig is not found")

    old_handler = m.rig_handler
    rig = m.add_rig(conf)
    try:
        bpy.context.view_layer.objects.active = rig
        rigger = m.run_rigger(ui.rig_manual_sculpt)

        if old_handler:
            old_handler.clear_weights()

        if m.core.alt_topo:
            m.fitter.transfer_weights(m.core.obj, conf.weights_npz)
        else:
            utils.import_vg(m.core.obj, conf.weights_npz, False)

        m.rig_handler.finalize(rigger)

        m.rig_handler.rig.data["charmorph_template"] =\
            m.core.char.name or m.core.obj.data.get("charmorph_template", "")
        m.rig_handler.rig.data["charmorph_rig_type"] = conf.name
        m.core.obj.data["charmorph_rig_type"] = conf.name

    except Exception:
        bpy.data.armatures.remove(rig.data)
        raise

    if old_handler:
        old_handler.delete_rig()
    return m.rig_handler.err


class OpRig(bpy.types.Operator):
    bl_idname = "charmorph.rig"
    bl_label = "Add rig"
    bl_description = "Add or update character rig"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and mm.morpher.core.char.armature

    def execute(self, context):
        t = utils.Timer()
        try:
            err = add_rig(context.window_manager.charmorph_ui)
            if err is not None:
                self.report({"ERROR"}, err)
        except rigging.RigException as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        t.time("rigging")
        mm.recreate_charmorphs()
        return {"FINISHED"}


class OpUnrig(bpy.types.Operator):
    bl_idname = "charmorph.unrig"
    bl_label = "Unrig"
    bl_description = "Remove all riging data from the character and all its assets so you can continue morphing it"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        if context.mode != "OBJECT":
            return False
        obj = mm.morpher.core.obj
        return obj and obj.find_armature()

    def execute(self, _):  # pylint: disable=no-self-use
        m = mm.morpher
        obj = m.core.obj
        handler = m.rig_handler

        old_rig = m.rig or obj.find_armature()
        if old_rig:
            if obj.parent is old_rig:
                utils.copy_transforms(obj, old_rig)
                utils.lock_obj(obj, False)

        if handler:
            handler.clear_weights()
            handler.delete_rig()
        elif old_rig:
            bpy.data.armatures.remove(old_rig.data)

        if "charmorph_rig_type" in obj.data:
            del obj.data["charmorph_rig_type"]

        mm.recreate_charmorphs()

        return {"FINISHED"}


class UIProps:
    rig: bpy.props.EnumProperty(
        name="Rig",
        items=lambda _ui, _ctx: [(name, rig.title, "") for name, rig in mm.morpher.core.char.armature.items()],
        description="Rigging options")
    rig_manual_sculpt: bpy.props.BoolProperty(
        name="Manual edit/sculpt",
        default=False,
        description="Enable it if you want changes outside CharMorph's morphing panel "
                    "(i.e. Blender's edit or sculpt mode) to affect character rig")


class CHARMORPH_PT_Rig(bpy.types.Panel):
    bl_label = "Rig"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 9

    @classmethod
    def poll(cls, context):
        return context.mode in ("OBJECT", "POSE") and mm.morpher

    def draw(self, context):
        l = self.layout
        ui = context.window_manager.charmorph_ui
        l.prop(ui, "rig")
        l.prop(ui, "rig_manual_sculpt")
        l.operator("charmorph.rig")
        l.operator("charmorph.unrig")


classes = [OpRig, OpUnrig, CHARMORPH_PT_Rig]
