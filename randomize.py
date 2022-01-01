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

class WhatToProps:
    randomize_morphs: bpy.props.BoolProperty(
        name="Morphs", default=True,
        description="Randomize morphs")
    randomize_mats: bpy.props.BoolProperty(
        name="Materials", default=False,
        description="Randomize materials")
    randomize_incl: bpy.props.StringProperty(
        name="Incl. regex")
    randomize_excl: bpy.props.StringProperty(
        name="Excl. regex")

class UIProps(WhatToProps):
    randomize_mode: bpy.props.EnumProperty(
        name="Mode",
        default="RL1",
        items=[
            ("OVR", "Overwrite current", "Overwrite current morphing"),
            ("RL1", "Relative to non-random", "Relative to last hand-edited morphing"),
            ("RL2", "Relative to current", "Relative to current morphing"),
            ("SEG", "Segmented", "Split every property to segments and remain within them"),
        ],
        description="Randomization mode (doesn't affect material colors)")
    randomize_func: bpy.props.EnumProperty(
        name="Function",
        default="REG",
        items=[
            ("REG", "Regular", "Regular random function"),
            ("GAU", "Gaussian", "Relative to last hand-edited morphing"),
        ],
        description="Use regular random func or gaussian")
    randomize_sigma: bpy.props.FloatProperty(
        name="Sigma", min=0, soft_max=1, default=0.1, precision=3, description="Gaussian sigma", subtype="FACTOR")
    randomize_segs: bpy.props.IntProperty(
        name="Segments",
        default=7,
        min=2, soft_max=25,
        description="Segment count for segmented randomization"
    )
    randomize_strength: bpy.props.FloatProperty(
        name="Strength", min=0, soft_max=1, default=0.2, precision=2, description="Randomization strength", subtype="FACTOR")

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
        m = morphing.morpher
        if not m:
            return False
        if m.version != saved_version:
            saved_props = None
        return True

    def draw(self, context):
        ui = context.window_manager.charmorph_ui
        self.layout.prop(ui, "randomize_mode")
        self.layout.prop(ui, "randomize_func")

        col = self.layout.column(align=True)
        col.label(text="What to randomize:")
        for prop in WhatToProps.__annotations__: # pylint: disable=no-member
            col.prop(ui, prop)

        self.layout.separator()
        if ui.randomize_func == "GAU":
            self.layout.prop(ui, "randomize_sigma")
        if ui.randomize_mode == "SEG":
            self.layout.prop(ui, "randomize_segs")
        else:
            self.layout.prop(ui, "randomize_strength")
        self.layout.operator('charmorph.randomize')

def save_props():
    global saved_props
    m = morphing.morpher
    if m.version == saved_version:
        return
    saved_props = {}
    for name, morph in m.morphs_l2.items():
        if morph is not None:
            saved_props[name] = m.prop_get(name)

class OpRandomize(bpy.types.Operator):
    bl_idname = "charmorph.randomize"
    bl_label = "Randomize"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return hasattr(context.window_manager, 'charmorphs') and morphing.morpher

    def execute(self, context): # pylint: disable=no-self-use
        global saved_version
        wm = context.window_manager
        ui = wm.charmorph_ui
        if ui.randomize_mode == "RL1":
            save_props()
        incl = re.compile(ui.randomize_incl)
        excl = re.compile(ui.randomize_excl)
        if ui.randomize_func == "GAU":
            random_func = lambda: random.gauss(0.5, ui.randomize_sigma)
        else:
            random_func = random.random
        m = morphing.morpher
        if ui.randomize_morphs:
            m.lock()
            try:
                for name, morph in m.morphs_l2.items():
                    if morph is None:
                        continue
                    if ui.randomize_excl and (excl.search(name) or not incl.search(name)):
                        continue
                    if ui.randomize_mode == "OVR":
                        m.reset_meta()
                    if ui.randomize_mode == "SEG":
                        val = (math.floor((m.prop_get(name)+1) * ui.randomize_segs / 2) + random_func()) * 2 / ui.randomize_segs - 1
                    else:
                        val = max(min((ui.randomize_strength * (random_func() * 2 - 1)), 1), -1)
                    if ui.randomize_mode == "RL1":
                        val += saved_props.get(name, 0)
                    elif ui.randomize_mode == "RL2":
                        val += m.prop_get(name)
                    if val < 0 and morph.min == 0:
                        val = -val
                    if m.clamp:
                        val = max(min(val, morph.max), morph.min)
                    m.prop_set(name, val)
            finally:
                m.unlock()
        if ui.randomize_mode == "RL1":
            saved_version = m.version
        return {"FINISHED"}

classes = [OpRandomize, CHARMORPH_PT_Randomize]
