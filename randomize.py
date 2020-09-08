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

import re, random, math
import bpy

from . import morphing

saved_props = None
saved_version = -1

class CHARMORPH_PT_Randomize(bpy.types.Panel):
    bl_label = "Randomize"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 3

    @classmethod
    def poll(cls, context):
        global saved_props
        if context.window_manager.charmorph_ui.randomize_mode != "RL1":
            saved_props = None
        if not hasattr(context.window_manager,'charmorphs'):
            return False
        if context.window_manager.charmorphs.version != saved_version:
            saved_props = None
        return True

    def draw(self, context):
        ui = context.window_manager.charmorph_ui
        self.layout.prop(ui, "randomize_mode")

        col = self.layout.column(align=True)
        col.label(text="What to randomize:")
        col.prop(ui, "randomize_morphs")
        col.prop(ui, "randomize_mats")
        col.prop(ui, "randomize_incl")
        col.prop(ui, "randomize_excl")

        self.layout.separator()
        if ui.randomize_mode=="SEG":
            self.layout.prop(ui, "randomize_segs")
        else:
            self.layout.prop(ui, "randomize_strength")
        self.layout.operator('charmorph.randomize')

def save_props(cm):
    global saved_props
    if cm.version == saved_version:
        return
    saved_props = {}
    for prop in dir(cm):
        if prop.startswith("prop_"):
            saved_props[prop[5:]] = getattr(cm, prop)

class OpRandomize(bpy.types.Operator):
    bl_idname = "charmorph.randomize"
    bl_label = "Randomize"

    @classmethod
    def poll(cls, context):
        return hasattr(context.window_manager, 'charmorphs')

    def execute(self, context):
        global saved_version
        scn = context.window_manager
        ui = scn.charmorph_ui
        cm = scn.charmorphs
        if ui.randomize_mode == "RL1":
            save_props(cm)
        incl = re.compile(ui.randomize_incl)
        excl = re.compile(ui.randomize_excl)
        if ui.randomize_morphs:
            morphing.asset_lock = True
            for prop in dir(cm):
                if not prop.startswith("prop_"):
                    continue
                propname = prop[5:]
                if excl.search(propname) or not incl.search(propname):
                    continue
                if ui.randomize_mode == "OVR":
                    morphing.reset_meta(cm)
                if ui.randomize_mode == "SEG":
                    val = (math.floor((getattr(cm, prop)+1) * ui.randomize_segs / 2) + random.random()) * 2 / ui.randomize_segs - 1
                else:
                    val = (ui.randomize_strength * (random.random() * 2 - 1))
                if ui.randomize_mode == "RL1":
                    val += saved_props.get(propname, 0)
                elif ui.randomize_mode == "RL2":
                    val += getattr(cm, prop)
                setattr(cm, prop, val)
            morphing.asset_lock = False
            morphing.refit_assets()
        if ui.randomize_mode == "RL1":
            saved_version = cm.version
        return {"FINISHED"}

classes = [OpRandomize, CHARMORPH_PT_Randomize]
