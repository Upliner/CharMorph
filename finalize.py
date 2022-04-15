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

import logging, traceback, numpy

import bpy # pylint: disable=import-error

from .lib import fit_calc, rigging, utils
from . import morphing, fitting, rigify

logger = logging.getLogger(__name__)

def remove_armature_modifiers(obj):
    for m in list(obj.modifiers):
        if m.type == "ARMATURE":
            obj.modifiers.remove(m)

def delete_old_rig(obj, rig):
    rigify.remove_rig(rig)
    remove_armature_modifiers(obj)

def clear_vg_names(vgs, vg_names):
    if not vg_names:
        return
    for vg in list(vgs):
        if vg.name in vg_names:
            vgs.remove(vg)

def clear_old_weights(obj, char, rig):
    vgs = obj.vertex_groups
    for bone in rig.data.bones:
        if bone.use_deform:
            vg = vgs.get(bone.name)
            if vg:
                vgs.remove(vg)
    clear_vg_names(vgs, set(rigging.char_rig_vg_names(char, rig)))

def clear_old_weights_with_assets(obj, char, rig):
    clear_old_weights(obj, char, rig)
    for asset in fitting.get_fitter(obj).get_assets():
        clear_old_weights(asset, char, rig)

def delete_old_rig_with_assets(obj, rig):
    delete_old_rig(obj, rig)
    for asset in fitting.get_fitter(obj).get_assets():
        remove_armature_modifiers(asset)

def add_rig(ui, verts: numpy.ndarray, verts_alt: numpy.ndarray):
    m = morphing.morpher
    obj = m.obj
    char = m.char
    conf = char.armature.get(ui.fin_rig)
    if not conf:
        raise rigging.RigException("Rig is not found")

    rig_type = conf.type
    if rig_type not in ("arp", "rigify", "regular"):
        raise rigging.RigException(f"Rig type {rig_type} is not supported")

    rig = utils.import_obj(char.path(conf.file), conf.obj_name, "ARMATURE")
    if not rig:
        raise rigging.RigException("Rig import failed")

    new_vgs = None
    err = None
    try:
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="EDIT")

        rig.data.use_mirror_x = False
        rigger = rigging.Rigger(bpy.context)
        if conf.joints:
            joints = conf.joints
            if m.alt_topo and (ui.fin_manual_sculpt or verts is verts_alt):
                joints = fit_calc.RiggerFitCalculator(m).transfer_weights_get(obj, rigging.vg_read(joints))
            rigger.joints_from_file(joints, verts)
        else:
            rigger.joints_from_char(obj, verts_alt)
        rigger.set_opts(conf.bones)
        joints = None
        if rig_type == "arp":
            joints = rigging.layer_joints(bpy.context, conf.arp_reference_layer)
        if not rigger.run(joints):
            raise rigging.RigException("Rig fitting failed")

        bpy.ops.object.mode_set(mode="OBJECT")

        old_rig = obj.find_armature()
        if old_rig:
            clear_old_weights_with_assets(obj, char, old_rig)

        if m.alt_topo:
            fitting.get_fitter(m).transfer_weights(obj, conf.weights_npz)
        else:
            rigging.import_vg(obj, conf.weights_npz, False)

        attach = True
        if rig_type == "rigify":
            rigify.apply_metarig_parameters(rig)
            metarig_only = ui.rigify_metarig_only
            if metarig_only or (not hasattr(rig.data, "rigify_generate_mode") and not hasattr(rig.data, "rigify_target_rig")):
                if not metarig_only:
                    err = "Rigify is not found! Generating metarig only"
                utils.copy_transforms(rig, obj)
                attach = False
            else:
                rig = rigify.do_rig(m, conf, rigger)

        if rig_type == "arp":
            if hasattr(bpy.ops, "arp") and hasattr(bpy.ops.arp, "match_to_rig"):
                try:
                    bpy.ops.arp.match_to_rig()
                except Exception as e:
                    err = str(e)
                    logger.error(traceback.format_exc())
            else:
                err = "Auto-Rig Pro is not found! Can't match the rig"

        rig.data["charmorph_template"] = obj.data.get("charmorph_template", "")
        rig.data["charmorph_rig_type"] = ui.fin_rig
        obj.data["charmorph_rig_type"] = ui.fin_rig

        if old_rig:
            delete_old_rig_with_assets(obj, old_rig)

        if attach:
            attach_rig(obj, rig)
    except:
        try:
            if conf and conf.weights_npz:
                clear_vg_names(set(rigging.vg_names(conf.weights_npz)), new_vgs)
            bpy.data.armatures.remove(rig.data)
        except:
            pass
        raise
    return err

def attach_rig(obj, rig):
    utils.copy_transforms(rig, obj)
    utils.reset_transforms(obj)
    obj.parent = rig

    utils.lock_obj(obj, True)

    mod = obj.modifiers.new("charmorph_rig", "ARMATURE")
    mod.use_vertex_groups = True
    mod.object = rig
    rigging.reposition_armature_modifier(obj)
    if "preserve_volume" in obj.vertex_groups or "preserve_volume_inv" in obj.vertex_groups:
        mod2 = obj.modifiers.new("charmorph_rig_pv", "ARMATURE")
        mod2.use_vertex_groups = True
        mod2.use_deform_preserve_volume = True
        mod2.use_multi_modifier = True
        mod2.object = rig
        if "preserve_volume_inv" in obj.vertex_groups:
            mod2.vertex_group = "preserve_volume_inv"
        else:
            mod2.vertex_group = "preserve_volume"
            mod2.invert_vertex_group = True
        rigging.reposition_armature_modifier(obj)
    else:
        mod.use_deform_preserve_volume = True

    if bpy.context.window_manager.charmorph_ui.fitting_weights != "NONE":
        fitting.get_fitter(obj).transfer_new_armature()

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

def _get_fin_sk(obj):
    keys = obj.data.shape_keys
    if not keys or not keys.key_blocks:
        return None, False
    fin_sk = keys.key_blocks.get("charmorph_final")
    if not fin_sk:
        # FIXME: what if L3 morphs are non-zero?
        return obj.shape_key_add(name="charmorph_final", from_mix=True), True
    return fin_sk, False

def _get_sk_verts(ui):
    m = morphing.morpher
    fin_sk = None
    fin_sk_tmp = False
    verts, verts_alt = None, None
    if hasattr(m, "get_final") and not ui.fin_manual_sculpt:
        verts = m.get_final()

    if ui.fin_morph != "NO" or verts is None:
        fin_sk, fin_sk_tmp = _get_fin_sk(m.obj)

    if verts is None or m.alt_topo:
        verts_alt = utils.verts_to_numpy(m.obj.data.vertices if fin_sk is None else fin_sk.data)

    if fin_sk_tmp:
        if ui.fin_morph == "NO":
            m.obj.shape_key_remove(fin_sk)
            fin_sk = None
        else:
            fin_sk.value = 1

    if verts is None:
        verts = verts_alt
    elif verts_alt is None:
        verts_alt = verts

    return fin_sk, verts, verts_alt

def _cleanup_morphs(ui, fin_sk):
    if ui.fin_morph == "NO":
        return
    obj = morphing.morpher.obj

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
    if fin_sk:
        obj.shape_key_remove(fin_sk)
    obj.shape_key_remove(keys.reference_key)

def apply_morphs(ui):
    fin_sk, verts, verts_alt = _get_sk_verts(ui)
    _cleanup_morphs(ui, fin_sk)
    if (ui.fin_csmooth and not ui.fin_cs_morphing) or ui.fin_morph == "AL":
        morphing.morpher.obj.data.vertices.foreach_set("co", verts_alt.reshape(-1))
    return verts, verts_alt

def _add_modifiers(ui):
    obj = morphing.morpher.obj
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
        mod = add_modifier(obj, "CORRECTIVE_SMOOTH", rigging.reposition_cs_modifier)
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
        mod = add_modifier(obj, "SUBSURF", rigging.reposition_subsurf_modifier)
        mod.show_viewport = ui.fin_subdivision == "RV"

    add_corrective_smooth(obj)
    add_subsurf(obj)

    for asset in fitting.get_fitter(obj).get_assets():
        if ui.fin_csmooth_assets == "RO":
            sk_to_verts(asset, "charmorph_fitting")
        elif ui.fin_csmooth_assets == "FR":
            sk_to_verts(asset, "Basis")
        if ui.fin_csmooth_assets != "NO":
            add_corrective_smooth(asset)
        if ui.fin_subdiv_assets:
            add_subsurf(asset)

def _do_vg_cleanup():
    unused_l1 = set()
    m = morphing.morpher
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

class OpFinalize(bpy.types.Operator):
    bl_idname = "charmorph.finalize"
    bl_label = "Finalize"
    bl_description = "Finalize character (add rig, modifiers, cleanup)"
    bl_options = {"UNDO"}
    vg_cleanup: bool

    @classmethod
    def poll(cls, _):
        return morphing.morpher

    def _do_rig(self, ui, verts, verts_alt):
        if ui.fin_rig == "-":
            return True
        try:
            err = add_rig(ui, verts, verts_alt)
            if err is not None:
                self.report({"ERROR"}, err)
                self.vg_cleanup = False
        except rigging.RigException as e:
            self.report({"ERROR"}, str(e))
            return False
        return True

    def execute(self, context):
        t = utils.Timer()
        if context.view_layer != bpy.context.view_layer:
            self.report({'ERROR'}, "Bad context")
            return {"CANCELLED"}

        ui = context.window_manager.charmorph_ui
        self.vg_cleanup = ui.fin_vg_cleanup

        if not self._do_rig(ui, *apply_morphs(ui)):
            return {"CANCELLED"}

        # Show warning if fin_morph == "AL" and some shapekeys are present?

        _add_modifiers(ui)

        if self.vg_cleanup:
            _do_vg_cleanup()

        morphing.recreate_charmorphs()

        t.time("total finalize")

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
        obj = morphing.get_obj_char(context)[0]
        return obj.find_armature()

    def execute(self, context): # pylint: disable=no-self-use
        obj, char = morphing.get_obj_char(context)

        old_rig = obj.find_armature()
        if old_rig:
            if obj.parent is old_rig:
                utils.copy_transforms(obj, old_rig)
                utils.lock_obj(obj, False)
            clear_old_weights_with_assets(obj, char, old_rig)

        delete_old_rig_with_assets(obj, old_rig)

        if "charmorph_rig_type" in obj.data:
            del obj.data["charmorph_rig_type"]

        morphing.recreate_charmorphs()

        return {"FINISHED"}

def get_rigs(_, context):
    char = morphing.get_obj_char(context)[1]
    result = [("-", "<None>", "Don't generate rig")]
    if char:
        result.extend((name, rig.title, "") for name, rig in char.armature.items())
    return result

class UIProps:
    fin_morph: bpy.props.EnumProperty(
        name="Apply morphs",
        default="SK",
        items=[
            ("NO", "Don't apply", "Keep all morphing shape keys"),
            ("SK", "Keep original basis", "Keep original basis shape key (recommended if you plan to fit more assets)"),
            ("AL", "Full apply", "Apply current mix as new basis and remove all shape keys (won't work when facial expression shapekeys or other additional morphs are present)"),
        ],
        description="Apply current shape key mix")
    fin_rig: bpy.props.EnumProperty(
        name="Rig",
        items=get_rigs,
        description="Rigging options")
    fin_subdivision: bpy.props.EnumProperty(
        name="Subdivision",
        default="RO",
        items=[
            ("NO", "No", "No subdivision surface"),
            ("RO", "Render only", "Use subdivision only for rendering"),
            ("RV", "Render+Viewport", "Use subdivision for rendering and viewport (may be slow on old hardware)"),
        ],
        description="Use subdivision surface for smoother look")
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
    fin_csmooth: bpy.props.BoolProperty(
        name="Corrective smooth",
        description="Use corrective smooth to fix deform artifacts",
        default = True,
    )
    fin_cs_limit: bpy.props.BoolProperty(
        name="Limit smooth",
        description="Use rig-defined vertex group to limit corrective smooth where it causes undesirable effects",
        default = True,
    )
    fin_cs_lenweight: bpy.props.BoolProperty(
        name="Length weighted smooth",
        description="Use length weighted smooth instead of simple",
        default = False,
    )
    fin_cs_morphing: bpy.props.BoolProperty(
        name="Smooth morphing",
        description="Use corrective smooth to smooth morphing artifacts (requires shape keys to be enabled)",
        default = False,
    )
    fin_manual_sculpt: bpy.props.BoolProperty(
        name="Manual edit/sculpt",
        default=False,
        description="Enable it if you want changes outside CharMorph's morphing panel (i.e. Blender's edit or sculpt mode) to affect character rig")
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
    bl_order = 9

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and morphing.get_obj_char(context)[0]

    def draw(self, context):
        l = self.layout
        ui = context.window_manager.charmorph_ui
        ll = l
        for prop in UIProps.__annotations__: # pylint: disable=no-member
            if prop.startswith("fin_cs_"):
                if ll == l:
                    ll = l.column()
                    ll.enabled = ui.fin_csmooth
            elif ll != l:
                l.separator()
                ll = l
            ll.prop(ui, prop)
        l.operator("charmorph.finalize")
        l.operator("charmorph.unrig")

classes = [OpFinalize, OpUnrig, CHARMORPH_PT_Finalize]
