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
# Copyright (C) 2021-2022 Michael Vigovsky

import os, re, json, numpy
import bpy, bpy_extras # pylint: disable=import-error
import idprop          # pylint: disable=import-error

from ..lib import yaml, rigging, utils

def np_particles_data(obj, particles):
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

    utils.np_matrix_transform(data, obj.matrix_world.inverted())
    return {"cnt":cnt, "data":data}

def export_hair(obj, psys_idx, filepath):
    pss = obj.particle_systems
    old_psys_idx = pss.active_index
    pss.active_index = psys_idx

    psys = pss[psys_idx]
    is_global = psys.is_global_hair
    override = {"object": obj}
    if not is_global:
        bpy.ops.particle.disconnect_hair(override)
    numpy.savez_compressed(filepath, **np_particles_data(obj, psys.particles))
    if not is_global:
        bpy.ops.particle.connect_hair(override)

    pss.active_index = old_psys_idx

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
        export_hair(context.object, context.object.particle_systems.active_index, self.filepath)
        return {"FINISHED"}

class OpHairImport(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    bl_idname = "cmedit.hair_import"
    bl_label = "Import hair"
    bl_description = "Load saved hairstyle from .npz file (will only work if particle and key counts are unchanged)"
    bl_options = {"UNDO"}

    filter_glob: bpy.props.StringProperty(default="*.npz", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.object and context.object.particle_systems.active

    def execute(self, context):
        z = numpy.load(self.filepath)
        utils.set_hair_points(context.object, z["cnt"], numpy.concatenate((((0, 0, 0),), z["data"])))
        return {"FINISHED"}

class DirExport(bpy.types.Operator):
    directory: bpy.props.StringProperty(
        name="Directory",
        description="Directory for exporting morphs",
        maxlen=1024,
        subtype='DIR_PATH',
    )

    def invoke(self, context, _event):
        if not self.directory:
            self.directory = os.path.dirname(context.blend_data.filepath)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class OpAllHairExport(DirExport):
    bl_idname = "cmedit.all_hair_export"
    bl_label = "Export all hair"
    bl_description = "Export all hairstyles to .npz files"

    @classmethod
    def poll(cls, context):
        return context.object

    def execute(self, context):
        for i, psys in enumerate(context.object.particle_systems):
            export_hair(context.object, i, os.path.join(self.directory, psys.name + ".npz"))
        return {"FINISHED"}

def float_dtype(context):
    return numpy.float64 if context.window_manager.cmedit_ui.morph_float_precicion == "64" else numpy.float32

def flatten(arr, dtype):
    return numpy.block([numpy.array(item, dtype=dtype) for item in arr])

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
        names, idx, weights = rigging.vg_weights_to_arrays(context.object, r.search)
        cnt = [len(i) for i in idx]

        numpy.savez_compressed(self.filepath,
            names=b'\0'.join(name.encode("utf-8") for name in names),
            cnt=numpy.array(cnt, dtype=numpy.uint16 if max(cnt) > 255 else numpy.uint8),
            idx=flatten(idx, numpy.uint16),
            weights=flatten(weights, float_dtype(context))
        )

        return {"FINISHED"}

class OpFaceExport(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.face_export"
    bl_label = "Export faces"
    bl_description = 'Export full face list to npy file'
    filename_ext = ".npy"

    filter_glob: bpy.props.StringProperty(default="*.npy", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH"

    def execute(self, context):
        numpy.save(self.filepath, numpy.array([f.vertices for f in context.object.data.polygons]).astype(dtype=numpy.uint16))
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

        with open(self.filepath, "w", encoding="utf-8") as f:
            yaml.dump(result, f, Dumper=utils.MyDumper)

        return {"FINISHED"}


class OpExportL1(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.l1_export"
    bl_label = "Export L1 morph"
    bl_description = "Export selected shapekey as L1 morph"

    filename_ext = ".npy"

    filter_glob: bpy.props.StringProperty(default="*.npy", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH"

    def execute(self, context):
        ui = context.window_manager.cmedit_ui
        obj = context.object
        sk = obj.active_shape_key
        data = sk.data if sk else obj.data.vertices

        arr = numpy.empty(len(data)*3, dtype=numpy.float64 if ui.morph_float_precicion == "64" else numpy.float32)
        data.foreach_get("co", arr)
        numpy.save(self.filepath, arr.reshape(-1, 3))
        return {"FINISHED"}

class MorphExporter:
    def __init__(self, context):
        self.obj = context.object
        rk = self.obj.data.shape_keys.reference_key
        self.rk = rk
        self.basis = numpy.empty(len(rk.data)*3)
        self.basis2 = numpy.empty(len(rk.data)*3)
        self.morphed = numpy.empty(len(rk.data)*3)

        ui = context.window_manager.cmedit_ui
        self.epsilonsq = ui.morph_epsilon ** 2

        self.dtype = numpy.float64 if ui.morph_float_precicion == "64" else numpy.float32

        rk.data.foreach_get("co", self.basis)

    def do_export(self, sk, path, has_ext):
        if sk.relative_key == sk:
            return
        sk.data.foreach_get("co", self.morphed)
        if sk.relative_key == self.rk:
            basis3 = self.basis
        else:
            basis3 = self.basis2
            sk.relative_key.data.foreach_get("co", basis3)

        self.morphed -= basis3
        m2 = self.morphed.reshape(-1, 3)

        if sk.vertex_group:
            vg = self.obj.vertex_groups[sk.vertex_group].index
            for i, v in enumerate(self.obj.data.vertices):
                for e in v.groups:
                    if e.group == vg:
                        m2[i] *= e.weight
                        break
                else:
                    m2[i] = (0, 0, 0)

        if has_ext and path[-4:] == ".npy":
            numpy.save(path, m2.astype(dtype=self.dtype, casting="same_kind"))
            return

        idx = ((m2 * m2).sum(1) > self.epsilonsq).nonzero()[0]
        if (has_ext and path[-4:] == ".npz") or len(idx) * 5 <= len(m2) * 4:
            numpy.savez(path, idx=idx.astype(dtype=numpy.uint16), delta=m2[idx].astype(dtype=self.dtype, casting="same_kind"))
        else:
            numpy.save(path, m2.astype(dtype=self.dtype, casting="same_kind"))

class OpMorphExport(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.morph_export"
    bl_label = "Export single morph"
    bl_description = "Export active shapekey as L2/L3 morph"

    #filename_ext = ".npz"

    filter_glob: bpy.props.StringProperty(default="*.npy;*.npz", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH" and context.object.active_shape_key

    def execute(self, context):
        MorphExporter(context).do_export(context.object.active_shape_key, self.filepath, True)
        return {"FINISHED"}

class OpMorphsExport(DirExport):
    bl_idname = "cmedit.morphs_export"
    bl_label = "Export morphs"
    bl_description = "Export specified morphs from shape keys to a specified directory"

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH"

    def execute(self, context):
        ui = context.window_manager.cmedit_ui
        r = re.compile(ui.morph_regex)
        m = context.object.data
        if not m.shape_keys or not m.shape_keys.key_blocks or not m.shape_keys.reference_key:
            self.report({"ERROR"}, "No shape keys!")
            return {"CANCELLED"}
        keys = {}

        for sk in m.shape_keys.key_blocks:
            if not r.match(sk.name):
                continue
            name = r.sub(ui.morph_replace, sk.name)
            keys[name] = sk
            for ext in (".npy",".npz"):
                if os.path.exists(os.path.join(self.directory, name + ext)):
                    self.report({"ERROR"}, name + f"{name}{ext} already exists!")
                    return {"CANCELLED"}

        me = MorphExporter(context)

        for name, sk in keys.items():
            if sk == me.rk:
                continue
            me.do_export(sk, os.path.join(self.directory, name), False)

        return {"FINISHED"}

class OpMorphListExport(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.morphlist_export"
    bl_label = "Export morph list"
    bl_description = 'Export morphs list to json file'
    filename_ext = ".json"

    def execute(self, context):
        ui = context.window_manager.cmedit_ui
        r = re.compile(ui.morph_regex)
        lst = []
        keys = context.object.data.shape_keys
        for sk in keys.key_blocks:
            if not r.match(sk.name):
                continue
            if sk == keys.reference_key:
                continue
            name = r.sub(ui.morph_replace, sk.name)
            if name.startswith("--"):
                lst.append({"separator": True})
            elif sk.slider_min == 0 and sk.slider_max == 1:
                lst.append({"morph": name})
            else:
                lst.append({"morph": name, "min": sk.slider_min, "max": sk.slider_max})

        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(lst, f)

        return {"FINISHED"}

# There seems to be bug in pylint with numpy's nonzero function
# pylint: disable=no-member
def sel_arr(items):
    return numpy.array([x.select for x in items], dtype=bool).nonzero()[0].astype(dtype=numpy.uint16)

class OpSelSetExport(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.selset_export"
    bl_label = "Export vertex+face list"
    bl_description = 'Export selected vertices and faces to npz file'
    filename_ext = ".npz"

    def execute(self, context):
        obj = context.object
        mesh = obj.data
        if mesh.is_editmode:
            obj.update_from_editmode()

        numpy.savez(self.filepath, verts=sel_arr(mesh.vertices), faces=sel_arr(mesh.polygons))
        return {"FINISHED"}

class UIProps:
    vg_regex: bpy.props.StringProperty(
        name="VG regex",
        description="Regular expression for vertex group export",
        default="^(DEF-|MCH-|ORG|(corrective_smooth|preserve_volume)(_inv)?$)",
    )
    vg_overwrite: bpy.props.BoolProperty(
        name="VG overwrite",
        description="Overwrite existing vertex groups with imported ones",
    )
    morph_regex: bpy.props.StringProperty(
        name="Morph regex",
        description="Regular expression for morph export",
        default=r"L2\_\_",
    )
    morph_replace: bpy.props.StringProperty(
        name="Morph name replace",
        description="Replace matched morph regex with this content",
        default="",
    )
    morph_float_precicion: bpy.props.EnumProperty(
        name="Precision",
        description="Floating point precision for morph npz files",
        default="32",
        items=[
            ("32", "32 bits", "IEEE Single precision floating point"),
            ("64", "64 bits", "IEEE Double precision floating point"),
        ]
    )
    morph_epsilon: bpy.props.FloatProperty(
        name="Morph cutoff",
        description="Ignore vertices morphed less than this value",
        default=1e-6,
        precision = 6,
    )
    rig_bones_mode: bpy.props.EnumProperty(
        name="Bones mode",
        description="Bones export mode",
        default="N",
        items=[
            ("N", "Props only", "Export data only where charmorph_* custom props are present"),
            ("X", "X axis", "Export X axis for all bones"),
            ("Z", "Z axis", "Export Z axis for all bones"),
        ]
    )

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
        l.operator("cmedit.all_hair_export")
        l.operator("cmedit.hair_import")
        l.operator("cmedit.face_export")
        l.operator("cmedit.selset_export")
        l.separator()
        l.prop(ui, "rig_bones_mode")
        l.operator("cmedit.bones_export")
        l.separator()
        l.prop(ui, "vg_regex")
        l.prop(ui, "vg_overwrite")
        l.operator("cmedit.vg_export")
        l.operator("cmedit.vg_import")
        l.separator()
        l.prop(ui, "morph_regex")
        l.prop(ui, "morph_replace")
        l.prop(ui, "morph_float_precicion")
        l.operator("cmedit.l1_export")
        l.operator("cmedit.morph_export")
        l.prop(ui, "morph_epsilon")
        l.operator("cmedit.morphs_export")
        l.operator("cmedit.morphlist_export")

classes = [OpHairExport, OpAllHairExport, OpHairImport, OpVgExport, OpVgImport, OpBoneExport, OpFaceExport, OpExportL1, OpMorphExport, OpMorphsExport, OpMorphListExport, OpSelSetExport, CHARMORPH_PT_FileIO]
