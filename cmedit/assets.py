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

import numpy

import bpy, bpy_extras, bmesh  # pylint: disable=import-error

from . import file_io
from ..lib import morpher_cores, fit_calc, utils

class CMEDIT_PT_Assets(bpy.types.Panel):
    bl_label = "Assets"
    bl_parent_id = "VIEW3D_PT_CMEdit"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 3

    def draw(self, context):
        ui = context.window_manager.cmedit_ui
        l = self.layout
        l.prop(ui, "char_obj")
        l.prop(ui, "asset_obj")
        l.prop(ui, "retarg_sk_src")
        l.prop(ui, "retarg_sk_dst")
        l.operator("cmedit.retarget")
        l.operator("cmedit.final_to_sk")
        l.operator("cmedit.fold_export")


def get_shape_keys(obj):
    sk = obj.data.shape_keys
    if not sk or not sk.key_blocks:
        return ()
    return [("sk_" + sk.name, sk.name, '') for sk in sk.key_blocks]


def get_shape_keys_with_morpher(ui, _):
    result = [("m_b", "(morpher basis)", ""), ("m_f", "(morpher final)", "")]
    if ui.char_obj:
        result.extend(get_shape_keys(ui.char_obj))
    return result


def get_fold_shape_keys(_, context):
    ui = context.window_manager.cmedit_ui
    return [("_", "(none)", "")] + get_shape_keys(ui.char_obj)


def retarg_get_geom(obj, name):
    if name.startswith("m_"):
        mcore = morpher_cores.get(obj)
        if name == "m_b":
            return fit_calc.geom_morpher(mcore)
        if name == "m_f":
            return fit_calc.geom_morpher_final(mcore)
    if name.startswith("sk_"):
        return fit_calc.geom_shapekey(obj.data, obj.data.shape_keys.key_blocks[name[3:]])
    raise ValueError("Invalid retarget geom name: " + name)


class OpRetarget(bpy.types.Operator):
    bl_idname = "cmedit.retarget"
    bl_label = "Retarget asset"
    bl_description = "Refit asset from selected source shape key to target one"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        ui = context.window_manager.cmedit_ui
        return ui.char_obj and ui.asset_obj

    def execute(self, context):
        ui = context.window_manager.cmedit_ui
        char = ui.char_obj

        geom_src = retarg_get_geom(char, ui.retarg_sk_src)
        geom_dst = retarg_get_geom(char, ui.retarg_sk_dst)
        if len(geom_src.verts) != len(geom_dst.verts):
            self.report({"ERROR"}, f"Vertex count mismatch: {len(geom_src.verts)} != {len(geom_dst.verts)}. "
                                   "Can't retarget alt_topo morpher states with shape keys.")
            return {"FINISHED"}

        if not ui.asset_obj.data.shape_keys:
            ui.asset_obj.shape_key_add(name="Basis", from_mix=False)
        sk = ui.asset_obj.shape_key_add(name="retarget", from_mix=False)
        sk.value = 1

        f = fit_calc.FitCalculator(geom_src)
        fit = f.get_binding(ui.asset_obj).fit(geom_dst.verts - geom_src.verts)
        fit += utils.get_basis_numpy(ui.asset_obj)
        sk.data.foreach_set("co", fit.reshape(-1))

        return {"FINISHED"}


class OpFinalToSk(bpy.types.Operator):
    bl_idname = "cmedit.final_to_sk"
    bl_label = "Final to shape key"
    bl_options = {"UNDO"}
    bl_description = "Add shape key from final form (shape keys + modifiers)."\
        "Can be useful because of problems with applying of corrective smooth modifier."

    @classmethod
    def poll(cls, context):
        return context.object

    def execute(self, context):  # pylint: disable=no-self-use
        if not context.object.data.shape_keys:
            context.object.shape_key_add(name="Basis", from_mix=False)
        sk = context.object.shape_key_add(name="final", from_mix=False)
        bm = bmesh.new()
        try:
            utils.bmesh_cage_object(bm, context)
            a = [v.co[i] for v in bm.verts for i in range(3)]
            sk.data.foreach_set("co", a)
        finally:
            bm.free()

        return {"FINISHED"}


def get_sk_verts(ui, sk):
    return utils.verts_to_numpy(ui.char_obj.data.shape_keys.key_blocks[sk[3:]].data)


class OpExportFold(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.fold_export"
    bl_label = "Export fitting data"
    bl_description = "Export data for fitting acceleration and correction. Use char as proxy mesh and asset as full asset"
    filename_ext = ".npz"

    precision: file_io.prop_precision
    cutoff: file_io.prop_cutoff
    filter_glob: bpy.props.StringProperty(default="*.npz", options={'HIDDEN'})
    sk_binding: bpy.props.EnumProperty(
        name="Binding shape key",
        description="Shape key for binding",
        items=get_fold_shape_keys,
    )
    sk_weights: bpy.props.EnumProperty(
        name="Weights shape key",
        description="Shape key for rig weights transfer",
        items=get_fold_shape_keys,
    )

    @classmethod
    def poll(cls, context):
        ui = context.window_manager.cmedit_ui
        return ui.char_obj and ui.asset_obj

    def execute(self, context):
        ui = context.window_manager.cmedit_ui
        for obj in ui.char_obj, ui.asset_obj:
            if obj.data.is_editmode:
                obj.update_from_editmode()
        f = fit_calc.FitCalculator(fit_calc.geom_mesh(ui.char_obj.data))
        binding = f.get_binding(ui.asset_obj)[0]

        if self.sk_binding.startswith("sk_"):
            verts = get_sk_verts(ui, self.sk_binding)
        else:
            verts = f.geom.verts

        faces = numpy.array(f.geom.faces, dtype=numpy.uint32)
        faces = faces.astype(file_io.get_bits(faces.reshape(-1)), casting="same_kind")

        data = {
            "verts": verts.astype(file_io.float_dtype(self.precision), casting="same_kind"),
            "faces": faces,
            "pos": binding[0],
            "idx": binding[1].astype(file_io.get_bits(binding[1]), casting="same_kind"),
            "weights": binding[2].astype(numpy.float32, casting="same_kind"),
        }

        if self.sk_weights.startswith("sk_"):
            diff = get_sk_verts(ui, self.sk_weights) - verts
            idx = file_io.morph_idx_epsilon(diff, self.cutoff)
            data["wmorph_idx"] = idx
            data["wmorph_delta"] = diff[idx].astype(file_io.float_dtype(self.precision), casting="same_kind")

        numpy.savez(self.filepath, **data)
        return {"FINISHED"}


class UIProps:
    asset_obj: bpy.props.PointerProperty(
        name="Asset",
        description="Asset mesh for retargetting",
        type=bpy.types.Object,
        poll=utils.visible_mesh_poll,
    )
    retarg_sk_src: bpy.props.EnumProperty(
        name="Source shape key",
        description="Source shape key for retarget",
        items=get_shape_keys_with_morpher,
    )
    retarg_sk_dst: bpy.props.EnumProperty(
        name="Target shape key",
        description="Target shape key for retarget",
        items=get_shape_keys_with_morpher,
    )


classes = OpRetarget, OpFinalToSk, OpExportFold, CMEDIT_PT_Assets
