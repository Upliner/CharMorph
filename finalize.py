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

import bpy  # pylint: disable=import-error

from . import rig
from .lib import rigging, utils
from .morphing import manager as mm

logger = logging.getLogger(__name__)


def sk_to_verts(obj, sk):
    if isinstance(sk, str):
        k = obj.data.shape_keys
        if k and k.key_blocks:
            sk = k.key_blocks.get(sk)
    if sk is None:
        return
    arr = numpy.empty(len(sk.data) * 3)
    sk.data.foreach_get("co", arr)
    obj.data.vertices.foreach_set("co", arr)


def _cleanup_morphs(ui, fin_sk):
    if ui.fin_morph == "NO":
        return
    obj = mm.morpher.core.obj

    if "cm_morpher" in obj.data:
        del obj.data["cm_morpher"]
        prefix = "cmorph_" if ui.fin_morph == "AL" else "cmorph_L2_"
        for k in [k for k in obj.data.keys() if k.startswith(prefix)]:
            del obj.data[k]

    if ui.fin_morph == "AL" and "cm_alt_topo" in obj.data:
        del obj.data["cm_alt_topo"]

    keys = obj.data.shape_keys
    if not keys or not keys.key_blocks:
        return

    for key in keys.key_blocks:
        if key not in (keys.reference_key, fin_sk):
            if key.name.startswith("L1_") or key.name.startswith("L2_") or key.name.startswith("L4_"):
                obj.shape_key_remove(key)

    if ui.fin_morph != "AL":
        return
    if len(keys.key_blocks) > (2 if fin_sk else 1):
        return
    obj.shape_key_remove(keys.reference_key)
    if fin_sk:
        obj.shape_key_remove(fin_sk)


def apply_morphs(ui):
    mc = mm.morpher.core
    obj = mc.obj
    k = obj.data.shape_keys
    fin_sk = None
    if k and k.key_blocks:
        fin_sk = k.key_blocks.get("charmorph_final")
        if not fin_sk and ui.fin_morph != "NO":
            # FIXME: Set all non-morphing shape keys to zero before creating mix
            fin_sk = obj.shape_key_add(name="charmorph_final", from_mix=True)
            fin_sk.value = 1

    _cleanup_morphs(ui, fin_sk)

    if ui.fin_cs_morphing:
        obj.data.vertices.foreach_set("co", mc.get_final_alt_topo().reshape(-1))


def _add_modifiers(ui):
    obj = mm.morpher.core.obj

    def add_modifier(obj, typ, reposition):
        for mod in obj.modifiers:
            if mod.type == typ:
                return mod
        mod = obj.modifiers.new("charmorph_" + typ.lower(), typ)
        reposition(obj)
        return mod

    def add_corrective_smooth(obj):
        if not ui.fin_csmooth:
            return
        mod = add_modifier(obj, "CORRECTIVE_SMOOTH", utils.reposition_cs_modifier)
        mod.smooth_type = "LENGTH_WEIGHTED" if ui.fin_cs_lenweight else "SIMPLE"
        if ui.fin_cs_limit:
            if "corrective_smooth" in obj.vertex_groups:
                mod.vertex_group = "corrective_smooth"
            elif "corrective_smooth_inv" in obj.vertex_groups:
                mod.vertex_group = "corrective_smooth_inv"
                mod.invert_vertex_group = True
        else:
            mod.vertex_group = ""

    def add_subsurf(obj):
        if ui.fin_subdivision == "NO":
            return
        mod = add_modifier(obj, "SUBSURF", utils.reposition_subsurf_modifier)
        mod.show_viewport = ui.fin_subdivision == "RV"

    add_corrective_smooth(obj)
    add_subsurf(obj)

    for afd in mm.morpher.fitter.get_assets():
        if ui.fin_csmooth_assets == "RO":
            sk_to_verts(afd.obj, "charmorph_fitting")
        elif ui.fin_csmooth_assets == "FR":
            sk_to_verts(afd.obj, "Basis")
        if ui.fin_csmooth_assets != "NO":
            add_corrective_smooth(afd.obj)
        if ui.fin_subdiv_assets:
            add_subsurf(afd.obj)


def _do_vg_cleanup():
    unused_l1 = set()
    m = mm.morpher.core
    current_l1 = m.L1
    for l1 in m.morphs_l1:
        if l1 != current_l1:
            unused_l1.add(l1)

    obj = m.obj

    hair_vg = obj.vertex_groups.get("hair_" + current_l1)
    if hair_vg and "hair" not in obj.vertex_groups:
        hair_vg.name = "hair"

    # Make sure we won't delete any vertex groups used by hair particle systems
    for psys in obj.particle_systems:
        for attr in dir(psys):
            if attr.startswith("vertex_group_"):
                vg = getattr(psys, attr)
                if vg.startswith("hair_"):
                    unused_l1.remove(vg[5:])

    for vg in obj.vertex_groups:
        if vg.name.startswith("joint_") or (
                vg.name.startswith("hair_") and vg.name[5:] in unused_l1):
            obj.vertex_groups.remove(vg)


def _bbox_correction_coeffs(mcore, bbox):
    def calc_boxes(data: numpy.ndarray):
        boxes = data[bbox.reshape(-1)].reshape(bbox.shape+(3,))
        axis = len(bbox.shape)-1
        result = boxes.max(axis)
        result -= boxes.min(axis)
        return result

    coeffs = calc_boxes(mcore.get_final())
    coeffs /= calc_boxes(mcore.full_basis)
    return coeffs


def _ensure_basis(obj):
    if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
        obj.shape_key_add(name="Basis", from_mix=False)

def get_exp_sk(obj, name):
    name = "Exp_" + name
    sk = obj.data.shape_keys.key_blocks.get(name)
    if sk:
        return sk
    return obj.shape_key_add(name=name, from_mix=False)


def _import_expresions(add_assets):
    mc = mm.morpher.core
    fitter = mm.morpher.fitter

    _ensure_basis(mc.obj)
    if add_assets:
        for afd in fitter.get_assets():
            _ensure_basis(afd.obj)

    bbox = mc.char.bbox
    if bbox is not None:
        bb_idx = bbox["idx"]
        bb_coeffs = _bbox_correction_coeffs(mc, bbox["bbox"])

    if mc.alt_topo:
        binding = fitter.get_binding(fitter.alt_topo_afd)

    basis = utils.get_basis_numpy(mc.obj)

    for name, data in mc.enum_expressions():
        if bbox is not None:
            data[bb_idx] *= bb_coeffs

        if mc.alt_topo:
            fitted_data = binding.fit(data)
        elif add_assets:
            fitted_data = data.copy()
        else:
            fitted_data = data

        fitted_data += basis
        sk = get_exp_sk(mc.obj, name)
        sk.data.foreach_set("co", fitted_data.reshape(-1))

        if add_assets:
            for afd in fitter.get_assets():
                fitted_data = afd.binding.fit(data)
                if ((fitted_data ** 2).sum(1) < 1e-6).all():
                    continue
                fitted_data += afd.geom.verts
                sk = get_exp_sk(afd.obj, name)
                sk.data.foreach_set("co", fitted_data.reshape(-1))


class OpFinalize(bpy.types.Operator):
    bl_idname = "charmorph.finalize"
    bl_label = "Finalize"
    bl_description = "Finalize character (add rig, modifiers, cleanup)"
    bl_options = {"UNDO"}
    vg_cleanup: bool

    @classmethod
    def poll(cls, _):
        return mm.morpher

    def _do_rig(self, ui):
        if not ui.fin_rig:
            return True
        try:
            err = rig.add_rig(ui)
            if err is not None:
                self.report({"ERROR"}, err)
                self.vg_cleanup = False
        except rigging.RigException as e:
            self.report({"ERROR"}, str(e))
            return False
        return True

    def execute(self, context):
        t = utils.Timer()
        ui = context.window_manager.charmorph_ui
        mm.morpher.core.ensure()

        apply_morphs(ui)
        self.vg_cleanup = ui.fin_vg_cleanup
        if not self._do_rig(ui):
            return {"CANCELLED"}

        if ui.fin_expressions != "NO":
            _import_expresions(ui.fin_expressions == "CA")

        # Show warning if fin_morph == "AL" and some shapekeys are present?

        _add_modifiers(ui)
        if self.vg_cleanup:
            _do_vg_cleanup()
        mm.recreate_charmorphs()

        t.time("total finalize")
        return {"FINISHED"}


class UIProps:
    fin_morph: bpy.props.EnumProperty(
        name="Apply morphs",
        default="SK",
        items=[
            ("NO", "Don't apply", "Keep all morphing shape keys"),
            ("SK", "Keep original basis", "Keep original basis shape key (recommended if you plan to fit more assets)"),
            ("AL", "Full apply", "Apply current mix as new basis and remove all shape keys"
                "except facial expression shapekeys or other additional morphs"),
        ],
        description="Apply current shape key mix")
    fin_subdivision: bpy.props.EnumProperty(
        name="Subdivision",
        default="RO",
        items=[
            ("NO", "No", "No subdivision surface"),
            ("RO", "Render only", "Use subdivision only for rendering"),
            ("RV", "Render+Viewport", "Use subdivision for rendering and viewport (may be slow on old hardware)"),
        ],
        description="Use subdivision surface for smoother look")
    fin_expressions: bpy.props.EnumProperty(
        name="Expressions",
        description="Import or correct facial and other expression shape keys",
        default="NO",
        items=[
            ("NO", "No", "Don't import expresion shape keys"),
            ("CH", "Character", "Import expression shape keys for character only"),
            ("CA", "Character+Assets", "Import expression shape keys for assets if they affect them (breathing for example)"),
        ],
    )
    fin_rig: bpy.props.BoolProperty(
        name="Rig",
        description="Add rig to the character as a part of finalization process",
        default=True,
    )
    fin_csmooth: bpy.props.BoolProperty(
        name="Corrective smooth",
        description="Use corrective smooth to fix deform artifacts",
        default=True,
    )
    fin_csmooth_assets: bpy.props.EnumProperty(
        name="Corrective smooth for assets",
        default="NO",
        description="Use corrective smooth for assets too",
        items=[
            ("NO", "None", "No corrective smooth"),
            ("FR", "Fitting+Rig", "Allow to smooth artifacts caused by fitting and armature deform"),
            ("RO", "Rig only", "Allow to smooth only artifacts caused by armature deform"),
            ("NC", "No change", "Apply corrective smooth to assets but don't change its parameters"),
        ],
    )
    fin_cs_limit: bpy.props.BoolProperty(
        name="Limit smooth",
        description="Use rig-defined vertex group to limit corrective smooth where it causes undesirable effects",
        default=True,
    )
    fin_cs_lenweight: bpy.props.BoolProperty(
        name="Length weighted smooth",
        description="Use length weighted smooth instead of simple",
        default=False,
    )
    fin_cs_morphing: bpy.props.BoolProperty(
        name="Smooth morphing",
        description="Use corrective smooth to smooth morphing artifacts (requires shape keys to be enabled)",
        default=False,
    )
    fin_subdiv_assets: bpy.props.BoolProperty(
        name="Subdivide assets",
        default=False,
        description="Subdivide assets together with character")
    fin_vg_cleanup: bpy.props.BoolProperty(
        name="Cleanup vertex groups",
        default=False,
        description="Remove unused vertex groups after finalization")


class CHARMORPH_PT_Finalize(bpy.types.Panel):
    bl_label = "Finalization"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 10

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and mm.morpher

    def draw(self, context):
        l = self.layout
        ui = context.window_manager.charmorph_ui
        ll = l
        for prop in UIProps.__annotations__:  # pylint: disable=no-member
            if prop.startswith("fin_cs_"):
                if ll == l:
                    ll = l.column()
                    ll.enabled = ui.fin_csmooth
            elif ll != l:
                l.separator()
                ll = l
            ll.prop(ui, prop)
        l.operator("charmorph.finalize")


classes = [OpFinalize, CHARMORPH_PT_Finalize]
