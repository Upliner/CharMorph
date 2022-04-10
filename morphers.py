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

import os, numpy

import mathutils # pylint: disable=import-error

from .lib import charlib
from . import morphing

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

class ShapeKeysMorpher(morphing.Morpher):
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
        self.morphs_l2.clear()
        self.morphs_combo.clear()
        if not self.obj.data.shape_keys:
            return

        def load_shape_keys_by_prefix(prefix):
            for sk in self.obj.data.shape_keys.key_blocks:
                if sk.name.startswith(prefix):
                    self.add_morph_l2(sk.name[len(prefix):], sk, sk.slider_min, sk.slider_max)

        load_shape_keys_by_prefix("L2__")

        if len(self.L1) > 0:
            load_shape_keys_by_prefix(f"L2_{self.L1}_")

        for k, v in self.morphs_combo.items():
            names = list(enum_combo_names(k))
            combo_morpher = ShapeKeysComboMorpher(v.data, len(names))
            for i, name in enumerate(names):
                morph = self.morphs_l2.get(name)
                if not morph:
                    morph = morphing.Morph([], -1, 1)
                    self.morphs_l2[name] = morph
                morph.data.append((combo_morpher, i))

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
        morph = self.morphs_l2[name]
        if self.is_combo_morph(morph):
            self._prop_set_combo(morph, value)
        else:
            self._prop_set_simple(morph, value)

    def prop_get(self, name):
        morph = self.morphs_l2[name]
        if self.is_combo_morph(morph):
            return sum(combo_morpher.get(idx) for combo_morpher, idx in morph.data)/len(morph.data)
        if len(morph.data) == 1:
            return morph.data[0].value
        skmin, skmax = tuple(morph.data)
        return (0 if skmax is None else skmax.value) - (0 if skmin is None else skmin.value)

class NumpyMorpher(morphing.Morpher):
    basis = None
    morphed = None
    def __init__(self, obj):
        super().__init__(obj)
        self.full_basis = self.char.np_basis
        if self.full_basis is None:
            self.full_basis = super().get_basis()
        self.counter = 1
        if obj.data.get("cm_alt_topo"):
            self.alt_topo = True
            self.alt_topo_basis = morphing.get_basis(obj, False)
        else:
            self.alt_topo_basis = self.full_basis
        if len(self.alt_topo_basis) != len(obj.data.vertices):
            self.error = f"Vertex count mismatch {len(self.alt_topo_basis)} != {len(obj.data.vertices)}"
            if not self.alt_topo and self.char.faces is not None:
                self.alt_topo_buildable = True

    def has_morphs(self):
        return self.obj.data.get("cm_morpher") == "ext" # HACK: used just to prevent morphing when morphing data was removed

    def _get_L1_data(self, name):
        if not name:
            return None
        if name == self.char.basis:
            return self.char.np_basis

        file = self.morphs_l1.get(name, "")
        if not os.path.isfile(file):
            return None
        result = numpy.load(file)
        result.flags.writeable = False
        return result

    def update_L1(self):
        self.basis = self._get_L1_data(self.L1)
        if self.L1:
            self.obj.data["cmorph_L1"] = self.L1
        if self.basis is None:
            self.basis = self.full_basis

    def get_L1(self):
        self.morphs_l1.clear()
        path = self.char.path("morphs/L1")
        if not os.path.isdir(path):
            return ""
        for file in os.listdir(path):
            if file[-4:] != ".npy":
                continue
            self.morphs_l1[file[:-4]] = os.path.join(path, file)
        L1 = self.obj.data.get("cmorph_L1", "")
        if L1 not in self.morphs_l1:
            L1 = ""
        return L1

    def _load_dir(self, path):
        path = self.char.path(path)
        for morph in charlib.list_morph_dir(path):
            if morph.get("separator"):
                self.morphs_l2[f"\0\0\0{self.counter}"] = None
                self.counter += 1
            else:
                self.add_morph_l2(morph["morph"], os.path.join(path, morph["morph"] + ".npz"), morph.get("min", 0), morph.get("max", 1))

    def get_morphs_L2(self):
        self.morphs_l2.clear()
        self.morphs_combo.clear()
        self._load_dir("morphs/L2")
        if self.L1:
            self._load_dir("morphs/L2/"+self.L1)

        for k, v in self.morphs_combo.items():
            for name in enum_combo_names(k):
                self.morphs_l2[name] = self.morphs_l2.get(name, v)

    def _do_morph(self, data, idx, value):
        if abs(value) < 0.001:
            return
        item = data[idx]
        if item is None:
            return
        if isinstance(item, str):
            npz = numpy.load(item)
            item = (npz["idx"], npz["delta"].astype(dtype=numpy.float64, casting="same_kind"))
            data[idx] = item
        if self.morphed is None:
            self.morphed = self.basis.astype(dtype=numpy.float64, casting="same_kind")
        self.morphed[item[0]] += item[1] * value

    def _do_all_morphs(self):
        if self.basis is None:
            self.update_L1()
        self.morphed = None

        for name, morph in self.morphs_l2.items():
            if morph is None or len(morph.data) > 2:
                continue
            data = morph.data
            val = self.obj.data.get("cmorph_L2_"+name)
            if val:
                if self.clamp:
                    val = max(min(val, morph.max), morph.min)
                if len(data) == 1:
                    self._do_morph(data, 0, val)
                elif len(data) == 2:
                    if val < 0:
                        self._do_morph(data, 0, -val)
                    else:
                        self._do_morph(data, 1, val)

        for name, morph in self.morphs_combo.items():
            values = [self.prop_get_clamped(morph_name) for morph_name in enum_combo_names(name)]
            data = morph.data
            coeff = 2 / len(data)
            for i in range(len(data)):
                val2 = get_combo_item_value(i, values) * coeff
                self._do_morph(data, i, val2)

        if self.morphed is None:
            self.morphed = self.basis

    def do_update(self):
        self._do_all_morphs()

        if not self.alt_topo:
            morphing.get_target(self.obj).foreach_set("co", self.morphed.reshape(-1))
            self.obj.data.update()

        super().do_update()

    def prop_get(self, name):
        return self.obj.data.get("cmorph_L2_"+name, 0.0)

    # Clamp to -1..1 only for combo props
    def prop_get_clamped(self, name):
        val = self.prop_get(name)
        if self.clamp:
            return max(min(val, 1), -1)
        return val

    def prop_set_internal(self, name, value):
        self.obj.data["cmorph_L2_" + name] = value

    def get_basis(self):
        return self.full_basis

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
