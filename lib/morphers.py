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

import re, logging, numpy

import bpy # pylint: disable=import-error
import mathutils # pylint: disable=import-error

from . import charlib, morphs, materials, fitting, rigging, utils

logger = logging.getLogger(__name__)

sep_re = re.compile(r"[ _-]")
eval_unsafe = re.compile(r"__|[:;,({'\"\[]")

if isinstance(bpy.props.StringProperty(), tuple):
    # Before Blender 2.93 properties were tuples
    def prefixed_prop(prefix, prop):
        return (prefix + prop[1]["name"], prop)
else:
    # Blender version >= 2.93
    def prefixed_prop(prefix, prop):
        return (prefix + prop.keywords["name"], prop)

def morph_category_name(name):
    m = sep_re.search(name)
    if m:
        return name[:m.start()]
    return name

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
    morphs_l2: list[morphs.MinMaxMorph] = []
    materials = materials.Materials(None)
    sliding_joints = {}
    L1_idx = 0

    presets = {}
    presets_list = [("_", "(reset)", "")]

    def __init__(self, obj):
        self.obj = obj
        self.char = charlib.obj_char(obj)
        self.morphs_l1 = {}
        self.meta_prev = {}
        self.fitter = fitting.Fitter(self)

        self._init_storage()
        self.L1 = self.get_L1()
        self.L1_list = [(name, self.char.types.get(name, {}).get("title", name), "") for name in sorted(self.morphs_l1.keys())]
        self.update_L1_idx()
        self.update_morphs_L2()
        if self.obj:
            self.rig = obj.find_armature()
        if self.rig:
            self.rig_name = self.rig.data.get("charmorph_rig_type","")
            self.error = "Character is rigged.\nLive rig deform is not supported"

    def __bool__(self):
        return self.obj is not None

    # these methods are overriden in subclass
    @staticmethod
    def _init_storage():
        pass
    @staticmethod
    def get_L1():
        return ""
    @staticmethod
    def update_L1():
        pass
    def get_morphs_L2(self):
        return self.morphs_l2
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

    def update_morphs_L2(self):
        self.morphs_l2 = self.get_morphs_L2()
        if not self.char.custom_morph_order:
            self.morphs_l2.sort(key=lambda morph: morph.name)

    def _set_L1(self, L1):
        if L1 not in self.morphs_l1:
            return False
        self.L1 = L1
        self.update_L1()
        self.create_charmorphs_L2()
        self.materials.apply(self.char.types.get(L1, {}).get("mtl_props"))
        self.update()
        return True

    def set_L1_by_idx(self, idx):
        if idx == self.L1_idx or idx >= len(self.L1_list):
            return
        self.L1_idx = idx
        self._set_L1(self.L1_list[idx][0])

    def do_update(self):
        self.fitter.refit_all()
        self._recalc_sliding_joints()

    def update(self):
        if self.upd_lock:
            return
        self.do_update()

    @utils.lazyproperty
    def full_basis(self):
        return self.char.np_basis if self.char.np_basis is not None else charlib.get_basis(self.obj)

    def get_basis_alt_topo(self):
        return self.full_basis

    def lock(self):
        self.upd_lock = True

    def unlock(self):
        self.upd_lock = False
        self.update()

    def apply_morph_data(self, data, preset_mix):
        if data is None:
            self.reset_meta()
            data = {}
        else:
            meta_props = data.get("meta", {})
            for name in self.char.morphs_meta:
                # TODO handle preset_mix?
                value = meta_props.get(name, 0)
                self.meta_prev[name] = value
                self.obj.data["cmorph_meta_" + name] = value
        morph_props = data.get("morphs", {})
        self.lock()
        try:
            for morph in self.morphs_l2:
                if not morph.name:
                    continue
                value = morph_props.get(morph.name, 0)
                if preset_mix:
                    value = (value+self.prop_get(morph.name))/2
                self.prop_set(morph.name, value)
        finally:
            self.unlock()
        self.materials.apply(data.get("materials"))

    # Reset all meta properties to 0
    def reset_meta(self):
        d = self.obj.data
        for k, v in self.char.morphs_meta.items():
            self.materials.apply((name, 0) for name in v.get("materials", ()))
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
        return sum(self._calc_meta_val(self.char.morphs_meta[meta_prop].get("morphs", {}).get(prop), val)
            for meta_prop, val in self.meta_prev.items())

    def meta_get(self, name):
        return self.obj.data.get("cmorph_meta_" + name, 0.0)

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

            mtl_items = data.get("materials", {}).items()
            if ui.meta_materials == "R":
                for pname, coeffs in mtl_items:
                    prop = self.materials.props.get(pname)
                    if prop:
                        prop.default_value += self._calc_meta_val(coeffs, value)-self._calc_meta_val(coeffs, prev_value)
            elif ui.meta_materials == "A":
                self.materials.apply((name, self._calc_meta_val(coeffs, value)) for name, coeffs in mtl_items)

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

    def morph_prop(self, morph):
        return bpy.props.FloatProperty(
            name=morph.name,
            soft_min=morph.min, soft_max=morph.max,
            precision=3,
            get=lambda _: self.prop_get(morph.name),
            set=lambda _, value: self.prop_set(morph.name, max(min(value, morph.max), morph.min) if self.clamp else value)
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
            self.categories = [(name, name, "") for name in sorted(set(morph_category_name(morph.name) for morph in self.morphs_l2 if morph.name))]

    # Create a property group with all L2 morphs
    def create_charmorphs_L2(self):
        del_charmorphs_L2()
        self.update_morph_categories()
        self.update_presets()
        self._init_sliding_joints()
        self.meta_prev.clear()

        props = {}
        if self.morphs_l2:
            props.update(prefixed_prop("prop_", self.morph_prop(morph)) for morph in self.morphs_l2 if morph.name)
            props.update(prefixed_prop("meta_", self.meta_prop(k, v)) for k, v in self.char.morphs_meta.items())
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

def get_combo_item_value(arr_idx, values):
    return max(sum(val*((arr_idx >> val_idx & 1)*2-1) for val_idx, val in enumerate(values)), 0)

def enum_combo_names(name):
    nameParts = name.split("_")
    return (f"{nameParts[0]}_{name}" for name in nameParts[1].split("-"))

class ShapeKeysComboMorpher:
    def __init__(self, arr, dims):
        self.arr = arr
        self.coeff = 2 / len(arr)
        self.values = [self.get_combo_prop_value(i) for i in range(dims)]

    def get_combo_prop_value(self, idx):
        return sum(0 if sk is None else sk.value * ((arr_idx >> idx & 1)*2-1) for arr_idx, sk in enumerate(self.arr))

    def get(self, idx):
        val = self.get_combo_prop_value(idx)
        self.values[idx] = val
        return val

    def set(self, idx, value):
        self.values[idx] = value
        for arr_idx, sk in enumerate(self.arr):
            sk.value = get_combo_item_value(arr_idx, self.values) * self.coeff

class ShapeKeysMorpher(Morpher):
    morphs_l2_dict = {}
    def update_L1(self):
        for name, sk in self.morphs_l1.items():
            sk.value = 1 if name == self.L1 else 0

        # clear old L2 shape keys
        if not self.obj.data.shape_keys:
            return
        for sk in self.obj.data.shape_keys.key_blocks:
            if sk.name.startswith("L2_") and not sk.name.startswith("L2__") and not sk.name.startswith(f"L2_{self.L1}_"):
                sk.value = 0

    # scan object shape keys and convert them to dictionary
    def get_L1(self):
        self.morphs_l1.clear()
        if not self.obj.data.shape_keys:
            return ""
        maxkey = ""
        maxval = 0
        for sk in self.obj.data.shape_keys.key_blocks:
            if not sk.name.startswith("L1_"):
                continue
            name = sk.name[3:]
            if sk.value > maxval:
                maxkey = name
                maxval = sk.value
            self.morphs_l1[name] = sk

        return maxkey

    def has_morphs(self):
        if self.morphs_l1:
            return True
        if not self.obj.data.shape_keys or not self.obj.data.shape_keys.key_blocks:
            return False
        for sk in self.obj.data.shape_keys.key_blocks:
            if sk.name.startswith("L2_"):
                return True
        return False

    def get_morphs_L2(self):
        if not self.obj.data.shape_keys:
            return []

        combiner = morphs.MorphCombiner()
        def load_shape_keys_by_prefix(prefix):
            for sk in self.obj.data.shape_keys.key_blocks:
                if sk.name.startswith(prefix):
                    combiner.add_morph(morphs.MinMaxMorphData(sk.name[len(prefix):], sk.slider_min, sk.slider_max), sk)

        load_shape_keys_by_prefix("L2__")

        if len(self.L1) > 0:
            load_shape_keys_by_prefix(f"L2_{self.L1}_")

        for k, v in combiner.morphs_combo.items():
            names = list(enum_combo_names(k))
            combo_morpher = ShapeKeysComboMorpher(v.data, len(names))
            for i, name in enumerate(names):
                morph = combiner.morphs_dict[name]
                if morph.data is None:
                    morph.data = []
                morph.data.append((combo_morpher, i))

        self.morphs_l2_dict = combiner.morphs_dict
        return combiner.morphs_list


    @staticmethod
    def is_combo_morph(morph):
        return len(morph.data) > 0 and isinstance(morph.data[0], tuple)

    @staticmethod
    def _prop_set_combo(morph, value):
        for combo_morpher, idx in morph.data:
            combo_morpher.set(idx, value)

    @staticmethod
    def _prop_set_simple(morph, value):
        if len(morph.data) == 1:
            morph.data[0].value = value
            return
        skmin, skmax = tuple(morph.data)
        if value < 0:
            if skmax is not None: skmax.value = 0
            if skmin is not None: skmin.value = -value
        else:
            if skmin is not None: skmin.value = 0
            if skmax is not None: skmax.value = value

    def prop_set_internal(self, name, value):
        morph = self.morphs_l2_dict[name]
        if self.is_combo_morph(morph):
            self._prop_set_combo(morph, value)
        else:
            self._prop_set_simple(morph, value)

    def prop_get(self, name):
        morph = self.morphs_l2_dict[name]
        if self.is_combo_morph(morph):
            return sum(combo_morpher.get(idx) for combo_morpher, idx in morph.data)/len(morph.data)
        if len(morph.data) == 1:
            return morph.data[0].value
        skmin, skmax = tuple(morph.data)
        return (0 if skmax is None else skmax.value) - (0 if skmin is None else skmin.value)

    def get_diff(self):
        result = utils.get_morphed_numpy(self.obj)
        result -= self.full_basis
        return result

class NumpyMorpher(Morpher):
    storage: morphs.MorphStorage
    basis: numpy.ndarray = None
    morphed: numpy.ndarray = None
    morphs_combo: dict[str, morphs.MinMaxMorph] = {}

    def __init__(self, obj, storage=None):
        self.storage = storage
        super().__init__(obj)
        if obj.data.get("cm_alt_topo"):
            self.alt_topo = True
            self.alt_topo_basis = charlib.get_basis(obj)
        else:
            self.alt_topo_basis = self.full_basis
        if len(self.alt_topo_basis) != len(obj.data.vertices):
            self.error = f"Vertex count mismatch {len(self.alt_topo_basis)} != {len(obj.data.vertices)}"
            if not self.alt_topo and self.char.faces is not None:
                self.alt_topo_buildable = True

    def _init_storage(self):
        if self.storage is None:
            self.storage = morphs.MorphStorage(self.char)

    def has_morphs(self):
        return self.obj.data.get("cm_morpher") == "ext" # HACK: used just to prevent morphing when morphing data was removed

    def update_L1(self):
        if self.L1:
            self.obj.data["cmorph_L1"] = self.L1
            self.basis = self.storage.resolve_lazy_L1(self.morphs_l1.get(self.L1))
            if self.basis is not None:
                self.morphs_l1[self.L1] = self.basis

        if self.basis is None:
            self.basis = self.full_basis

    def get_L1(self):
        self.morphs_l1 = {morph.name: self.storage.get_lazy(1, morph.name) for morph in self.storage.enum(1)}
        L1 = self.obj.data.get("cmorph_L1", "")
        if L1 not in self.morphs_l1:
            L1 = ""
        return L1

    def get_morphs_L2(self):
        combiner = morphs.MorphCombiner()
        for morph in self.storage.enum(2):
            combiner.add_morph(morph, self.storage.get_lazy(2, morph.name))
        if self.L1:
            for morph in self.storage.enum(2, self.L1):
                combiner.add_morph(morph, self.storage.get_lazy(2, self.L1, morph.name))
        self.morphs_combo = combiner.morphs_combo
        return combiner.morphs_list

    def _do_all_morphs(self):
        if self.basis is None:
            self.update_L1()
        if self.morphed is None:
            self.morphed = self.basis.copy()
        else:
            self.morphed[:] = self.basis

        for morph in self.morphs_l2:
            morph.apply(self.morphed, self.prop_get_clamped(morph.name))

        for name, morph in self.morphs_combo.items():
            values = [self.prop_get_clamped(morph_name) for morph_name in enum_combo_names(name)]
            data = morph.data
            coeff = 2 / len(data)
            for i in range(len(data)):
                morph.get_morph(i).apply(self.morphed, get_combo_item_value(i, values) * coeff)

    def do_update(self):
        self._do_all_morphs()

        if not self.alt_topo:
            utils.get_target(self.obj).foreach_set("co", self.morphed.reshape(-1))
            self.obj.data.update()

        super().do_update()

    def prop_get(self, name):
        return self.obj.data.get("cmorph_L2_"+name, 0.0)

    # Clamp to -1..1 only for combo props
    def prop_get_clamped(self, name):
        if not name:
            return 0.0
        val = self.prop_get(name)
        if self.clamp:
            return max(min(val, 1), -1)
        return val

    def prop_set_internal(self, name, value):
        self.obj.data["cmorph_L2_" + name] = value

    def get_basis_alt_topo(self):
        return self.alt_topo_basis

    def _get_co(self, i):
        if self.morphed is None:
            self._do_all_morphs()
        return mathutils.Vector(self.morphed[i])

    def get_diff(self):
        if self.morphed is None:
            self._do_all_morphs()
        return self.morphed - self.full_basis

    def get_final(self):
        if not self.has_morphs():
            return None
        if self.morphed is None:
            self._do_all_morphs()
        return self.morphed

def get_morpher(obj, storage = None):
    if obj.data.get("cm_morpher") == "ext" or obj.data.get("cm_alt_topo"):
        return NumpyMorpher(obj, storage)
    return ShapeKeysMorpher(obj)
