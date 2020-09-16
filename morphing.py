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

import os, json, logging, re, abc
import bpy

from . import yaml, library, materials, file_io, fitting

logger = logging.getLogger(__name__)

last_object = None
morpher = None

sep_re = re.compile("[ _]")

def morph_category_name(name):
    m = sep_re.search(name)
    if m:
        return name[:m.start()]
    return name

def morph_categories_prop(morphs):
    return [("category",bpy.props.EnumProperty(
        name="Category",
        items=[("<None>","<None>","Hide all morphs"), ("<All>","<All>","Show all morphs")] +
            [(name,name,"") for name in sorted(set(morph_category_name(morph) for morph in morphs.keys()))],
        description="Select morphing categories to show"))]

d_minmax = {"min": 0, "max": 1}
def convertSigns(signs):
    try:
        return sum(d_minmax[sign] << i for i, sign in enumerate(signs))
    except KeyError:
        return -1

class Morpher(metaclass=abc.ABCMeta):
    def __init__(self, obj):
        self.obj = obj
        self.char = library.obj_char(obj)
        self.upd_lock = False
        self.meta_lock = False
        self.morphs_l1 = None
        self.morphs_l2 = None
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
    def morph_props(self, name, data): pass

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
            self.morphs_l2[name] = (data)
            return

        names = nameParts[1].split("-")
        signArr = nameParts[2].split("-")
        signIdx = convertSigns(signArr)

        if len(names) == 0 or len(names) != len(signArr):
            logger.error("Invalid L2 morph name: {}, skipping".format(name))
            return

        morph_name = nameParts[0]+"_"+nameParts[1]
        cnt = 2 ** len(names)

        if morph_name in self.morphs_l2:
            morph = self.morphs_l2[morph_name]
            if len(morph) != cnt:
                logger.error("L2 combo morph conflict: different dimension count on {}, skipping".format(name))
                return
        else:
            morph = [None] * cnt
            self.morphs_l2[morph_name] = morph

        morph[signIdx] = data

    def apply_morph_data(self, charmorphs, data, preset_mix):
        morph_props = data.get("morphs", {})
        meta_props = data.get("meta", {})
        self.lock()
        self.meta_lock = True
        for prop in dir(charmorphs):
            if prop.startswith("prop_"):
                value = morph_props.get(prop[5:], 0)
                if preset_mix:
                    value = (value+getattr(charmorphs, prop))/2
                setattr(charmorphs, prop, value)
            elif prop.startswith("meta_"):
                # TODO handle preset_mix?
                value = meta_props.get(prop[5:],0)
                self.meta_prev[prop[5:]] = value
                setattr(charmorphs, prop, value)
        self.meta_lock = False
        self.unlock()
        materials.apply_props(data.get("materials"))

    # Reset all meta properties to 0
    def reset_meta(self, charmorphs):
        self.meta_lock = True
        self.meta_prev = { k: 0.0 for k in self.char.morphs_meta.keys() }
        for prop in dir(charmorphs):
            if prop.startswith("meta_"):
                setattr(charmorphs, prop, 0)
        self.meta_lock = False

    def meta_prop(self, name, data):
        def update(cm, context):
            if self.meta_lock:
                return
            prev_value = self.meta_prev.get(name, 0.0)
            value = getattr(cm, "meta_" + name)
            if value == prev_value:
                return
            self.meta_prev[name] = value
            ui = context.window_manager.charmorph_ui
            def calc_val(val):
                return coeffs[1]*val if val > 0 else -coeffs[0]*val

            self.lock()
            for k, coeffs in data.get("morphs",{}).items():
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
                if propval*sign<-0.999 and val_prev*sign < -1:
                     propval = val_cur
                else:
                    propval += val_cur-val_prev
                setattr(cm, propname, propval)

            self.unlock()

            if ui.meta_materials != "N":
                for k, coeffs in data.get("materials",{}).items():
                    if materials.props and k in materials.props:
                        if ui.meta_materials == "R":
                            materials.props[k].default_value += calc_val(value)-calc_val(prev_value)
                        else:
                            materials.props[k].default_value = calc_val(value)

        return ("meta_"+name,bpy.props.FloatProperty(name=name,
            min = -1.0, max = 1.0,
            precision = 3,
            update = update))

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
        if self.char.name == "":
            return None
        result = {}
        def load_dir(path):
            path = os.path.join(library.data_dir, path)
            if not os.path.isdir(path):
                return {}
            for fn in os.listdir(path):
                if os.path.isfile(os.path.join(path, fn)):
                    data = file_io.load_morph_data(os.path.join(path, fn))
                    if data != None:
                        result[fn[:-5]] = data
        try:
            load_dir(self.char.path("presets"))
            load_dir(self.char.path("presets/" + self.L1))
        except Exception as e:
            logger.error(e)
        return result

    clamp_combos_prop = bpy.props.BoolProperty(
        name="Clamp combo props",
        description="Clamp combo properties to (-1..1) so they remain in realistic range",
        default=True)

    @staticmethod
    def morph_prop(name, getter, setter, soft_min = -1):
        return bpy.props.FloatProperty(name=name,
            soft_min = soft_min, soft_max = 1.0,
            precision = 3,
            get = getter,
            set = setter)

    # Create a property group with all L2 morphs
    def create_charmorphs_L2(self):
        del_charmorphs_L2()
        self.get_morphs_L2()
        if not self.morphs_l2:
            return

        self.meta_prev = { k: 0.0 for k in self.char.morphs_meta.keys() }

        propGroup = type("CharMorpher_Dyn_PropGroup",
            (bpy.types.PropertyGroup,),
            {"__annotations__":
                dict([("clamp_combos", self.clamp_combos_prop)] + self.preset_prop() + morph_categories_prop(self.morphs_l2) +
                    [("prop_"+name, prop) for sublist in (self.morph_props(k, v) for k, v in self.morphs_l2.items()) for name, prop in sublist] +
                    [self.meta_prop(name, data) for name, data in self.char.morphs_meta.items()])})

        bpy.utils.register_class(propGroup)

        bpy.types.WindowManager.charmorphs = bpy.props.PointerProperty(
            type=propGroup, options={"SKIP_SAVE"})

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

    if not m.has_morphs():
        return

    morpher = m

    items = [(name, m.char.types.get(name, {}).get("title",name), "") for name in m.morphs_l1.keys()]

    if not m.L1 and "default_type" in m.char.config:
        m.set_L1(m.char.default_type)

    L1_idx = 0
    for i in range(1, len(items)-1):
        if items[i][0] == m.L1:
            L1_idx = i
            break

    def chartype_setter(self, value):
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

class CHARMORPH_PT_Morphing(bpy.types.Panel):
    bl_label = "Morphing"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 2

    @classmethod
    def poll(cls, context):
        return hasattr(context.window_manager,'charmorphs')

    def draw(self, context):
        morphs = context.window_manager.charmorphs
        ui = context.window_manager.charmorph_ui
        propList = sorted(dir(morphs))
        self.layout.label(text= "Character type")
        col = self.layout.column(align=True)

        col.prop(context.window_manager,"chartype")
        if hasattr(morphs,"preset"):
            col.prop(morphs, "preset")
            col.prop(ui, "preset_mix")

        col.separator()

        meta_morphs = [p for p in propList if p.startswith("meta_")]
        if meta_morphs:
            self.layout.label(text = "Meta morphs")
            col = self.layout.column(align=True)
            col.prop(ui, "meta_materials")
            col.prop(ui, "relative_meta")

            for prop in meta_morphs:
                col.prop(morphs, prop, slider=True)

        self.layout.prop(morphs, "clamp_combos")

        self.layout.separator()

        self.layout.prop(morphs, "category")
        if morphs.category != "<None>":
            col = self.layout.column(align=True)
            for prop in (p for p in propList if p.startswith("prop_" + ("" if morphs.category == "<All>" else morphs.category))):
                col.prop(morphs, prop, slider=True)

classes = [CHARMORPH_PT_Morphing]
