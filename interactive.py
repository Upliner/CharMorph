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

import logging, numpy
import bpy, mathutils, gpu, gpu_extras # pylint: disable=import-error
from bpy_extras import view3d_utils    # pylint: disable=import-error, no-name-in-module

from .common import manager as mm


logger = logging.getLogger(__name__)


class OpMorphInteractive(bpy.types.Operator):
    bl_idname = "charmorph.interactive"
    bl_label = "Interactive morphing"
    bl_description = "Morph character in interactive mode"

    groups: dict
    group_tris: dict
    verts: numpy.ndarray

    def __init__(self):
        self.handler = None
        self.bvh = None
        self.select = None
        self.old_select = None
        self.shader = None

    @classmethod
    def poll(cls, _):
        return bool(mm.morpher)

    def modal(self, context, event):
        if event.type != 'MOUSEMOVE':
            if event.type == "INBETWEEN_MOUSEMOVE":
                return {'PASS_THROUGH'}
            self._finish()
            context.region.tag_redraw()
            return {'FINISHED'}
        args = (context.region, context.space_data.region_3d, (event.mouse_region_x, event.mouse_region_y))
        face = self.bvh.ray_cast(view3d_utils.region_2d_to_origin_3d(*args), view3d_utils.region_2d_to_vector_3d(*args))[2]
        self.select = None if face is None else self.groups.get(mm.morpher.core.obj.data.polygons[face].vertices[0])
        if self.select != self.old_select:
            #print(self.select)
            context.region.tag_redraw()
        self.old_select = self.select
        return {'PASS_THROUGH'}

    def draw_handler(self):
        item = self.group_tris.get(self.select)
        if not item:
            return

        if isinstance(item, list):
            item = gpu_extras.batch.batch_for_shader(
                self.shader, 'TRIS', {"pos": self.verts}, indices=item)
            self.group_tris[self.select] = item

        self.shader.bind()
        self.shader.uniform_float("color", (0, 0.5, 1, 0.3))
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.depth_mask_set(True)
        item.draw(self.shader)
        gpu.state.depth_mask_set(False)

    def _finish(self):
        print("handler removed")
        bpy.types.SpaceView3D.draw_handler_remove(self.handler, 'WINDOW')
        self.handler = None

    def execute(self, context):
        if self.handler:
            return {"CANCELLED"}
        obj = mm.morpher.core.obj
        self.shader = gpu.shader.from_builtin('3D_UNIFORM_COLOR')
        self.bvh = mathutils.bvhtree.BVHTree.FromObject(obj, context.evaluated_depsgraph_get())
        self.verts = mm.morpher.core.get_final_alt_topo().astype(numpy.float32)

        self.groups = {}
        for v in obj.data.vertices:
            if len(v.groups) == 1:
                self.groups[v.index] = v.groups[0].group

        self.group_tris = {}
        obj.data.calc_loop_triangles()
        for tri in obj.data.loop_triangles:
            g = self.groups.get(tri.vertices[0])
            if not g:
                continue
            if self.groups.get(tri.vertices[1]) == g and self.groups.get(tri.vertices[2]) == g:
                arr = self.group_tris.get(g, [])
                self.group_tris[g] = arr
                arr.append(tri.vertices)

        context.window_manager.modal_handler_add(self)
        self.handler = bpy.types.SpaceView3D.draw_handler_add(self.draw_handler, (), 'WINDOW', 'POST_VIEW')
        print("interactive started")

        return {'RUNNING_MODAL'}

classes = [OpMorphInteractive]
