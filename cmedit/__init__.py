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
# Copyright (C) 2020-2021 Michael Vigovsky

import logging, numpy
import bpy, bpy_extras, bmesh  # pylint: disable=import-error

from ..lib import charlib, morpher_cores, fit_calc, utils
from . import file_io, rigging, vg_calc, symmetry

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
        l.operator("cmedit.export_fold")
        l.operator("cmedit.final_to_sk")


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
        fit = fit_calc.calc_fit(geom_dst.verts - geom_src.verts, f.get_weights(ui.asset_obj))
        fit += utils.get_basis_numpy(ui.asset_obj)
        sk.data.foreach_set("co", fit.reshape(-1))

        return {"FINISHED"}


class OpExportFold(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "cmedit.export_fold"
    bl_label = "Exoport fold"
    bl_description = "Export data for fitting acceleration and correction. "\
        "Use char as decimated asset, asset as full asset, target shape key for morphing"
    filename_ext = ".npz"

    filter_glob: bpy.props.StringProperty(default="*.npz", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        ui = context.window_manager.cmedit_ui
        return ui.char_obj and ui.asset_obj

    def execute(self, context):  # pylint: disable=no-self-use
        ui = context.window_manager.cmedit_ui
        f = fit_calc.FitCalculator(fit_calc.geom_mesh(ui.char_obj.data))
        afd = fit_calc.AssetFitData()
        afd.obj = ui.asset_obj
        afd.geom = fit_calc.geom_mesh(ui.asset_obj.data)
        weights = f.get_weights(afd)

        if ui.retarg_sk_dst.startswith("sk_"):
            verts = numpy.empty(len(ui.asset_obj.data.vertices) * 3)
            ui.asset_obj.data.shape_keys.key_blocks[ui.retarg_sk_dst[3:]].data.foreach_get("co", verts)
        else:
            verts = f.geom.verts

        faces = numpy.array(f.geom.faces, dtype=numpy.uint32)
        faces = faces.astype(file_io.get_bits(faces.reshape(-1)), casting="same_kind")

        numpy.savez_compressed(self.filepath,
            verts=verts.reshape(3, -1).astype(numpy.float32, casting="same_kind"),
            faces=faces,
            pos=weights[0], idx=weights[1].astype(file_io.get_bits(weights[1]), casting="same_kind"),
            weights=weights[2].astype(numpy.float32, casting="same_kind")
        )
        return {"FINISHED"}


class OpFinalToSk(bpy.types.Operator):
    bl_idname = "cmedit.final_to_sk"
    bl_label = "Final to shape key"
    bl_description = "Add shape key from final form (shape keys + modifiers). Can be useful because of problems with applying of corrective smooth modifier."
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object

    def execute(self, context):  # pylint: disable=no-self-use
        if not context.object.data.shape_keys:
            context.object.shape_key_add(name="Basis", from_mix=False)
        sk = context.object.shape_key_add(name="final", from_mix=False)
        bm = bmesh.new()
        try:
            bm.from_object(context.object, context.evaluated_depsgraph_get(), True, False, False)
            a = [v.co[i] for v in bm.verts for i in range(3)]
            sk.data.foreach_set("co", a)
        finally:
            bm.free()

        return {"FINISHED"}


def get_shape_keys(ui, _):
    result = [("m_b", "(morpher basis)", ""), ("m_f", "(morpher final)", "")]
    if ui.char_obj:
        sk = ui.char_obj.data.shape_keys
        if sk and sk.key_blocks:
            result.extend((("sk_" + sk.name, sk.name, '') for sk in sk.key_blocks))
    return result


class CMEditUIProps(bpy.types.PropertyGroup, vg_calc.UIProps):
    char_obj: bpy.props.PointerProperty(
        name="Char",
        description="Character mesh for rigging and asset fitting",
        type=bpy.types.Object,
        poll=utils.visible_mesh_poll,
    )
    asset_obj: bpy.props.PointerProperty(
        name="Asset",
        description="Asset mesh for retargetting",
        type=bpy.types.Object,
        poll=utils.visible_mesh_poll,
    )
    retarg_sk_src: bpy.props.EnumProperty(
        name="Source shape key",
        description="Source shape key for retarget",
        items=get_shape_keys,
    )
    retarg_sk_dst: bpy.props.EnumProperty(
        name="Target shape key",
        description="Target shape key for retarget",
        items=get_shape_keys,
    )


classes = [CMEditUIProps, VIEW3D_PT_CMEdit, OpRetarget, OpExportFold, OpFinalToSk, CMEDIT_PT_Assets]

for module in rigging, vg_calc, symmetry, file_io:
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
