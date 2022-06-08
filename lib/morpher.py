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

import re, typing, logging

import bpy, mathutils  # pylint: disable=import-error

from . import charlib, morpher_cores, materials, fitting, fit_calc, sliding_joints, rigging, utils

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


def match_armature(main: charlib.Armature, match: dict[str, charlib.Armature]) -> charlib.Armature:
    for k, values in match.items():
        if not isinstance(values, list):
            values = (values,)
        match_value = getattr(main, k)
        if not any(match_value == value for value in values):
            return False
    return True


def matching_armatures(main: charlib.Armature, candidates: list[charlib.Armature]) -> typing.Iterable[charlib.Armature]:
    return (c for c in candidates if any(match_armature(main, match) for match in c.match))


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


def _null_handler(_name, _value):
    pass


class Morpher:
    version = 0
    L1_idx = 0
    meta_prev: dict[str, float]
    categories: list[tuple[str, str, str]] = []
    rfc: fit_calc.RiggerFitCalculator = None

    presets: dict[str, dict] = {}
    presets_list = [("_", "(reset)", "")]

    def __init__(self, core: morpher_cores.MorpherCore, undo_handler=_null_handler):
        self.core = core
        self.push_undo = undo_handler
        self._obj_name = self.core.obj

        self.L1_list = [
            (name, core.char.types.get(name, {}).get("title", name), "")
            for name in sorted(core.morphs_l1.keys())
        ]
        self.update_L1_idx()

        self.rig = self.core.obj.find_armature() if self.core.obj else None
        self.rig_handler = self._get_rig_handler()
        self.is_slow = bool(self.rig_handler) and self.rig_handler.slow
        if self.rig and (not self.rig_handler or not self.rig_handler.is_morphable()):
            self.core.error = "Character is rigged.\nMorphing is not supported\n for this rig type"

        self.materials = materials.Materials(core.obj)
        if self.core.obj:
            self.fitter = fitting.Fitter(self)
        self.sj_calc = sliding_joints.SJCalc(self.core.char, self.rig, self.get_co)

    def __bool__(self):
        return self.core.obj is not None

    def __getattr__(self, attr):
        return getattr(self.core, attr)

    def get_co(self, i):
        return mathutils.Vector(self.core.get_final()[i])

    def update_L1_idx(self):
        try:
            self.L1_idx = next((i for i, item in enumerate(self.L1_list) if item[0] == self.core.L1))
        except StopIteration:
            pass

    def check_obj(self):
        result = self.core.check_obj()
        if self.rig_handler:
            result &= self.rig_handler.check_obj()
        return result

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
        if self.core.error:
            return
        self.core.update()
        self.fitter.refit_all()
        self.sj_calc.recalc()
        self.update_rig()

    def apply_morph_data(self, data, preset_mix):
        if data is None:
            self.reset_meta()
            data = {}
        else:
            meta_props = data.get("meta", {})
            for name in self.core.char.morphs_meta:
                # TODO handle preset_mix?
                value = meta_props.get(name, 0)
                self.core.obj.data["cmorph_meta_" + name] = value
        morph_props = data.get("morphs", {}).copy()
        for morph in self.core.morphs_l2:
            if not morph.name:
                continue
            value = morph_props.get(morph.name, 0)
            if preset_mix:
                value = (value + self.core.prop_get(morph.name)) / 2
            self.core.prop_set(morph.name, value)
            try:
                del morph_props[morph.name]
            except KeyError:
                pass
        for prop in morph_props:
            logger.error("Unknown morph name: %s", prop)
        self.materials.apply(data.get("materials"))
        self.update()

    # Reset all meta properties to 0
    def reset_meta(self):
        d = self.core.obj.data
        for k, v in self.core.char.morphs_meta.items():
            if bpy.context.window_manager.charmorph_ui.meta_materials != "N":
                self.materials.apply((name, 0) for name in v.get("materials", ()))
            pname = "cmorph_meta_" + k
            if pname in d:
                del d[pname]

    def meta_get(self, name):
        return self.core.obj.data.get("cmorph_meta_" + name, 0.0)

    def meta_prop(self, name, data):
        mprop = MetaProp(name, data, self)

        def setter(_, new_value):
            mprop.update_prev()
            mprop.value = new_value
            self.core.obj.data[mprop.pname] = new_value

        def update(_, context):
            if not self.check_obj():
                return
            ui = context.window_manager.charmorph_ui
            if mprop.update(ui.relative_meta, ui.meta_materials):
                self.version += 1
                self.update()

        return bpy.props.FloatProperty(
            name=name,
            min=-1.0, max=1.0,
            precision=3,
            get=lambda _: self.meta_get(name),
            set=setter,
            update=update)

    def prop_set(self, name, value):
        if not self.check_obj():
            return
        self.version += 1
        self.core.prop_set(name, value)
        self.update()

    def morph_prop(self, morph):
        saved_value = None

        def setter(_, value):
            nonlocal saved_value
            if self.core.clamp:
                value = max(min(value, morph.max), morph.min)
            saved_value = value
            self.prop_set(morph.name, value)

        return bpy.props.FloatProperty(
            name=morph.name,
            soft_min=morph.min, soft_max=morph.max,
            precision=3,
            get=lambda _: self.core.prop_get(morph.name),
            set=setter,
            update=lambda _ui, _ctx: self.push_undo(morph.name, saved_value)
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

    def get_rig_type(self):
        if self.rig is None:
            return None
        rig_type = self.rig.data.get("charmorph_rig_type")
        if not rig_type:
            rig_type = self.core.obj.data.get("charmorph_rig_type")
        return rig_type

    def _get_rig_handler(self, conf=None) -> rigging.RigHandler:
        if conf is None:
            conf = self.core.char.armature.get(self.get_rig_type())
        if conf is None:
            return None
        cls = rigging.handlers.get(conf.type)
        if not cls:
            return None
        return cls(self, self.rig, conf)

    def add_rig(self, conf: charlib.Armature):
        cls = rigging.handlers.get(conf.type)
        if not cls:
            raise rigging.RigException(rigging.rig_errors.get(
                conf.type, f"Rig type {conf.type} is not supported"))
        self.rig = utils.import_obj(self.core.char.path(conf.file), conf.obj_name, "ARMATURE")
        if not self.rig:
            raise rigging.RigException("Rig import failed")

        self.rig_handler = cls(self, self.rig, conf)
        return self.rig

    def update_rig(self):
        if self.rig is None:
            return
        self.run_rigger(run_func=self.rig_handler.on_update)

    def _run_rigger(self, rigger):
        bpy.context.view_layer.objects.active = self.rig
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            if not rigger.run(self.rig_handler.get_bones()):
                raise rigging.RigException("Rig fitting failed")
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")

    def run_rigger(self, manual_sculpt=False, run_func=None):
        if self.rig_handler is None:
            self.rig_handler = self._get_rig_handler()
        if self.rig_handler is None:
            return None
        if run_func is None:
            run_func = self._run_rigger

        verts = self.core.get_final()
        verts_alt = self.core.get_final_alt_topo()

        self.rig.data.use_mirror_x = False
        rigger = rigging.Rigger(bpy.context)
        conf = self.rig_handler.conf

        def add_char_joints(joints):
            if self.core.alt_topo and manual_sculpt:
                if self.rfc is None:
                    self.rfc = fit_calc.RiggerFitCalculator(self)
                joints = self.rfc.transfer_weights_get(self.core.obj, joints)
                rigger.joints_from_file(joints, verts_alt)
            else:
                rigger.joints_from_file(joints, verts)

        if conf.joints:
            add_char_joints(conf.joints)
        else:
            rigger.joints_from_char(self.core.obj, verts_alt)

        self.rig_handler.tweaks = rigging.unpack_tweaks(conf.parent.dirpath, conf.tweaks)
        rigger.set_opts(conf.bones)
        for afd in self.fitter.get_assets():
            for a in matching_armatures(conf, afd.conf.armature or ()):
                rigger.set_opts(a.bones)
                rigging.unpack_tweaks(a.parent.dirpath, a.tweaks, self.rig_handler.tweaks)
                for j in a.asset_joints:
                    if j.verts == "char":
                        add_char_joints(j.file)
                    elif j.verts == "asset":
                        rigger.joints_from_file(j.file, afd.geom.verts)
                    else:
                        logger.error('Unknown verts source "%s" for asset %s', j["verts"], afd.obj.name)

        run_func(rigger)
        self.rig_handler.after_update()

        return rigger


def _calc_meta_val(coeffs, val):
    if not coeffs:
        return 0
    return coeffs[1] * val if val > 0 else -coeffs[0] * val


class MetaProp:
    prev_value: float

    def __init__(self, name, data, morpher: Morpher):
        self.name = name
        self.data = data
        self.pname = "cmorph_meta_" + name
        self.morpher = morpher

        self.update_prev()
        self.value = self.prev_value

    def update_prev(self):
        self.prev_value = self.morpher.core.obj.data.get(self.pname, 0.0)

    def _calc_meta_abs_val(self, prop, mvals):
        metadict = self.morpher.core.char.morphs_meta
        return sum(
            _calc_meta_val(metadict[meta_prop].get("morphs", {}).get(prop), val)
            for meta_prop, val in mvals
        )

    def update(self, relative_meta, meta_materials):
        if self.value == self.prev_value:
            return False
        self.morpher.push_undo(self.name, self.value)
        mvals = {(k, self.morpher.meta_get(k)) for k in self.morpher.core.char.morphs_meta}

        for prop, coeffs in self.data.get("morphs", {}).items():
            if not relative_meta:
                self.morpher.core.prop_set(prop, self._calc_meta_abs_val(prop, mvals))
                continue

            propval = self.morpher.core.prop_get(prop)

            val_prev = _calc_meta_val(coeffs, self.prev_value)
            val_cur = _calc_meta_val(coeffs, self.value)

            # assign absolute prop value if current property value is out of range
            # or add a delta if it is within (-0.999 .. 0.999)
            sign = -1 if val_cur - val_prev < 0 else 1
            if propval * sign < -0.999 and val_prev * sign < -1:
                propval = self._calc_meta_abs_val(prop, mvals)
            else:
                propval += val_cur - val_prev
            self.morpher.core.prop_set(prop, propval)

        mtl_items = self.data.get("materials", {}).items()
        if meta_materials == "R":
            for pname, coeffs in mtl_items:
                prop = self.morpher.materials.get_node_output(pname)
                if prop:
                    prop.default_value += _calc_meta_val(coeffs, self.value) - _calc_meta_val(coeffs, self.prev_value)
        elif meta_materials == "A":
            self.morpher.materials.apply((name, _calc_meta_val(coeffs, self.value)) for name, coeffs in mtl_items)

        return True


null_morpher = Morpher(morpher_cores.MorpherCore(None))
null_morpher.core.error = "Null"


def get(obj, storage=None, undo_handler=_null_handler):
    return Morpher(morpher_cores.get(obj, storage), undo_handler)
