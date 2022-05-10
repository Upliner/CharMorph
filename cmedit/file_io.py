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
import bpy, bpy_extras, bmesh, idprop  # pylint: disable=import-error

from ..lib import morphs, utils


def np_particles_data(obj, particles, precision=numpy.float32):
    cnt = numpy.empty(len(particles), dtype=numpy.uint8)
    total = 0
    mx = 1
    for i, p in enumerate(particles):
        c = len(p.hair_keys) - 1
        cnt[i] = c
        total += c
        if c > mx:
            mx = c

    data = numpy.empty((total, 3), dtype=precision)
    tmp = numpy.empty(mx * 3 + 3, dtype=precision)
    i = 0
    for p in particles:
        t2 = tmp[:len(p.hair_keys) * 3]
        p.hair_keys.foreach_get("co_local", t2)
        t2 = t2[3:].reshape((-1, 3))
        data[i:i + len(t2)] = t2
        i += len(t2)

    utils.np_matrix_transform(data, obj.matrix_world.inverted())
    return {"cnt": cnt, "data": data}


def export_hair(obj, psys_idx, filepath, precision):
    pss = obj.particle_systems
    old_psys_idx = pss.active_index
    pss.active_index = psys_idx

    psys = pss[psys_idx]
    is_global = psys.is_global_hair
    override = {"object": obj}
    if not is_global:
        bpy.ops.particle.disconnect_hair(override)
    numpy.savez_compressed(filepath, **np_particles_data(obj, psys.particles, precision))
    if not is_global:
        bpy.ops.particle.connect_hair(override)

    pss.active_index = old_psys_idx


prop_precision = bpy.props.EnumProperty(
    name="Precision",
    description="Floating point precision for morph npz files",
    default="32",
    items=[
        ("32", "32 bits", "IEEE Single precision floating point"),
        ("64", "64 bits", "IEEE Double precision floating point"),
    ]
)


def float_dtype(value):
    return numpy.float64 if value == "64" else numpy.float32


class OpHairExport(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.hair_export"
    bl_label = "Export hair"
    bl_description = "Export hairstyle to .npz file"
    filename_ext = ".npz"

    filter_glob: bpy.props.StringProperty(default="*.npz", options={'HIDDEN'})
    precision: prop_precision

    @classmethod
    def poll(cls, context):
        return context.object and context.object.particle_systems.active

    def execute(self, context):
        export_hair(context.object, context.object.particle_systems.active_index, self.filepath, float_dtype(self.precision))
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
    precision: prop_precision
    directory: bpy.props.StringProperty(subtype='DIR_PATH')
    filter_folder: bpy.props.BoolProperty(default=True, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH"

    def invoke(self, context, _):
        if not self.directory:
            self.directory = os.path.dirname(context.blend_data.filepath)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class OpAllHairExport(DirExport):
    bl_idname = "cmedit.all_hair_export"
    bl_label = "Export all hair"
    bl_description = "Export all hairstyles to .npz files"

    def execute(self, context):
        for i, psys in enumerate(context.object.particle_systems):
            export_hair(context.object, i, os.path.join(self.directory, psys.name + ".npz"), float_dtype(self.precision))
        return {"FINISHED"}


def flatten(arr, dtype):
    return numpy.block([numpy.array(item, dtype=dtype) for item in arr])


def get_bits(arr):
    num = max(arr, default=0)
    if num >= 65536:
        return numpy.uint32
    if num >= 256:
        return numpy.uint16
    return numpy.uint8


class OpVgExport(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.vg_export"
    bl_label = "Export VGs"
    bl_description = "Export vertex groups matching regex"
    filename_ext = ".npz"

    filter_glob: bpy.props.StringProperty(default="*.npz", options={'HIDDEN'})
    precision: prop_precision
    regex: bpy.props.StringProperty(
        name="VG regex",
        description="Regular expression for vertex group export",
        default="^(DEF-|MCH-|ORG|(corrective_smooth|preserve_volume)(_inv)?$)",
    )

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH"

    def execute(self, context):
        r = re.compile(self.regex)
        names, idx, weights = utils.vg_weights_to_arrays(context.object, r.search)
        cnt = [len(i) for i in idx]

        numpy.savez_compressed(
            self.filepath,
            names=b'\0'.join(name.encode("utf-8") for name in names),
            cnt=numpy.array(cnt, dtype=get_bits(cnt)),
            idx=flatten(idx, numpy.uint16),
            weights=flatten(weights, float_dtype(self.precision))
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
    overwrite: bpy.props.BoolProperty(
        name="VG overwrite",
        description="Overwrite existing vertex groups with imported ones",
    )

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH" and context.mode == "OBJECT"

    def execute(self, context):
        utils.import_vg(context.object, self.filepath, self.overwrite)
        return {"FINISHED"}


class OpBoneExport(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.bones_export"
    bl_label = "Export Bone settings"
    bl_description = 'Export bone settings (offsets, roll). Must be in edit mode when exporting in other than "Props only" mode'
    filename_ext = ".yaml"

    filter_glob: bpy.props.StringProperty(default="*.yaml", options={'HIDDEN'})
    mode: bpy.props.EnumProperty(
        name="Export mode",
        description="Bones export mode",
        default="N",
        items=[
            ("N", "Props only", "Export data only where charmorph_* custom props are present"),
            ("X", "X axis", "Export X axis for all bones"),
            ("Z", "Z axis", "Export Z axis for all bones"),
        ]
    )

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "ARMATURE"

    def execute(self, context):
        result = {}
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
                if self.mode == "X":
                    bd["axis_x"] = list(b.x_axis)
                elif self.mode == "Z":
                    bd["axis_z"] = list(b.z_axis)

            if len(bd) > 0:
                result[b.name] = bd

        with open(self.filepath, "w", encoding="utf-8") as f:
            utils.dump_yaml(result, f)

        return {"FINISHED"}


class OpExportL1(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.l1_export"
    bl_label = "Export L1 morph"
    bl_description = "Export selected shapekey as L1 morph"
    filename_ext = ".npy"

    filter_glob: bpy.props.StringProperty(default="*.npy", options={'HIDDEN'})
    precision: prop_precision

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH"

    def execute(self, context):
        obj = context.object
        sk = obj.active_shape_key
        data = sk.data if sk else obj.data.vertices

        arr = numpy.empty(len(data) * 3, dtype=float_dtype(self.precision))
        data.foreach_get("co", arr)
        numpy.save(self.filepath, arr.reshape(-1, 3))
        return {"FINISHED"}


def export_morph(m, path, epsilon, dtype):
    def save_npy():
        numpy.save(path, m.astype(dtype=dtype, casting="same_kind"))

    if path[-4:] == ".npy":
        save_npy()
        return

    idx = ((m ** 2).sum(1) > epsilon ** 2).nonzero()[0]
    if (path[-4:] == ".npz") or len(idx) * 5 <= len(m) * 4:
        numpy.savez(path, idx=idx.astype(dtype=numpy.uint16), delta=m[idx].astype(dtype=dtype, casting="same_kind"))
    else:
        save_npy()


class MorphExporter:
    def __init__(self, obj, epsilon, dtype):
        self.obj = obj
        self.epsilon = epsilon
        self.dtype = dtype

        rk = self.obj.data.shape_keys.reference_key
        self.rk = rk
        self.basis = numpy.empty(len(rk.data) * 3)
        self.basis2 = numpy.empty(len(rk.data) * 3)
        self.morphed = numpy.empty(len(rk.data) * 3)
        rk.data.foreach_get("co", self.basis)

    def do_export(self, sk, path):
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

        export_morph(m2, path, self.epsilon, self.dtype)


prop_cutoff = bpy.props.FloatProperty(
    name="Cutoff",
    description="Ignore vertices morphed by less than this value",
    default=1e-4,
    precision=6,
)


class OpMorphExport(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.morph_export"
    bl_label = "Export single morph"
    bl_description = "Export active shapekey as L2/L3 morph"
    check_extension = None  # you can enter .npy or .npz in filename to force format or omit extension for auto detection
    filename_ext = ""

    filter_glob: bpy.props.StringProperty(default="*.npy;*.npz", options={'HIDDEN'})
    mode: bpy.props.EnumProperty(
        name="Mode",
        description="What to export: active shape key or deformed result with all shape keys and modifiers",
        default="SK",
        items=[
            ("SK", "Active shapekey", "Export active shape key"),
            ("BC", "BMesh cage", "Export BMesh cage that includes all shape keys mix and modifiers result"),
        ]
    )
    precision: prop_precision
    cutoff: prop_cutoff

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH" and context.object.active_shape_key

    def execute(self, context):
        if self.mode == "SK":
            exp = MorphExporter(context.object, self.cutoff, float_dtype(self.precision))
            exp.do_export(context.object.active_shape_key, self.filepath)
        elif self.mode == "BC":
            bm = bmesh.new()
            try:
                utils.bmesh_cage_object(bm, context)
                export_morph(
                    [v.co for v in bm.verts] - utils.get_basis_numpy(context.object),
                    self.filepath, self.cutoff, float_dtype(self.precision))
            finally:
                bm.free()
        else:
            self.report({"ERROR"}, "Invalid mode")
            return {"CANCELLED"}

        return {"FINISHED"}


prop_regex = bpy.props.StringProperty(
    name="Morph regex",
    description="Regular expression for morph export",
    default=r"L2\_\_",
)
prop_re_replace = bpy.props.StringProperty(
    name="Morph name replace",
    description="Replace matched morph regex with this content",
    default="",
)


class OpMorphsExport(DirExport):
    bl_idname = "cmedit.morphs_export"
    bl_label = "Export morphs"
    bl_description = "Export specified morphs from shape keys to a specified directory"

    regex: prop_regex
    re_replace: prop_re_replace
    precision: prop_precision
    cutoff: prop_cutoff

    def execute(self, context):
        r = re.compile(self.regex)
        m = context.object.data
        if not m.shape_keys or not m.shape_keys.key_blocks or not m.shape_keys.reference_key:
            self.report({"ERROR"}, "No shape keys!")
            return {"CANCELLED"}
        keys = {}

        for sk in m.shape_keys.key_blocks:
            if not r.match(sk.name):
                continue
            name = r.sub(self.re_replace, sk.name)
            keys[name] = sk
            for ext in (".npy", ".npz"):
                if os.path.exists(os.path.join(self.directory, name + ext)):
                    self.report({"ERROR"}, name + f"{name}{ext} already exists!")
                    return {"CANCELLED"}

        exp = MorphExporter(context.object, self.cutoff, float_dtype(self.precision))

        for name, sk in keys.items():
            if sk == exp.rk:
                continue
            exp.do_export(sk, os.path.join(self.directory, name))

        return {"FINISHED"}


class OpMorphListExport(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.morphlist_export"
    bl_label = "Export morph list"
    bl_description = 'Export morphs list to json file'
    filename_ext = ".json"

    regex: prop_regex
    re_replace: prop_re_replace

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj and obj.type == "MESH" and obj.data.shape_keys and obj.data.shape_keys.key_blocks

    def execute(self, context):
        r = re.compile(self.regex)
        lst = []
        keys = context.object.data.shape_keys
        for sk in keys.key_blocks:
            if not r.match(sk.name):
                continue
            if sk == keys.reference_key:
                continue
            name = r.sub(self.re_replace, sk.name)
            if name.startswith("--"):
                lst.append({"separator": True})
            elif sk.slider_min == 0 and sk.slider_max == 1:
                lst.append({"morph": name})
            else:
                lst.append({"morph": name, "min": sk.slider_min, "max": sk.slider_max})

        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(lst, f)

        return {"FINISHED"}


class OpMorphsImport(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    bl_idname = "cmedit.morphs_import"
    bl_label = "Import morphs"
    bl_description = 'Import L2/L3 morphs as shape keys'

    filter_glob: bpy.props.StringProperty(default="*.npy;*.npz", options={'HIDDEN'})
    directory: bpy.props.StringProperty(subtype='DIR_PATH')
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement)
    prefix: bpy.props.StringProperty(
        name="Prefix",
        description="Prefix all imported shape keys with this string",
        default="",
    )

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH"

    def execute(self, context):
        obj = context.object
        if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
            sk = obj.shape_key_add(name="Basis", from_mix=False)
        else:
            sk = obj.data.shape_keys.reference_key
        basis = numpy.empty(len(sk.data) * 3)
        sk.data.foreach_get("co", basis)
        basis = basis.reshape(-1, 3)

        for file in self.files:
            sk = obj.shape_key_add(name=self.prefix + os.path.splitext(os.path.basename(file.name))[0], from_mix=False)
            sk.data.foreach_set("co", morphs.load(os.path.join(self.directory, file.name)).apply(basis.copy()).reshape(-1))

        return {"FINISHED"}


# There seems to be bug in pylint with numpy's nonzero function
# pylint: disable=no-member
def sel_arr(items):
    return numpy.array([x.select for x in items], dtype=bool).nonzero()[0].astype(dtype=numpy.uint16)


class OpSubsetExport(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.subset_export"
    bl_label = "Export subset"
    bl_description = 'Export selected vertices and faces to npz file'
    filename_ext = ".npz"

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH"

    def execute(self, context):
        obj = context.object
        mesh = obj.data
        if mesh.is_editmode:
            obj.update_from_editmode()

        numpy.savez(self.filepath, verts=sel_arr(mesh.vertices), faces=sel_arr(mesh.polygons))
        return {"FINISHED"}


class CHARMORPH_PT_FileIO(bpy.types.Panel):
    bl_label = "File I/O"
    bl_parent_id = "VIEW3D_PT_CMEdit"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 4

    def draw(self, _):
        l = self.layout
        l.label(text="Utils:")
        l.operator("cmedit.face_export")
        l.operator("cmedit.subset_export")
        l.operator("cmedit.bones_export")
        l.separator()
        l.label(text="Hair:")
        l.operator("cmedit.hair_export")
        l.operator("cmedit.all_hair_export")
        l.operator("cmedit.hair_import")
        l.separator()
        l.label(text="Vertex groups:")
        l.operator("cmedit.vg_export")
        l.operator("cmedit.vg_import")
        l.separator()
        l.label(text="Morphs:")
        l.operator("cmedit.l1_export")
        l.operator("cmedit.morph_export")
        l.operator("cmedit.morphs_export")
        l.operator("cmedit.morphlist_export")
        l.operator("cmedit.morphs_import")


classes = [
    OpFaceExport, OpSubsetExport, OpBoneExport,
    OpHairExport, OpAllHairExport, OpHairImport,
    OpVgExport, OpVgImport,
    OpExportL1, OpMorphExport, OpMorphsExport, OpMorphListExport, OpMorphsImport,
    CHARMORPH_PT_FileIO
]
