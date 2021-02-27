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

import os, logging, re, abc
import bpy

from . import library, materials, fitting, utils

logger = logging.getLogger(__name__)

last_object = None
morpher = None

sep_re = re.compile(r"[ _-]")

def morph_category_name(name):
    m = sep_re.search(name)
    if m:
        return name[:m.start()]
    return name

def morph_categories_prop(morphs):
    return [("category", bpy.props.EnumProperty(
        name="Category",
        items=
        [("<None>", "<None>", "Hide all morphs"), ("<All>", "<All>", "Show all morphs")] +
        [(name, name, "") for name in sorted(set(morph_category_name(morph) for morph in morphs.keys()))],
        description="Select morphing categories to show"))]

d_minmax = {"min": 0, "max": 1}
def convertSigns(signs):
    try:
        return sum(d_minmax[sign] << i for i, sign in enumerate(signs))
    except KeyError:
        return -1

def prefixed_prop(prefix, prop):
    return (prefix + prop[1]["name"], prop)

class Morpher(metaclass=abc.ABCMeta):
    def __init__(self, obj):
        self.obj = obj
        self.char = library.obj_char(obj)
        self.upd_lock = False
        self.clamp = False
        self.morphs_l1 = None
        self.morphs_l2 = {}
        self.morphs_combo = {}
        self.meta_prev = {}
        self.version = 0
        self.L1 = self.get_L1()
        materials.update_props(obj)
        self.mtl_props = materials.props

    @abc.abstractmethod
    def get_L1(self): pass
    @abc.abstractmethod
    def update_L1(self): pass
    @abc.abstractmethod
    def get_morphs_L2(self): pass
    @abc.abstractmethod
    def morph_prop(self, name, data): pass

    def set_L1(self, L1):
        if L1 not in self.morphs_l1:
            return False
        self.L1 = L1
        self.update_L1()
        self.apply_materials(self.char.types.get(L1, {}).get("mtl_props"))
        self.update()
        return True

    def do_update(self):
        fitting.refit_char_assets(self.obj)

    def update(self):
        if self.upd_lock:
            return
        self.do_update()

    def lock(self):
        self.upd_lock = True

    def unlock(self):
        self.upd_lock = False
        self.update()

    def apply_materials(self, data):
        materials.apply_props(data, self.mtl_props)

    def add_morph_l2(self, name, data):
        nameParts = name.split("_")
        if len(nameParts) != 3 or ("min" not in nameParts[2] and "max" not in nameParts[2]):
            self.morphs_l2[name] = [data]
            return

        names = nameParts[1].split("-")
        signArr = nameParts[2].split("-")
        signIdx = convertSigns(signArr)

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
            if len(morph) != cnt:
                logger.error("L2 combo morph conflict: different dimension count on %s, skipping", name)
                return
        else:
            morph = [None] * cnt
            arr[morph_name] = morph

        morph[signIdx] = data

    def apply_morph_data(self, charmorphs, data, preset_mix):
        morph_props = data.get("morphs", {})
        meta_props = data.get("meta", {})
        self.lock()
        try:
            for prop in dir(charmorphs):
                if prop.startswith("prop_"):
                    value = morph_props.get(prop[5:], 0)
                    if preset_mix:
                        value = (value+getattr(charmorphs, prop))/2
                    setattr(charmorphs, prop, value)
                elif prop.startswith("meta_"):
                    # TODO handle preset_mix?
                    value = meta_props.get(prop[5:], 0)
                    self.meta_prev[prop[5:]] = value
                    self.obj.data["cmorph_" + prop] = value
        finally:
            self.unlock()
        materials.apply_props(data.get("materials"))

    # Reset all meta properties to 0
    def reset_meta(self):
        d = self.obj.data
        for k in self.char.morphs_meta.keys():
            self.meta_prev[k] = 0
            pname = "cmorph_meta_" + k
            if pname in d:
                del d[pname]

    def meta_prop(self, name, data):
        pname = "cmorph_meta_" + name

        value = self.obj.data.get(pname, 0.0)
        self.meta_prev[name] = value

        def setter(_, new_value):
            nonlocal value
            value = new_value
            self.obj.data[pname] = value

        def update(cm, context):
            prev_value = self.meta_prev.get(name, 0.0)
            if value == prev_value:
                return
            self.meta_prev[name] = value
            ui = context.window_manager.charmorph_ui
            def calc_val(val):
                return coeffs[1]*val if val > 0 else -coeffs[0]*val

            self.lock()
            try:
                for k, coeffs in data.get("morphs", {}).items():
                    propname = "prop_" + k
                    if not hasattr(cm, propname):
                        continue

                    if not ui.relative_meta:
                        setattr(cm, propname, calc_val(value))
                        continue

                    propval = getattr(cm, propname)

                    val_prev = calc_val(prev_value)
                    val_cur = calc_val(value)

                    # assign absolute prop value if current property value is out of range
                    # or add a delta if it is within (-0.999 .. 0.999)
                    sign = -1 if val_cur-val_prev < 0 else 1
                    if propval*sign < -0.999 and val_prev*sign < -1:
                        propval = val_cur
                    else:
                        propval += val_cur-val_prev
                    setattr(cm, propname, propval)
            finally:
                self.unlock()

            if ui.meta_materials != "N":
                for k, coeffs in data.get("materials", {}).items():
                    if materials.props and k in materials.props:
                        if ui.meta_materials == "R":
                            materials.props[k].default_value += calc_val(value)-calc_val(prev_value)
                        else:
                            materials.props[k].default_value = calc_val(value)

        return bpy.props.FloatProperty(
            name=name,
            min=-1.0, max=1.0,
            precision=3,
            get=lambda _: self.obj.data.get(pname, 0.0),
            set=setter,
            update=update)

    def preset_prop(self):
        presets = self.load_presets()
        if not presets:
            return []

        def update(cm, context):
            if not cm.preset:
                return
            self.apply_morph_data(cm, presets.get(cm.preset, {}), context.window_manager.charmorph_ui.preset_mix)

        return [("preset", bpy.props.EnumProperty(
            name="Presets",
            default="_",
            items=[("_", "(reset)", "")] + [(name, name, "") for name in sorted(presets.keys())],
            description="Choose morphing preset",
            update=update))]

    def load_presets(self):
        if not self.char.name:
            return None
        result = {}
        def load_dir(path):
            path = os.path.join(library.data_dir, path)
            if not os.path.isdir(path):
                return
            for fn in os.listdir(path):
                if os.path.isfile(os.path.join(path, fn)):
                    data = file_io.load_morph_data(os.path.join(path, fn))
                    if data is not None:
                        result[fn[:-5]] = data
        try:
            load_dir(self.char.path("presets"))
            load_dir(self.char.path("presets/" + self.L1))
        except Exception as e:
            logger.error(e)
        return result

    def basic_morph_prop(self, name, getter, setter_base, soft_min=-1):
        def setter(cm, value):
            if self.clamp:
                value = max(min(value, 1), -1)
            self.version += 1
            setter_base(cm, value)
            self.update()
        return bpy.props.FloatProperty(
            name=name,
            soft_min=soft_min, soft_max=1.0,
            precision=3,
            get=getter,
            set=setter)

    def clamp_prop(self):
        def setter(_, value):
            self.clamp = value
        prop = bpy.props.BoolProperty(
            name="Clamp props",
            description="Clamp properties to (-1..1) so they remain in realistic range",
            get=lambda _: self.clamp,
            set=setter,
            update=lambda cm, ctx: self.update(),
            default=True)
        return ("clamp", prop)

    # Create a property group with all L2 morphs
    def create_charmorphs_L2(self):
        del_charmorphs_L2()
        self.get_morphs_L2()
        if not self.morphs_l2:
            return

        self.meta_prev.clear()

        propGroup = type(
            "CharMorpher_Dyn_PropGroup",
            (bpy.types.PropertyGroup,),
            {
                "__annotations__":
                dict(
                    [self.clamp_prop()] + self.preset_prop() + morph_categories_prop(self.morphs_l2) +
                    [prefixed_prop("prop_", self.morph_prop(k, v)) for k, v in self.morphs_l2.items()] +
                    [prefixed_prop("meta_", self.meta_prop (k, v)) for k, v in self.char.morphs_meta.items()]
                    )
            }
        )

        bpy.utils.register_class(propGroup)

        bpy.types.WindowManager.charmorphs = bpy.props.PointerProperty(
            type=propGroup, options={"SKIP_SAVE"})

        cm = bpy.context.window_manager.charmorphs
        for prop in dir(cm):
            if prop.startswith("prop_"):
                if abs(getattr(cm, prop)) > 1:
                    break
        else:
            self.clamp = True


from . import morphers

def create_charmorphs(obj):
    global last_object, morpher
    last_object = obj
    if obj.type != "MESH":
        return

    if obj.data.get("cm_morpher") == "ext":
        m = morphers.NumpyMorpher(obj)
    else:
        m = morphers.ShapeKeysMorpher(obj)

    ui = bpy.context.window_manager.charmorph_ui

    if not m.has_morphs():
        return

    morpher = m

    if m.char.default_armature and ui.fin_rig not in m.char.armature:
        ui.fin_rig = m.char.default_armature

    items = [(name, m.char.types.get(name, {}).get("title", name), "") for name in sorted(m.morphs_l1.keys())]

    if not m.L1 and "default_type" in m.char.config:
        m.set_L1(m.char.default_type)

    L1_idx = 0
    for i in range(1, len(items)-1):
        if items[i][0] == m.L1:
            L1_idx = i
            break

    def chartype_setter(_, value):
        nonlocal L1_idx
        if value == L1_idx:
            return
        L1_idx = value
        m.set_L1(items[L1_idx][0])

    bpy.types.WindowManager.chartype = bpy.props.EnumProperty(
        name="Type",
        items=items,
        description="Choose character type",
        get=lambda self: L1_idx,
        set=chartype_setter,
        options={"SKIP_SAVE"})

    m.create_charmorphs_L2()

# Delete morphs property group
def del_charmorphs_L2():
    if not hasattr(bpy.types.WindowManager, "charmorphs"):
        return
    propGroup = bpy.types.WindowManager.charmorphs[1]['type']
    del bpy.types.WindowManager.charmorphs
    bpy.utils.unregister_class(propGroup)
    fitting.invalidate_cache()

def del_charmorphs():
    global last_object, morpher
    last_object = None
    morpher = None
    if hasattr(bpy.types.WindowManager, "chartype"):
        del bpy.types.WindowManager.chartype
    del_charmorphs_L2()

def bad_object():
    if not morpher:
        return False
    try:
        return bpy.data.objects.get(morpher.obj.name) is not morpher.obj
    except ReferenceError:
        return True

class UIProps:
    preset_mix: bpy.props.BoolProperty(
        name="Mix with current",
        description="Mix selected preset with current morphs",
        default=False)
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

class CHARMORPH_PT_Morphing(bpy.types.Panel):
    bl_label = "Morphing"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 2

    @classmethod
    def poll(cls, context):
        return hasattr(context.window_manager, 'charmorphs')

    def draw(self, context):
        morphs = context.window_manager.charmorphs
        ui = context.window_manager.charmorph_ui
        propList = sorted(dir(morphs))
        self.layout.label(text="Character type")
        col = self.layout.column(align=True)

        col.prop(context.window_manager, "chartype")
        if hasattr(morphs, "preset"):
            col.prop(morphs, "preset")
            col.prop(ui, "preset_mix")

        col.separator()

        meta_morphs = [p for p in propList if p.startswith("meta_")]
        if meta_morphs:
            self.layout.label(text="Meta morphs")
            col = self.layout.column(align=True)
            col.prop(ui, "meta_materials")
            col.prop(ui, "relative_meta")

            for prop in meta_morphs:
                col.prop(morphs, prop, slider=True)

        self.layout.prop(morphs, "clamp")

        self.layout.separator()

        self.layout.label(text="MORE MORPHS HERE:")
        self.layout.prop(morphs, "category")
        if morphs.category != "<None>":
            self.layout.prop(ui, "morph_filter")
            col = self.layout.column(align=True)
            for prop in propList:
                if prop.startswith("prop_" + ("" if morphs.category == "<All>" else morphs.category)):
                    if ui.morph_filter.lower() in prop[5:].lower():
                        col.prop(morphs, prop, slider=True)

classes = [CHARMORPH_PT_Morphing]
