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
# Copyright (C) 2021 Michael Vigovsky

import os, re, numpy
import bpy, bpy_extras # pylint: disable=import-error
import idprop          # pylint: disable=import-error

from . import yaml, rigging, utils

def np_particles_data(particles):
    cnt = numpy.empty(len(particles), dtype=numpy.uint8)
    total = 0
    mx = 1
    for i, p in enumerate(particles):
        c = len(p.hair_keys)-1
        cnt[i] = c
        total += c
        if c > mx:
            mx = c

    data = numpy.empty((total, 3), dtype=numpy.float32)
    tmp = numpy.empty(mx*3+3, dtype=numpy.float32)
    i = 0
    for p in particles:
        t2 = tmp[:len(p.hair_keys)*3]
        p.hair_keys.foreach_get("co_local", t2)
        t2 = t2[3:].reshape((-1, 3))
        data[i:i+len(t2)] = t2
        i += len(t2)
    return {"cnt":cnt, "data":data}

class OpHairExport(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.hair_export"
    bl_label = "Export hair"
    bl_description = "Export hairstyle to .npz file"
    filename_ext = ".npz"

    filter_glob: bpy.props.StringProperty(default="*.npz", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.object and context.object.particle_systems.active

    def execute(self, context):
        psys = context.object.particle_systems.active
        is_global = psys.is_global_hair
        override = context.copy()
        if not is_global:
            bpy.ops.particle.disconnect_hair(override)
        numpy.savez_compressed(self.filepath, **np_particles_data(psys.particles))
        if not is_global:
            bpy.ops.particle.connect_hair(override)
        return {"FINISHED"}

class OpVgExport(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.vg_export"
    bl_label = "Export VGs"
    bl_description = "Export vertex groups matching regex"
    filename_ext = ".npz"

    filter_glob: bpy.props.StringProperty(default="*.npz", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH"

    def execute(self, context):
        r = re.compile(context.window_manager.cmedit_ui.vg_regex)
        m = {}
        arr = []
        dt = numpy.uint8
        names = bytearray()
        for vg in context.object.vertex_groups:
            if r.search(vg.name):
                m[vg.index] = len(arr)
                arr.append([])
                if len(names) > 0:
                    names.append(0)
                names.extend(vg.name.encode("utf-8"))

        for v in context.object.data.vertices:
            for g in v.groups:
                i = m.get(g.group)
                if i is None:
                    continue
                a = arr[i]
                a.append((v.index, g.weight))
                if len(a) > 255:
                    dt = numpy.uint16

        cnt = numpy.empty(len(arr), dtype=dt)
        total = 0
        for i, a in enumerate(arr):
            cnt[i] = len(a)
            total += len(a)

        idx = numpy.empty(total, dtype=numpy.uint16)
        weights = numpy.empty(total, dtype=numpy.float64)

        i = 0
        for a in arr:
            for t in a:
                idx[i] = t[0]
                weights[i] = t[1]
                i += 1

        numpy.savez_compressed(self.filepath, names=names, cnt=cnt, idx=idx, weights=weights)

        return {"FINISHED"}

class OpVgImport(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    bl_idname = "cmedit.vg_import"
    bl_label = "Import VGs"
    bl_description = "Import vertex groups from npz file"
    bl_options = {"UNDO"}

    filter_glob: bpy.props.StringProperty(default="*.npz", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH" and context.mode == "OBJECT"

    def execute(self, context):
        rigging.import_vg(context.object, self.filepath, context.window_manager.cmedit_ui.vg_overwrite)
        return {"FINISHED"}

class OpBoneExport(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.bones_export"
    bl_label = "Export Bone settings"
    bl_description = 'Export bone settings (offsets, roll). Must be in edit mode when exporting in other than "Props only" mode'
    filename_ext = ".yaml"

    filter_glob: bpy.props.StringProperty(default="*.yaml", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "ARMATURE" and (context.mode == "EDIT_ARMATURE" or context.window_manager.cmedit_ui.rig_bones_mode == "N")

    def execute(self, context):
        result = {}
        mode = context.window_manager.cmedit_ui.rig_bones_mode
        a = context.object.data
        if context.mode == "EDIT_ARMATURE":
            bones = a.edit_bones
        else:
            bones = a.bones
        for b in bones:
            bd = {}
            for k, v in b.items():
                if k.startswith("charmorph_"):
                    if isinstance(v, idprop.types.IDPropertyArray):
                        v = list(v)
                    bd[k[10:]] = v

            if "axis_x" not in bd and "axis_z" not in bd:
                if mode == "X":
                    bd["axis_x"] = list(b.x_axis)
                elif mode == "Z":
                    bd["axis_z"] = list(b.z_axis)

            if len(bd) > 0:
                result[b.name] = bd

        with open(self.filepath, "w") as f:
            yaml.dump(result, f, Dumper=utils.MyDumper)

        return {"FINISHED"}

class OpMorphsExport(bpy.types.Operator):
    bl_idname = "cmedit.morphs_export"
    bl_label = "Export L2 Morphs"
    bl_description = "Export L2 all morphs from shape keys to a specified directory"

    directory: bpy.props.StringProperty(
        name="Directory",
        description="Directory for exporting morphs",
        maxlen=1024,
        subtype='DIR_PATH',
    )

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH"

    def execute(self, context):
        m = context.object.data
        if not m.shape_keys or not m.shape_keys.key_blocks or not m.shape_keys.reference_key:
            self.report({"ERROR"}, "No shape keys!")
            return {"CANCELLED"}
        keys = {}

        for sk in m.shape_keys.key_blocks:
            if sk.name.startswith("L2__"):
                name = sk.name[4:] + ".npz"
                keys[name] = sk
                if os.path.exists(os.path.join(self.directory, name)):
                    self.report({"ERROR"}, name + ".npz already exists!")
                    return {"CANCELLED"}

        rk = m.shape_keys.reference_key
        basis = numpy.empty(len(rk.data)*3)
        morphed = numpy.empty(len(rk.data)*3)
        rk.data.foreach_get("co", basis)

        if context.window_manager.cmedit_ui.morph_float_precicion == "64":
            dtype = numpy.float64
        else:
            dtype = numpy.float32

        for name, sk in keys.items():
            sk.data.foreach_get("co", morphed)
            morphed -= basis
            m2 = morphed.reshape(-1, 3)
            idx = m2.any(1).nonzero()[0]
            numpy.savez(os.path.join(self.directory, name), idx=idx.astype(dtype=numpy.uint16), delta=m2[idx].astype(dtype=dtype, casting="same_kind"))

        return {"CANCELLED"}

    def invoke(self, context, _event):
        if not self.directory:
            self.directory = os.path.dirname(context.blend_data.filepath)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class CHARMORPH_PT_FileIO(bpy.types.Panel):
    bl_label = "File I/O"
    bl_parent_id = "VIEW3D_PT_CMEdit"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"
    bl_order = 3

    def draw(self, context):
        ui = context.window_manager.cmedit_ui
        l = self.layout
        l.operator("cmedit.hair_export")
        l.separator()
        l.prop(ui, "rig_bones_mode")
        l.operator("cmedit.bones_export")
        l.separator()
        l.prop(ui, "vg_regex")
        l.prop(ui, "vg_overwrite")
        l.operator("cmedit.vg_export")
        l.operator("cmedit.vg_import")
        l.separator()
        l.prop(ui, "morph_float_precicion")
        l.operator("cmedit.morphs_export")

classes = [OpHairExport, OpVgExport, OpVgImport, OpBoneExport, OpMorphsExport, CHARMORPH_PT_FileIO]
