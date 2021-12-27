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

import os, numpy

from . import morphing

def get_combo_item_value(arr_idx, values):
    return sum(val*((arr_idx >> val_idx & 1)*2-1) for val_idx, val in enumerate(values))

def enum_combo_names(name):
    nameParts = name.split("_")
    return (nameParts[0]+"_"+name for name in nameParts[1].split("-"))

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
            if sk.name.startswith("L2_") and not sk.name.startswith("L2__") and not sk.name.startswith("L2_{}_".format(self.L1)):
                sk.value = 0

    # scan object shape keys and convert them to dictionary
    def get_L1(self):
        self.morphs_l1 = {}
        if not self.obj.data.shape_keys:
            return ""
        maxkey = ""
        maxval = 0
        for sk in self.obj.data.shape_keys.key_blocks:
            if len(sk.name) < 4 or not sk.name.startswith("L1_"):
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
        if not self.obj.data.shape_keys:
            return

        def load_shape_keys_by_prefix(prefix):
            for sk in self.obj.data.shape_keys.key_blocks:
                if sk.name.startswith(prefix):
                    self.add_morph_l2(sk.name[len(prefix):], sk)

        load_shape_keys_by_prefix("L2__")

        if len(self.L1) > 0:
            load_shape_keys_by_prefix("L2_%s_" % self.L1)

        for k, v in self.morphs_combo.items():
            names = list(enum_combo_names(k))
            combo_morpher = ShapeKeysComboMorpher(v, len(names))
            for i, name in enumerate(names):
                arr = self.morphs_l2.get(name)
                if not arr:
                    arr = []
                    self.morphs_l2[name] = arr
                arr.append((combo_morpher, i))

    # create simple prop that drives one min and one max shapekey
    def morph_prop_simple(self, name, skmin, skmax, soft_min=-1.0):
        def setter(_, value):
            if value < 0:
                if skmax is not None: skmax.value = 0
                if skmin is not None: skmin.value = -value
            else:
                if skmin is not None: skmin.value = 0
                if skmax is not None: skmax.value = value
        return self.basic_morph_prop(
            name,
            lambda _: (0 if skmax is None else skmax.value) - (0 if skmin is None else skmin.value),
            setter, soft_min,
        )

    # create a prop that drives multple shapekeys
    def morph_prop_combo(self, name, arr):
        def setter(_, value):
            for item in arr:
                item[0].set(item[1], value)

        return self.basic_morph_prop(
            name,
            lambda _: sum(item[0].get(item[1]) for item in arr)/len(arr),
            setter,
        )

    def morph_prop(self, name, data):
        if isinstance(data[0], tuple):
            return self.morph_prop_combo(name, data)
        if len(data) == 1:
            return self.morph_prop_simple(name, None, data[0], 0)

        return self.morph_prop_simple(name, data[0], data[1], -1)

class NumpyMorpher(morphing.Morpher):
    def __init__(self, obj):
        super().__init__(obj)
        self.basis = None
        self.full_basis = self._get_L1_data(self.char.basis)
        if self.full_basis is None:
            self.full_basis = self._get_fallback_basis()
        self.morphed = self.full_basis

    def has_morphs(self):
        return bool(self.char.name)

    def _get_L1_data(self, name):
        if not name:
            return None
        file = self.morphs_l1.get(name, "")
        if not os.path.isfile(file):
            return None
        return numpy.load(file).astype(dtype=numpy.float64, casting="same_kind")

    def _get_fallback_basis(self):
        sk = self.obj.data.shape_keys
        if sk:
            data = sk.reference_key.data
        else:
            data = self.obj.data.vertices
        arr = numpy.empty(len(data) * 3)
        data.foreach_get("co", arr)
        return arr.reshape(-1, 3)

    def update_L1(self):
        self.basis = self._get_L1_data(self.L1)
        if self.L1:
            self.obj.data["cmorph_L1"] = self.L1
        if self.basis is None:
            self.basis = self.full_basis

    def get_L1(self):
        self.morphs_l1 = {}
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

    def get_morphs_L2(self):
        self.morphs_l2.clear()
        def load_dir(path):
            path = self.char.path(path)
            if not os.path.isdir(path):
                return
            for file in os.listdir(path):
                if file[-4:] != ".npz":
                    continue
                self.add_morph_l2(file[:-4], os.path.join(path, file))
        load_dir("morphs/L2")
        if self.L1:
            load_dir("morphs/L2/"+self.L1)

        for k in self.morphs_combo:
            for name in enum_combo_names(k):
                self.morphs_l2[name] = self.morphs_l2.get(name)

    def _get_dest_shapekey(self):
        if not self.obj.data.shape_keys or not self.obj.data.shape_keys.key_blocks:
            self.obj.shape_key_add(name="Basis", from_mix=False)
        kb = self.obj.data.shape_keys.key_blocks
        sk = kb.get("charmorph_final")
        if sk is not None:
            return sk
        return self.obj.shape_key_add(name="charmorph_final", from_mix=False)

    def _do_morph(self, data, idx, value):
        if value < 0.001:
            return
        if self.clamp:
            value = min(value, 1)
        item = data[idx]
        if item is None:
            return
        if isinstance(item, str):
            npz = numpy.load(item)
            item = (npz["idx"], npz["delta"].astype(dtype=numpy.float64, casting="same_kind"))
            data[idx] = item
        if self.morphed is None:
            self.morphed = self.basis.copy()
        self.morphed[item[0]] += item[1] * value

    def do_update(self):
        if self.basis is None:
            self.update_L1()
        self.morphed = None

        for name, data in self.morphs_l2.items():
            if data is None:
                continue
            val = self.obj.data.get("cmorph_L2_"+name)
            if val:
                if len(data) == 1:
                    self._do_morph(data, 0, val)
                elif len(data) == 2:
                    self._do_morph(data, 0, -val)
                    self._do_morph(data, 1, val)

        for name, data in self.morphs_combo.items():
            values = [self.obj.data.get("cmorph_L2_"+n, 0.0) for n in enum_combo_names(name)]
            coeff = 2 / len(data)
            for i in range(len(data)):
                self._do_morph(data, i, get_combo_item_value(i, values) * coeff)

        if self.morphed is None:
            self.morphed = self.basis

        sk = self._get_dest_shapekey()
        sk.data.foreach_set("co", self.morphed.reshape(-1))
        sk.value = 1

        super().do_update()

    def morph_prop(self, name, data):
        soft_min = -1
        if isinstance(data, list) and len(data) == 1:
            soft_min = 0
        pname = "cmorph_L2_"+name
        def setter(_, value):
            self.obj.data[pname] = value
        return self.basic_morph_prop(
            name,
            lambda _: self.obj.data.get(pname, 0.0),
            setter, soft_min,
        )

    def get_diff(self):
        return self.morphed - self.full_basis
