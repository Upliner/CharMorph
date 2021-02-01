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

import logging
import bpy, mathutils

from . import library, morphing, rigging, fitting

logger = logging.getLogger(__name__)

class RigException(Exception):
    pass

def copy_transform(target, source):
    target.location = source.location
    target.rotation_mode = source.rotation_mode
    target.rotation_euler = source.rotation_euler
    target.rotation_quaternion = source.rotation_quaternion
    target.scale = source.scale

def add_rig(obj, conf, mode, verts):
    char = library.obj_char(obj)
    rig_type = conf.get("type")
    if rig_type not in ["rigify","regular"]:
        raise RigException("Rig type {} is not supported".format(rig_type))

    metarig = library.import_obj(char.path(conf["file"]), conf["obj_name"], "ARMATURE")
    if not metarig:
        raise RigException("Rig import failed")

    try:
        spine = metarig.pose.bones.get("spine")
        if spine:
            spine.rigify_parameters.make_custom_pivot = bpy.context.window_manager.charmorph_ui.fin_rigify_pivot

        # Trying to override the context leads to crash :( TODO: learn more about it, maybe even try to gdb blender
        #override = context.copy()
        #override["object"] = metarig
        #override["active_object"] = metarig

        joints = rigging.all_joints(metarig)

        bone_opts = None
        bones_file = conf.get("bones", char.bones)
        if bones_file:
            bone_opts = char.get_yaml(bones_file)

        bpy.context.view_layer.objects.active = metarig
        bpy.ops.object.mode_set(mode="EDIT")

        locs = rigging.vg_to_locs(obj, verts, char.path(conf.get("joints")))

        if not rigging.joints_to_locs(bpy.context, obj, joints, locs, bone_opts):
            raise RigException("Metarig fitting failed")

        bpy.ops.object.mode_set(mode="OBJECT")

        weights = conf.get("weights")
        if weights:
            rigging.import_vg(obj, char.path(weights), False)

        if rig_type == "rigify":
            if mode != "RG":
                copy_transform(metarig, obj)
            else:
                add_rigify(obj, metarig, conf, locs, bone_opts)
        else:
            attach_rig(obj, metarig)
    except:
        try:
            bpy.data.armatures.remove(metarig.data)
        except:
            pass
        raise

def add_mixin(obj, conf, rig):
    obj_name = conf.get("mixin")
    if not obj_name:
        return (None, None)
    mixin = library.import_obj(library.obj_char(obj).path(conf["file"]), obj_name, "ARMATURE")
    bones = [ b.name for b in mixin.data.bones ]
    joints = rigging.all_joints(mixin)
    override = bpy.context.copy()
    override["object"] = rig
    override["selected_editable_objects"] = [rig, mixin]
    bpy.ops.object.join(override)

    return (bones, joints)

def add_rigify(obj, metarig, conf, locs, opts):
    metarig.data.rigify_generate_mode = "new"
    bpy.ops.pose.rigify_generate()
    rig = bpy.context.object
    try:
        bpy.data.armatures.remove(metarig.data)
        rig.name = obj.name + "_rig"
        new_bones, new_joints = add_mixin(obj, conf, rig)

        editmode_tweaks, tweaks = rigging.unpack_tweaks(library.obj_char(obj).path("."), conf.get("tweaks",[]))
        bpy.ops.object.mode_set(mode="EDIT")

        if new_joints and not rigging.joints_to_locs(bpy.context, rig, new_joints, locs, opts):
            raise RigException("Mixin fitting failed")

        rigging.rigify_add_deform(bpy.context, obj)

        for tweak in editmode_tweaks:
            rigging.apply_editmode_tweak(bpy.context, tweak)
        bpy.ops.object.mode_set(mode="OBJECT")
        for tweak in tweaks:
            rigging.apply_tweak(rig, tweak)

        # adjust bone constraints for mixin
        if new_bones:
            for name in new_bones:
                bone = rig.pose.bones.get(name)
                if not bone:
                    continue
                for c in bone.constraints:
                    if c.type == "STRETCH_TO":
                        c.rest_length = bone.length

        attach_rig(obj, rig)
    except:
        try:
            bpy.data.armatures.remove(rig.data)
        except:
            pass
        raise

def attach_rig(obj, rig):
    copy_transform(rig, obj)

    obj.location = (0,0,0)
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = (1,0,0,0)
    obj.scale = (1,1,1)
    obj.parent = rig

    rigging.lock_obj(obj, True)

    rig.data["charmorph_template"] = obj.data.get("charmorph_template","")

    mod = obj.modifiers.new("charmorph_rig", "ARMATURE")
    mod.use_deform_preserve_volume = True
    mod.use_vertex_groups = True
    mod.object = rig
    rigging.reposition_armature_modifier(bpy.context, obj)

    if bpy.context.window_manager.charmorph_ui.fitting_armature:
        fitting.transfer_new_armature(obj)

def get_obj(context):
    m = morphing.morpher
    if m:
        return m.obj, m.char
    if context.object and context.object.type == "MESH":
        char = library.obj_char(context.object)
        if char.name:
            return context.object, char
    return (None, None)

class OpFinalize(bpy.types.Operator):
    bl_idname = "charmorph.finalize"
    bl_label = "Finalize"
    bl_description = "Finalize character (add rig, modifiers, cleanup)"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode=="OBJECT" and get_obj(context)[0]

    def execute(self, context):
        ui = context.window_manager.charmorph_ui
        obj, char = get_obj(context)
        if not char.name or not char.config:
            self.report({'ERROR'}, "Character config is not found")
            return {"CANCELLED"}

        unused_l1 = set()

        keys = obj.data.shape_keys
        fin_sk = None
        fin_sk_tmp = False
        if keys and keys.key_blocks:
            if ui.fin_morph != "NO" or ui.fin_rig != "-":
                fin_sk = obj.data.shape_keys.key_blocks.get("charmorph_final")
                if not fin_sk:
                    fin_sk_tmp = ui.fin_morph == "NO"
                    fin_sk = obj.shape_key_add(name="charmorph_final", from_mix=True)
                fin_sk.value = 1

            unknown_keys = False

            for key in keys.key_blocks:
                if key.name.startswith("L1_") and key.value<0.01:
                    unused_l1.add(key.name[3:])
                if ui.fin_morph != "NO" and key != keys.reference_key and key != fin_sk:
                    if key.name.startswith("L1_")  or key.name.startswith("L2_"):
                        obj.shape_key_remove(key)
                    else:
                        unknown_keys = True

            if ui.fin_morph == "AL":
                if unknown_keys:
                    self.report({"WARNING"}, "Unknown shape keys found. Keeping original basis anyway")
                else:
                    obj.shape_key_remove(keys.reference_key)
                    obj.shape_key_remove(fin_sk)
                    fin_sk = None

        if ui.fin_morph != "NO" and "cm_morpher" in obj.data:
            del obj.data["cm_morpher"]
            for k in [k for k in obj.data.keys() if k.startswith("cmorph_")]:
                del obj.data[k]

        vg_cleanup = ui.fin_vg_cleanup
        def do_rig():
            nonlocal vg_cleanup
            if ui.fin_rig == "-":
                return True
            if obj.find_armature() and ui.fin_rigify_mode != "MR":
                self.report({"WARNING"}, "Character is already attached to an armature, skipping rig")
                return True
            rig = char.armature.get(ui.fin_rig)
            if not rig:
                self.report({"ERROR"}, "Rig is not found")
                return False
            rigify_mode = ui.fin_rigify_mode
            if rigify_mode == "RG" and not hasattr(bpy.types.Armature, "rigify_generate_mode"):
                self.report({"ERROR"}, "Rigify is not found! Generating metarig only")
                rigify_mode = "MR"
                vg_cleanup = False
            try:
                add_rig(obj, rig, rigify_mode, fin_sk.data if fin_sk else None)
            except RigException as e:
                self.report({"ERROR"}, str(e))
                return False
            return True

        ok = do_rig()

        if fin_sk_tmp:
            # Remove temporary mix shape key
            obj.shape_key_remove(fin_sk)

        if not ok:
            return {"CANCELLED"}

        def add_modifiers(obj):
            def add_modifier(typ):
                for mod in obj.modifiers:
                    if mod.type == typ:
                        return mod
                return obj.modifiers.new("charmorph_" + typ.lower(), typ)

            if ui.fin_csmooth != "NO":
                mod = add_modifier("CORRECTIVE_SMOOTH")
                mod.smooth_type = ui.fin_csmooth[2:]
                if ui.fin_csmooth[:1] == "L":
                    if "corrective_smooth" in obj.vertex_groups:
                        mod.vertex_group = "corrective_smooth"
                    elif "corrective_smooth_inv" in obj.vertex_groups:
                        mod.vertex_group = "corrective_smooth_inv"
                        mod.invert_vertex_group = True
                elif ui.fin_csmooth == "U":
                    mod.vertex_group = ""

            if ui.fin_subdivision != "NO":
                mod = add_modifier("SUBSURF")
                mod.show_viewport = ui.fin_subdivision == "RV"

        add_modifiers(obj)

        if (ui.fin_subdivision != "NO" and ui.fin_subdiv_assets) or (ui.fin_csmooth != "NO" and ui.fin_cmooth_assets):
            for asset in fitting.get_assets(obj):
                add_modifiers(asset)


        if vg_cleanup:
            if hasattr(context.window_manager, "chartype"):
                current_l1 = context.window_manager.chartype
                m = morphing.morpher
                if m and m.obj == obj and m.morphs_l1:
                    for l1 in m.morphs_l1.keys():
                        if l1 != current_l1:
                            unused_l1.add(l1)

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

        if ui.fin_morph != "NO":
            morphing.del_charmorphs()

        return {"FINISHED"}


class CHARMORPH_PT_Finalize(bpy.types.Panel):
    bl_label = "Finalization"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 9

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and get_obj(context)[0]

    def draw(self, context):
        ui = context.window_manager.charmorph_ui
        char = get_obj(context)[1]
        self.layout.prop(ui, "fin_morph")
        self.layout.prop(ui, "fin_rig")
        rig = char.armature.get(ui.fin_rig)
        if rig and rig.get("type") == "rigify":
            self.layout.prop(ui, "fin_rigify_mode")
            self.layout.prop(ui, "fin_rigify_pivot")
        self.layout.prop(ui, "fin_subdivision")
        self.layout.prop(ui, "fin_csmooth")
        self.layout.prop(ui, "fin_vg_cleanup")
        self.layout.prop(ui, "fin_subdiv_assets")
        self.layout.prop(ui, "fin_cmooth_assets")
        self.layout.operator("charmorph.finalize")

classes = [OpFinalize, CHARMORPH_PT_Finalize]
