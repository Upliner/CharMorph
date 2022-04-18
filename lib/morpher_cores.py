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
# Copyright (C) 2022 Michael Vigovsky

import numpy
import mathutils  # pylint: disable=import-error

from . import charlib, morphs, utils

class MorpherCore:
    error = None
    clamp = True
    alt_topo = False
    alt_topo_buildable = False
    morphs_l2: list[morphs.MinMaxMorph]

    def __init__(self, obj):
        self.obj = obj
        self.char = charlib.obj_char(obj)
        self._init_storage()
        self.L1, self.morphs_l1 = self.get_L1()
        self.update_morphs_L2()

    # these methods are overriden in subclass
    @staticmethod
    def _init_storage():
        pass
    @staticmethod
    def get_L1() -> tuple[str, list[morphs.MinMaxMorph]]:
        return "", {}
    @staticmethod
    def get_morphs_L2():
        return []
    @staticmethod
    def _update_L1():
        pass
    @staticmethod
    def has_morphs():
        return False
    def get_co(self, i):
        return self.obj.data.vertices[i].co
    def update(self):
        pass

    ######

    def set_L1(self, value):
        self.L1 = value
        self._update_L1()
        self.update_morphs_L2()

    def update_morphs_L2(self):
        self.morphs_l2 = self.get_morphs_L2()
        if not self.char.custom_morph_order:
            self.morphs_l2.sort(key=lambda morph: morph.name)

    @utils.lazyproperty
    def full_basis(self):
        return self.char.np_basis if self.char.np_basis is not None else charlib.get_basis(self.obj)

    def get_basis_alt_topo(self):
        return self.full_basis

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

class ShapeKeysMorpher(MorpherCore):
    morphs_l2_dict = {}
    def _update_L1(self):
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
        if not self.obj.data.shape_keys:
            return "", {}
        morphs_l1 = {}
        maxkey = ""
        maxval = 0
        for sk in self.obj.data.shape_keys.key_blocks:
            if not sk.name.startswith("L1_"):
                continue
            name = sk.name[3:]
            if sk.value > maxval:
                maxkey = name
                maxval = sk.value
            morphs_l1[name] = sk

        return maxkey, morphs_l1

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

    def prop_get(self, name):
        morph = self.morphs_l2_dict[name]
        if self.is_combo_morph(morph):
            return sum(combo_morpher.get(idx) for combo_morpher, idx in morph.data)/len(morph.data)
        if len(morph.data) == 1:
            return morph.data[0].value
        skmin, skmax = tuple(morph.data)
        return (0 if skmax is None else skmax.value) - (0 if skmin is None else skmin.value)

    def prop_set(self, name, value):
        morph = self.morphs_l2_dict[name]
        if self.is_combo_morph(morph):
            self._prop_set_combo(morph, value)
        else:
            self._prop_set_simple(morph, value)

    def get_diff(self):
        result = utils.get_morphed_numpy(self.obj)
        result -= self.full_basis
        return result

class NumpyMorpher(MorpherCore):
    storage: morphs.MorphStorage
    basis: numpy.ndarray = None
    morphed: numpy.ndarray = None
    morphs_combo: dict[str, morphs.MinMaxMorph] = {}

    def __init__(self, obj, storage=None):
        self.storage = storage
        super().__init__(obj)
        if len(self.get_basis_alt_topo()) != len(obj.data.vertices):
            self.error = f"Vertex count mismatch {len(self.get_basis_alt_topo())} != {len(obj.data.vertices)}"
            if not self.alt_topo and self.char.faces is not None:
                self.alt_topo_buildable = True

    def _init_storage(self):
        if self.storage is None:
            self.storage = morphs.MorphStorage(self.char)

    def has_morphs(self):
        return self.obj.data.get("cm_morpher") == "ext" # HACK: used just to prevent morphing when morphing data was removed

    def _update_L1(self):
        if self.L1:
            self.obj.data["cmorph_L1"] = self.L1
            self.basis = self.storage.resolve_lazy_L1(self.morphs_l1.get(self.L1))
            if self.basis is not None:
                self.morphs_l1[self.L1] = self.basis

        if self.basis is None:
            self.basis = self.full_basis

    def get_L1(self):
        morphs_l1 = {morph.name: self.storage.get_lazy(1, morph.name) for morph in self.storage.enum(1)}
        L1 = self.obj.data.get("cmorph_L1", "")
        if L1 not in morphs_l1:
            L1 = ""
        return L1, morphs_l1

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
            self._update_L1()
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

    def update(self):
        self._do_all_morphs()

        if not self.alt_topo:
            utils.get_target(self.obj).foreach_set("co", self.morphed.reshape(-1))
            self.obj.data.update()

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

    def prop_set(self, name, value):
        self.obj.data["cmorph_L2_" + name] = value

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

class AltTopoMorpher(NumpyMorpher):
    def __init__(self, obj, storage=None):
        self.alt_topo = True
        self.alt_topo_basis = charlib.get_basis(obj)
        super().__init__(obj, storage)

    def get_basis_alt_topo(self):
        return self.alt_topo_basis

def get(obj, storage = None):
    if utils.is_true(obj.data.get("cm_alt_topo")):
        return AltTopoMorpher(obj, storage)
    if obj.data.get("cm_morpher") == "ext":
        return NumpyMorpher(obj, storage)
    return ShapeKeysMorpher(obj)