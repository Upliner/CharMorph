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

import logging, re
import bpy # pylint: disable=import-error

from . import materials, fitting
from .lib import charlib, fit_calc, utils, rigging

logger = logging.getLogger(__name__)

last_object = None

sep_re = re.compile(r"[ _-]")
eval_unsafe = re.compile(r"__|[:;,({'\"\[]")

def morph_category_name(name):
    m = sep_re.search(name)
    if m:
        return name[:m.start()]
    return name

d_minmax = {"min": 0, "max": 1}
def convertSigns(signs):
    try:
        return sum(d_minmax[sign] << i for i, sign in enumerate(signs))
    except KeyError:
        return -1

if isinstance(bpy.props.StringProperty(), tuple):
    # Before Blender 2.93 properties were tuples
    def prefixed_prop(prefix, prop):
        return (prefix + prop[1]["name"], prop)
else:
    # Blender version >= 2.93
    def prefixed_prop(prefix, prop):
        return (prefix + prop.keywords["name"], prop)

def get_target(obj):
    if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
        return obj.data.vertices
    sk = obj.data.shape_keys.key_blocks.get("charmorph_final")
    if sk is None:
        sk = obj.shape_key_add(name="charmorph_final", from_mix=False)
    if sk.value < 0.75:
        sk.value = 1
    return sk.data

def get_obj_char(context):
    if morpher:
        return morpher.obj, morpher.char
    obj = context.object
    if obj:
        if obj.type == "ARMATURE":
            children = obj.children
            if len(children) == 1:
                obj = children[0]
        if obj.type == "MESH":
            char = charlib.obj_char(obj)
            if char:
                return obj, char
    return (None, None)

def get_basis(data, use_morpher=True, use_char=True):
    if isinstance(data, bpy.types.Object):
        data = data.data
    k = data.shape_keys
    if k:
        return utils.verts_to_numpy(k.reference_key.data)

    if use_morpher and morpher and morpher.obj.data == data:
        return morpher.get_basis_alt_topo()

    alt_topo = data.get("cm_alt_topo")
    if isinstance(alt_topo, (bpy.types.Object, bpy.types.Mesh)):
        return get_basis(alt_topo, False, False)

    char = None
    if use_char:
        char = charlib.char_by_name(data.get("charmorph_template"))

    if char:
        if not alt_topo:
            basis = char.np_basis
            if basis is not None:
                return basis.copy()
        elif isinstance(alt_topo, str):
            return charlib.char_by_name(data.get("charmorph_template")).get_np("morphs/alt_topo/" + alt_topo)

    return utils.verts_to_numpy(data.vertices)

class Morph:
    __slots__ = "min", "max", "data"
    def __init__(self, data, minval=0, maxval=0):
        self.min = minval
        self.max = maxval
        self.data = data

class Morpher:
    upd_lock = False
    clamp = True
    version = 0
    error = None
    alt_topo = False
    alt_topo_buildable = False
    rig_name = ""
    rig = None
    categories = []
    sliding_joints = {}
    L1_idx = 0

    presets = {}
    presets_list = [("_", "(reset)", "")]

    def __init__(self, obj):
        self.obj = obj
        self.char = charlib.obj_char(obj)
        self.morphs_l1 = {}
        self.morphs_l2 = {}
        self.morphs_combo = {}
        self.meta_prev = {}
        self.mtl_props = materials.props

        self.L1 = self.get_L1()
        self.L1_list = [(name, self.char.types.get(name, {}).get("title", name), "") for name in sorted(self.morphs_l1.keys())]
        self.update_L1_idx()
        if self.obj:
            self.rig = obj.find_armature()
        if self.rig:
            self.rig_name = self.rig.data.get("charmorph_rig_type","")
            self.error = "Character is rigged.\nLive rig deform is not supported"

    def __bool__(self):
        return self.obj is not None

    # these methods are overriden in subclass
    @staticmethod
    def get_L1():
        return ""
    @staticmethod
    def update_L1():
        pass
    @staticmethod
    def get_morphs_L2():
        pass
    @staticmethod
    def prop_get(_name):
        return 0
    @staticmethod
    def prop_set_internal(_name, _value):
        pass
    @staticmethod
    def has_morphs():
        return False

    def _get_co(self, i):
        return self.obj.data.vertices[i].co

    def update_L1_idx(self):
        try:
            self.L1_idx = next((i for i, item in enumerate(self.L1_list) if item[0] == self.L1))
        except StopIteration:
            pass

    def set_L1(self, L1):
        result = self._set_L1(L1)
        if result:
            self.update_L1_idx()
        return result

    def _set_L1(self, L1):
        if L1 not in self.morphs_l1:
            return False
        self.L1 = L1
        self.update_L1()
        self.create_charmorphs_L2()
        self.apply_materials(self.char.types.get(L1, {}).get("mtl_props"))
        self.update()
        return True

    def set_L1_by_idx(self, idx):
        if idx == self.L1_idx or idx >= len(self.L1_list):
            return
        self.L1_idx = idx
        self._set_L1(self.L1_list[idx][0])

    def do_update(self):
        fitting.get_fitter(self).refit_all()
        self._recalc_sliding_joints()

    def update(self):
        if self.upd_lock:
            return
        self.do_update()

    # TODO: Use lazy props
    def get_basis(self):
        return get_basis(self.obj, False)

    def get_basis_alt_topo(self):
        return self.get_basis()

    def lock(self):
        self.upd_lock = True

    def unlock(self):
        self.upd_lock = False
        self.update()

    def apply_materials(self, data):
        materials.apply_props(data, self.mtl_props)

    def add_morph_l2(self, name, data, minval = 0, maxval = 1):
        nameParts = name.split("_")

        signIdx = -1
        if len(nameParts) == 3:
            signArr = nameParts[2].split("-")
            signIdx = convertSigns(signArr)

        if signIdx < 0:
            self.morphs_l2[name] = Morph([data], minval, maxval)
            return

        names = nameParts[1].split("-")

        if len(names) == 0 or len(names) != len(signArr):
            logger.error("Invalid L2 morph name: %s, skipping", name)
            return

        morph_name = nameParts[0]+"_"+nameParts[1]
        cnt = 2 ** len(names)

        if len(names) == 1:
            arr = self.morphs_l2
        else:
            arr = self.morphs_combo

        if morph_name in arr:
            morph = arr[morph_name]
            if len(morph.data) != cnt:
                logger.error("L2 combo morph conflict: different dimension count on %s, skipping", name)
                return
        else:
            morph = Morph([None] * cnt)
            arr[morph_name] = morph

        for sign in signArr:
            if sign == "min":
                morph.min = min(morph.min, -maxval)
            elif sign == "max":
                morph.max = max(morph.max, maxval)

        morph.data[signIdx] = data

    def apply_morph_data(self, data, preset_mix):
        morph_props = data.get("morphs", {})
        meta_props = data.get("meta", {})
        self.lock()
        try:
            for name, morph in self.morphs_l2.items():
                if morph is None:
                    continue
                value = morph_props.get(name, 0)
                if preset_mix:
                    value = (value+self.prop_get(name))/2
                self.prop_set(name, value)
            for name in self.meta_dict():
                # TODO handle preset_mix?
                value = meta_props.get(name, 0)
                self.meta_prev[name] = value
                self.obj.data["cmorph_meta_" + name] = value
        finally:
            self.unlock()
        materials.apply_props(data.get("materials"))

    # Reset all meta properties to 0
    def reset_meta(self):
        d = self.obj.data
        for k in self.meta_dict():
            self.meta_prev[k] = 0
            pname = "cmorph_meta_" + k
            if pname in d:
                del d[pname]

    @staticmethod
    def _calc_meta_val(coeffs, val):
        if not coeffs:
            return 0
        return coeffs[1]*val if val > 0 else -coeffs[0]*val

    def _calc_meta_abs_val(self, prop):
        return sum(self._calc_meta_val(self.meta_dict()[meta_prop].get("morphs", {}).get(prop), val)
            for meta_prop, val in self.meta_prev.items())

    def meta_get(self, name):
        return self.obj.data.get("cmorph_meta_" + name, 0.0)

    def meta_dict(self) -> dict:
        return self.char.morphs_meta

    def meta_prop(self, name, data):
        pname = "cmorph_meta_" + name

        value = self.obj.data.get(pname, 0.0)
        self.meta_prev[name] = value

        def setter(_, new_value):
            nonlocal value
            value = new_value
            self.obj.data[pname] = value

        def update(_, context):
            prev_value = self.meta_prev.get(name, 0.0)
            if value == prev_value:
                return
            self.meta_prev[name] = value
            ui = context.window_manager.charmorph_ui

            self.version += 1
            for prop, coeffs in data.get("morphs", {}).items():
                if prop not in self.morphs_l2:
                    continue

                if not ui.relative_meta:
                    self.prop_set_internal(prop, self._calc_meta_abs_val(prop))
                    continue

                propval = self.prop_get(prop)

                val_prev = self._calc_meta_val(coeffs, prev_value)
                val_cur = self._calc_meta_val(coeffs, value)

                # assign absolute prop value if current property value is out of range
                # or add a delta if it is within (-0.999 .. 0.999)
                sign = -1 if val_cur-val_prev < 0 else 1
                if propval*sign < -0.999 and val_prev*sign < -1:
                    propval = self._calc_meta_abs_val(prop)
                else:
                    propval += val_cur-val_prev
                self.prop_set_internal(prop, propval)

            self.update()

            if ui.meta_materials != "N":
                for k, coeffs in data.get("materials", {}).items():
                    if materials.props and k in materials.props:
                        if ui.meta_materials == "R":
                            materials.props[k].default_value += self._calc_meta_val(coeffs, value)-self._calc_meta_val(coeffs, prev_value)
                        else:
                            materials.props[k].default_value = self._calc_meta_val(coeffs, value)

        return bpy.props.FloatProperty(
            name=name,
            min=-1.0, max=1.0,
            precision=3,
            get=lambda _: self.meta_get(name),
            set=setter,
            update=update)

    def prop_set(self, name, value):
        self.version += 1
        self.prop_set_internal(name, value)
        self.update()

    def morph_prop(self, name, morph):
        return bpy.props.FloatProperty(
            name=name,
            soft_min=morph.min, soft_max=morph.max,
            precision=3,
            get=lambda _: self.prop_get(name),
            set=lambda _, value: self.prop_set(name, max(min(value, morph.max), morph.min) if self.clamp else value)
        )

    def get_presets(self):
        if not self.char:
            return {}
        result = self.char.presets.copy()
        result.update(self.char.load_presets("presets/" + self.L1))
        return result

    def update_presets(self):
        self.presets = self.get_presets()
        self.presets_list = Morpher.presets_list + [(name, name, "") for name in sorted(self.presets.keys())]

    def update_morph_categories(self):
        if not self.char.no_morph_categories:
            self.categories = [(name, name, "") for name in sorted(set(morph_category_name(morph) for morph, val in self.morphs_l2.items() if val is not None))]

    # Create a property group with all L2 morphs
    def create_charmorphs_L2(self):
        del_charmorphs_L2()
        self.get_morphs_L2()
        self.update_morph_categories()
        self.update_presets()
        self._init_sliding_joints()
        self.meta_prev.clear()

        props = {}
        if self.morphs_l2:
            props.update(prefixed_prop("prop_", self.morph_prop(k, v)) for k, v in self.morphs_l2.items() if v is not None)
            props.update(prefixed_prop("meta_", self.meta_prop (k, v)) for k, v in self.meta_dict().items())
        if self.sliding_joints:
            props.update(prefixed_prop("sj_", self.sliding_prop(k)) for k in self.sliding_joints.keys())
        if not props:
            return

        propGroup = type("CharMorpher_Dyn_PropGroup",(bpy.types.PropertyGroup,), {"__annotations__": props})
        bpy.utils.register_class(propGroup)
        bpy.types.WindowManager.charmorphs = bpy.props.PointerProperty(
            type=propGroup, options={"SKIP_SAVE"})

    def set_clamp(self, clamp):
        self.clamp = clamp

    # Sliding joint calculation
    def _calc_avg_dists(self, vert_pairs):
        if not vert_pairs:
            return 1
        return sum((self._get_co(a)-self._get_co(b)).length for a, b in vert_pairs)/len(vert_pairs)

    def _calc_sliding_influence(self, data):
        result = data.get("influence")
        if result is not None:
            return result
        calc = data["calc"]
        if not calc:
            return 0

        if isinstance(calc, str):
            # Check for eval safety. Attacks like 9**9**9 are still possible, but quite useless
            if eval_unsafe.search(calc):
                logger.error("bad calc: %s", calc)
                return 0
            calc = compile(calc, "", "eval")
            data["calc"] = calc

        vals = {}
        for k, v in data.items():
            if k.startswith("verts_"):
                vals[k] = self._calc_avg_dists(v)
        try:
            return eval(calc, {"__builtins__": None}, vals)
        except Exception as e:
            logger.error("bad calc: %s", e)
            return 0

    def _recalc_sliding_joints(self):
        for k, v in self.char.sliding_joints.items():
            if k in self.sliding_joints and "calc" in v:
                self.sliding_joints[k] = self._calc_sliding_influence(v)

    def _init_sliding_joints(self):
        if self.rig:
            self.sliding_joints = {name: self._get_sliding_influence(name)
                for name in self.char.sliding_joints
                if name.startswith(self.rig_name + "_")
            }
        else:
            self.sliding_joints = {k: self._calc_sliding_influence(v) for k,v in self.char.sliding_joints.items()}

    # Sliding joint handling
    def _iterate_sliding_constraints(self, name):
        item = self.char.sliding_joints.get(name)
        if not item:
            return
        for _, lower_bone, side in rigging.iterate_sliding_joints_item(item):
            bone = self.rig.pose.bones.get(f"MCH-{lower_bone}{side}")
            if not bone:
                continue
            c = bone.constraints
            if not c or c[0].type != "COPY_ROTATION":
                continue
            yield c[0]

    def _get_sliding_influence(self, name):
        for c in self._iterate_sliding_constraints(name):
            return c.influence

    def set_sliding_influence(self, name, value):
        self.sliding_joints[name] = value
        if self.rig:
            for c in self._iterate_sliding_constraints(name):
                c.influence = value

    def sliding_joints_by_rig(self, rig):
        return (name for name in self.sliding_joints
            if name.startswith(rig + "_"))

    def sliding_prop(self, name):
        return bpy.props.FloatProperty(
            name=name,
            min=0, soft_max = 0.2, max=1.0,
            precision=3,
            get=lambda _: self.sliding_joints.get(name, 0),
            set=lambda _, value: self.set_sliding_influence(name, value)
        )

null_morpher = Morpher(None)
null_morpher.lock()

morpher = null_morpher

from . import morphers

def get_morpher(obj) -> Morpher:
    logger.debug("switching object to %s", obj.name if obj else "")

    materials.update_props(obj)

    if obj.data.get("cm_morpher") == "ext" or obj.data.get("cm_alt_topo"):
        return morphers.NumpyMorpher(obj)
    return morphers.ShapeKeysMorpher(obj)

def update_morpher(m: Morpher):
    global last_object, morpher
    morpher = m
    last_object = m.obj

    ui = bpy.context.window_manager.charmorph_ui

    if ui.fin_rig not in m.char.armature:
        if m.char.default_armature:
            ui.fin_rig = m.char.default_armature
        else:
            ui.fin_rig = "-"

    if not m.L1 and m.char.default_type:
        m.set_L1(m.char.default_type)

    ui.morph_category = "<None>"

    if not m.morphs_l2:
        m.create_charmorphs_L2()

def recreate_charmorphs():
    global morpher
    if not morpher:
        return
    morpher = get_morpher(morpher.obj)
    morpher.create_charmorphs_L2()

def create_charmorphs(obj):
    global last_object, morpher
    last_object = obj
    if obj.type != "MESH":
        return
    if morpher.obj == obj:
        return

    update_morpher(get_morpher(obj))

# Delete morphs property group
def del_charmorphs_L2():
    if not hasattr(bpy.types.WindowManager, "charmorphs"):
        return
    cm = bpy.types.WindowManager.charmorphs
    if isinstance(cm, tuple):
        propGroup = cm[1]['type']
    else:
        propGroup = cm.keywords['type']
    del bpy.types.WindowManager.charmorphs
    bpy.utils.unregister_class(propGroup)

def del_charmorphs():
    global last_object, morpher
    last_object = None
    morpher = null_morpher
    del_charmorphs_L2()

def bad_object():
    if not morpher:
        return False
    try:
        return bpy.data.objects.get(morpher.obj.name) is not morpher.obj
    except ReferenceError:
        logger.warning("Current morphing object is bad, resetting...")
        return True

class OpResetChar(bpy.types.Operator):
    bl_idname = "charmorph.reset_char"
    bl_label = "Reset character"
    bl_description = "Reset all unavailable character morphs"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and morpher.char

    def execute(self, _):
        obj = morpher.obj
        obj.data["cm_morpher"] = "ext"
        new_morpher = get_morpher(obj)
        if new_morpher.error or not new_morpher.has_morphs():
            if new_morpher.error:
                self.report({'ERROR'}, new_morpher.error)
            else:
                self.report({'ERROR'}, "Still no morphs found")
            del obj.data["cm_morpher"]
            return {"CANCELLED"}
        update_morpher(new_morpher)
        return {"FINISHED"}

class OpBuildAltTopo(bpy.types.Operator):
    bl_idname = "charmorph.build_alt_topo"
    bl_label = "Build alt topo"
    bl_description = "Build alt topo from modified character mesh"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return morpher and morpher.alt_topo_buildable and morpher.has_morphs()

    def execute(self, context):
        ui = context.window_manager.charmorph_ui
        obj = morpher.obj
        btype = ui.alt_topo_build_type
        sk = obj.data.shape_keys
        has_sk = bool(sk and sk.key_blocks)
        if btype == "K" and has_sk:
            obj.data["cm_alt_topo"] = "sk"
            update_morpher(get_morpher(obj))
            return {"FINISHED"}
        weights = fit_calc.ReverseFitCalculator(morpher).get_weights(obj)
        result = fit_calc.calc_fit(morpher.full_basis - morpher.get_final(), *weights)
        result += utils.get_morphed_numpy(obj)
        result = result.reshape(-1)
        if btype == "K":
            basis = obj.shape_key_add(name="Basis", from_mix=False)
            final = obj.shape_key_add(name="charmorph_final", from_mix=False)
            basis.data.foreach_set("co", result)
            obj.data["cm_alt_topo"] = "sk"
            final.value = 1
        else:
            mesh = obj.data.copy()
            obj.data["cm_alt_topo"] = mesh
            if has_sk:
                old_mesh = obj.data
                obj.data = mesh
                while mesh.shape_keys and mesh.shape_keys.key_blocks:
                    obj.shape_key_remove(mesh.shape_keys.key_blocks[0])
                obj.data = old_mesh
            mesh.vertices.foreach_set("co", result)

        update_morpher(get_morpher(obj))
        return {"FINISHED"}

class UIProps:
    relative_meta: bpy.props.BoolProperty(
        name="Relative meta props",
        description="Adjust meta props relatively",
        default=True)
    meta_materials: bpy.props.EnumProperty(
        name="Materials",
        description="How changing meta properties will affect materials",
        default="A",
        items=[
            ("N", "None", "Don't change materials"),
            ("A", "Absolute", "Change materials according to absolute value of meta property"),
            ("R", "Relative", "Change materials according to relative value of meta property")])
    morph_filter: bpy.props.StringProperty(
        name="Filter",
        description="Show only morphs mathing this name",
        options={"TEXTEDIT_UPDATE"},
    )
    morph_clamp: bpy.props.BoolProperty(
        name="Clamp props",
        description="Clamp properties to (-1..1) so they remain in realistic range",
        get=lambda _: morpher.clamp,
        set=lambda _, value: morpher.set_clamp(value),
        update=lambda ui, _: morpher.update())
    morph_l1: bpy.props.EnumProperty(
        name="Type",
        description="Choose character type",
        items=lambda ui, _: morpher.L1_list,
        get=lambda _: morpher.L1_idx,
        set=lambda _, value: morpher.set_L1_by_idx(value),
        options={"SKIP_SAVE"})
    morph_category: bpy.props.EnumProperty(
        name="Category",
        items=lambda ui, _: [("<None>", "<None>", "Hide all morphs"), ("<All>", "<All>", "Show all morphs")] + morpher.categories,
        description="Select morphing categories to show")
    morph_preset: bpy.props.EnumProperty(
        name="Presets",
        items=lambda ui, _: morpher.presets_list,
        description="Choose morphing preset",
        update=lambda ui, _: morpher.apply_morph_data(morpher.presets.get(ui.morph_preset, {}), ui.morph_preset_mix))
    morph_preset_mix: bpy.props.BoolProperty(
        name="Mix with current",
        description="Mix selected preset with current morphs",
        default=False)
    alt_topo_build_type: bpy.props.EnumProperty(
        name="Alt topo type",
        description="Type of alt topo to build",
        default="P",
        items=[
            ("K", "Shapekey", "Store alt topo basis in shapekey"),
            ("P", "Separate mesh", "Store alt topo basis in separate mesh")])


class CHARMORPH_PT_Morphing(bpy.types.Panel):
    bl_label = "Morphing"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 2

    @classmethod
    def poll(cls, context):
        global last_object, morpher
        if context.mode != "OBJECT":
            if morpher:
                last_object = None
                morpher = null_morpher
            return False
        return morpher

    def draw(self, context):
        m = morpher
        ui = context.window_manager.charmorph_ui

        if m.error:
            self.layout.label(text="Morphing is impossible:")
            col = self.layout.column()
            for line in m.error.split("\n"):
                col.label(text=line)
            if m.alt_topo_buildable:
                col = self.layout.column()
                col.label(text="It seems you've changed object's topology")
                col.label(text="You can try to build alt topo")
                col.label(text="to continue morphing")
                self.layout.operator("charmorph.build_alt_topo")
                self.layout.prop(ui, "alt_topo_build_type")

            return

        if not hasattr(context.window_manager, "charmorphs") or not m.has_morphs():
            if m.char:
                col = self.layout.column(align=True)
                col.label(text="Object is detected as")
                col.label(text="valid CharMorph character,")
                col.label(text="but the morphing data was removed")
                if m.obj.data.get("cm_morpher") == "ext":
                    return
                col.separator()
                col.label(text="You can reset the character")
                col.label(text="to resume morphing")
                col.separator()
                col.operator('charmorph.reset_char')
            else:
                self.layout.label(text="No morphing data found")
            return

        self.layout.label(text="Character type")
        col = self.layout.column(align=True)
        if m.morphs_l1:
            col.prop(ui, "morph_l1")

        col = self.layout.column(align=True)
        col.prop(ui, "morph_preset")
        col.prop(ui, "morph_preset_mix")

        col.separator()

        morphs = context.window_manager.charmorphs
        meta_morphs = m.meta_dict().keys()
        if meta_morphs:
            self.layout.label(text="Meta morphs")
            col = self.layout.column(align=True)
            col.prop(ui, "meta_materials")
            col.prop(ui, "relative_meta")

            for prop in meta_morphs:
                col.prop(morphs, "meta_" + prop, slider=True)

        self.layout.prop(ui, "morph_clamp")

        morph_list = m.morphs_l2.keys()

        self.layout.separator()

        if m.categories:
            self.layout.label(text="MORE MORPHS HERE:")
            self.layout.prop(ui, "morph_category")
            if ui.morph_category == "<None>":
                return

        self.layout.prop(ui, "morph_filter")
        col = self.layout.column(align=True)
        if not m.char.custom_morph_order:
            morph_list = sorted(morph_list)
        for prop in morph_list:
            if m.char.custom_morph_order and m.morphs_l2[prop] is None:
                col.separator()
            elif ui.morph_category == "<All>" or prop.startswith(ui.morph_category):
                if ui.morph_filter.lower() in prop.lower():
                    col.prop(morphs, "prop_" + prop, slider=True)

classes = [CHARMORPH_PT_Morphing, OpResetChar, OpBuildAltTopo]
