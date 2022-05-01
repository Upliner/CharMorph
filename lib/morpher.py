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

import re, logging

import bpy  # pylint: disable=import-error

from . import morpher_cores, materials, fitting, sliding_joints

logger = logging.getLogger(__name__)

sep_re = re.compile(r"[ _-]")

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


def calc_meta_val(coeffs, val):
    if not coeffs:
        return 0
    return coeffs[1] * val if val > 0 else -coeffs[0] * val


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
    version = 0
    L1_idx = 0
    meta_prev: dict[str, float]
    categories: list[tuple[str, str, str]] = []

    presets: dict[str, dict] = {}
    presets_list = [("_", "(reset)", "")]

    def __init__(self, core: morpher_cores.MorpherCore):
        self.core = core
        self.meta_prev = {}

        self.L1_list = [
            (name, core.char.types.get(name, {}).get("title", name), "")
            for name in sorted(core.morphs_l1.keys())
        ]
        self.update_L1_idx()

        self.materials = materials.Materials(core.obj)
        if self.core.obj:
            self.fitter = fitting.Fitter(self.core, self)
        self.sj_calc = sliding_joints.SJCalc(self.core.char, self.core.rig, self.core.get_co)

    def __bool__(self):
        return self.core.obj is not None

    def __getattr__(self, attr):
        return getattr(self.core, attr)

    def update_L1_idx(self):
        try:
            self.L1_idx = next((i for i, item in enumerate(self.L1_list) if item[0] == self.core.L1))
        except StopIteration:
            pass

    def set_L1(self, L1, update=True):
        result = self._set_L1(L1, update)
        if result:
            self.update_L1_idx()
        return result

    def _set_L1(self, L1, update):
        if L1 not in self.core.morphs_l1:
            return False
        self.core.set_L1(L1)
        self.create_charmorphs_L2()
        self.materials.apply(self.core.char.types.get(L1, {}).get("mtl_props"))
        if update:
            self.update()
        return True

    def set_L1_by_idx(self, idx):
        if idx == self.L1_idx or idx >= len(self.L1_list):
            return
        self.L1_idx = idx
        self._set_L1(self.L1_list[idx][0], True)

    def update(self):
        self.core.update()
        self.fitter.refit_all()
        self.sj_calc.recalc()

    def apply_morph_data(self, data, preset_mix):
        if data is None:
            self.reset_meta()
            data = {}
        else:
            meta_props = data.get("meta", {})
            for name in self.core.char.morphs_meta:
                # TODO handle preset_mix?
                value = meta_props.get(name, 0)
                self.meta_prev[name] = value
                self.core.obj.data["cmorph_meta_" + name] = value
        morph_props = data.get("morphs", {})
        for morph in self.core.morphs_l2:
            if not morph.name:
                continue
            value = morph_props.get(morph.name, 0)
            if preset_mix:
                value = (value + self.core.prop_get(morph.name)) / 2
            self.core.prop_set(morph.name, value)
        self.materials.apply(data.get("materials"))
        self.update()

    # Reset all meta properties to 0
    def reset_meta(self):
        d = self.core.obj.data
        for k, v in self.core.char.morphs_meta.items():
            if bpy.context.window_manager.charmorph_ui.meta_materials != "N":
                self.materials.apply((name, 0) for name in v.get("materials", ()))
            self.meta_prev[k] = 0
            pname = "cmorph_meta_" + k
            if pname in d:
                del d[pname]

    def _calc_meta_abs_val(self, prop):
        return sum(
            calc_meta_val(self.core.char.morphs_meta[meta_prop].get("morphs", {}).get(prop), val)
            for meta_prop, val in self.meta_prev.items()
        )

    def meta_get(self, name):
        return self.core.obj.data.get("cmorph_meta_" + name, 0.0)

    def meta_prop(self, name, data):
        pname = "cmorph_meta_" + name

        value = self.core.obj.data.get(pname, 0.0)
        self.meta_prev[name] = value

        def setter(_, new_value):
            nonlocal value
            value = new_value
            self.core.obj.data[pname] = value

        def update(_, context):
            prev_value = self.meta_prev.get(name, 0.0)
            if value == prev_value:
                return
            self.meta_prev[name] = value
            ui = context.window_manager.charmorph_ui

            self.version += 1
            for prop, coeffs in data.get("morphs", {}).items():
                if not ui.relative_meta:
                    self.core.prop_set(prop, self._calc_meta_abs_val(prop))
                    continue

                propval = self.core.prop_get(prop)

                val_prev = calc_meta_val(coeffs, prev_value)
                val_cur = calc_meta_val(coeffs, value)

                # assign absolute prop value if current property value is out of range
                # or add a delta if it is within (-0.999 .. 0.999)
                sign = -1 if val_cur - val_prev < 0 else 1
                if propval * sign < -0.999 and val_prev * sign < -1:
                    propval = self._calc_meta_abs_val(prop)
                else:
                    propval += val_cur - val_prev
                self.core.prop_set(prop, propval)

            mtl_items = data.get("materials", {}).items()
            if ui.meta_materials == "R":
                for pname, coeffs in mtl_items:
                    prop = self.materials.props.get(pname)
                    if prop:
                        prop.default_value += calc_meta_val(coeffs, value) - calc_meta_val(coeffs, prev_value)
            elif ui.meta_materials == "A":
                self.materials.apply((name, calc_meta_val(coeffs, value)) for name, coeffs in mtl_items)

            self.update()

        return bpy.props.FloatProperty(
            name=name,
            min=-1.0, max=1.0,
            precision=3,
            get=lambda _: self.meta_get(name),
            set=setter,
            update=update)

    def prop_set(self, name, value):
        self.version += 1
        self.core.prop_set(name, value)
        self.update()

    def morph_prop(self, morph):
        return bpy.props.FloatProperty(
            name=morph.name,
            soft_min=morph.min, soft_max=morph.max,
            precision=3,
            get=lambda _: self.core.prop_get(morph.name),
            set=lambda _, value:
                self.prop_set(morph.name, max(min(value, morph.max), morph.min) if self.core.clamp else value)
        )

    def get_presets(self):
        if not self.core.char:
            return {}
        result = self.core.char.presets.copy()
        result.update(self.core.char.load_presets("presets/" + self.core.L1))
        return result

    def update_presets(self):
        self.presets = self.get_presets()
        self.presets_list = Morpher.presets_list + [(name, name, "") for name in sorted(self.presets.keys())]

    def update_morph_categories(self):
        if not self.core.char.no_morph_categories:
            self.categories = [
                (name, name, "") for name in sorted(set(
                    morph_category_name(morph.name)
                    for morph in self.core.morphs_l2 if morph.name))]

    # Create a property group with all L2 morphs
    def create_charmorphs_L2(self):
        del_charmorphs_L2()
        self.update_morph_categories()
        self.update_presets()

        props = {}
        if self.core.morphs_l2:
            props.update(prefixed_prop("prop_", self.morph_prop(morph)) for morph in self.core.morphs_l2 if morph.name)
            props.update(prefixed_prop("meta_", self.meta_prop(k, v)) for k, v in self.core.char.morphs_meta.items())
        props.update(prefixed_prop("sj_", prop) for prop in self.sj_calc.props())
        if not props:
            return

        propGroup = type("CharMorpher_Dyn_PropGroup", (bpy.types.PropertyGroup,), {"__annotations__": props})
        bpy.utils.register_class(propGroup)
        bpy.types.WindowManager.charmorphs = bpy.props.PointerProperty(
            type=propGroup, options={"SKIP_SAVE"})

    def set_clamp(self, clamp):
        self.core.clamp = clamp


null_morpher = Morpher(morpher_cores.MorpherCore(None))


def get(obj, storage=None):
    return Morpher(morpher_cores.get(obj, storage))
