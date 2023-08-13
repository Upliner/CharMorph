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

from . import charlib, morphs, utils


class MorpherCore(utils.ObjTracker):
    error = None
    clamp = True
    alt_topo = False
    alt_topo_buildable = False
    _alt_topo_verts: numpy.ndarray = None
    morphs_l2: list[morphs.MinMaxMorph]

    def __init__(self, obj):
        super().__init__(obj)
        self.char = charlib.library.obj_char(obj)
        self._init_storage()
        self.L1, self.morphs_l1 = self.get_L1()
        self.update_morphs_L2()

    # these methods are overriden in subclasses
    def _init_storage(self):
        pass

    def get_L1(self) -> tuple[str, dict]:
        return "", {}

    def get_morphs_L2(self):
        return []

    def _update_L1(self):
        pass

    def has_morphs(self):
        return False

    def update(self):
        self._alt_topo_verts = None

    def ensure(self):
        pass

    def get_final(self):
        return self.get_final_alt_topo()

    def check_vertex_count(self):
        if not self.char or self.char.np_basis is None:
            return True
        return len(self.obj.data.vertices) == len(self.char.np_basis)

    def get_final_alt_topo(self):
        if self._alt_topo_verts is None:
            self._alt_topo_verts = utils.get_morphed_numpy(self.obj)
        return self._alt_topo_verts

    def cleanup_asset_morphs(self):
        pass

    ######

    def _get_L2_morph_key(self):
        if not self.L1:
            return None
        name = self.char.types.get(self.L1, {}).get("L2")
        return name if name else self.L1

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
        return self.char.np_basis if self.char.np_basis is not None else utils.get_basis_numpy(self.obj)

    def get_basis_alt_topo(self):
        return self.full_basis

    def get_diff(self):
        return self.get_final() - self.full_basis

    def _del_asset_morphs(self):
        try:
            del self.obj.data["charmorph_asset_morphs"]
        except KeyError:
            pass

    def add_asset_morph(self, name: str, _: morphs.Morph):
        lst = self.obj.data.get("charmorph_asset_morphs")
        if not isinstance(lst, list):
            lst = []
        if name not in lst:
            lst.append(name)
        self.obj.data["charmorph_asset_morphs"] = lst

    def remove_asset_morph(self, name: str):
        lst = self.obj.data.get("charmorph_asset_morphs")
        if not isinstance(lst, list):
            lst = []
        else:
            try:
                lst.remove(name)
            except ValueError:
                return
        if lst:
            self.obj.data["charmorph_asset_morphs"] = lst
        else:
            self._del_asset_morphs()


def get_combo_item_value(arr_idx, values):
    return max(sum(val * ((arr_idx >> val_idx & 1) * 2 - 1) for val_idx, val in enumerate(values)), 0)


def enum_combo_names(name):
    nameParts = name.split("_")
    return (f"{nameParts[0]}_{name}" for name in nameParts[1].split("-"))


class ShapeKeysComboMorpher:
    def __init__(self, arr, dims):
        self.arr = arr
        self.coeff = 2 / len(arr)
        self.values = [self.get_combo_prop_value(i) for i in range(dims)]

    def get_combo_prop_value(self, idx):
        return sum(
            0 if sk is None else sk.value * ((arr_idx >> idx & 1) * 2 - 1)
            for arr_idx, sk in enumerate(self.arr)
        )

    def get(self, idx):
        val = self.get_combo_prop_value(idx)
        self.values[idx] = val
        return val

    def set(self, idx, value):
        self.values[idx] = value
        for arr_idx, sk in enumerate(self.arr):
            sk.value = get_combo_item_value(arr_idx, self.values) * self.coeff


class ShapeKeysMorpher(MorpherCore):
    morphs_l2_dict: dict[str, morphs.MinMaxMorph] = {}

    def _update_L1(self):
        for name, sk in self.morphs_l1.items():
            sk.value = 1 if name == self.L1 else 0

        # clear old L2 shape keys
        if not self.obj.data.shape_keys:
            return

        L2_key = self._get_L2_morph_key() or ""
        for sk in self.obj.data.shape_keys.key_blocks:
            if sk.name.startswith("L2_")\
                    and not sk.name.startswith("L2__")\
                    and not sk.name.startswith(f"L2_{L2_key}_"):
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
                    combiner.add_morph(morphs.MinMaxMorphData(sk.name[len(prefix):], sk, sk.slider_min, sk.slider_max))

        load_shape_keys_by_prefix("L2__")

        key = self._get_L2_morph_key()
        if key:
            load_shape_keys_by_prefix(f"L2_{key}_")

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
            if skmax is not None:
                skmax.value = 0
            if skmin is not None:
                skmin.value = -value
        else:
            if skmin is not None:
                skmin.value = 0
            if skmax is not None:
                skmax.value = value

    def prop_get(self, name):
        morph = self.morphs_l2_dict[name]
        if self.is_combo_morph(morph):
            return sum(combo_morpher.get(idx) for combo_morpher, idx in morph.data) / len(morph.data)
        if len(morph.data) == 1:
            return morph.data[0].value
        skmin, skmax = tuple(morph.data)
        return (0 if skmax is None else skmax.value) - (0 if skmin is None else skmin.value)

    def prop_set(self, name, value):
        morph = self.morphs_l2_dict[name]
        if self.clamp:
            value = max(min(value, morph.max), morph.min)
        if self.is_combo_morph(morph):
            self._prop_set_combo(morph, value)
        else:
            self._prop_set_simple(morph, value)

    def _ensure_basis(self):
        if not self.obj.data.shape_keys or not self.obj.data.shape_keys.key_blocks:
            self.obj.shape_key_add(name="Basis", from_mix=False)

    def add_asset_morph(self, name: str, morph: morphs.Morph):
        if self.error:
            return
        self._ensure_basis()
        sk_name = "charmorph_asset_" + name
        sk = self.obj.data.shape_keys.key_blocks.get(sk_name)
        if not sk:
            sk = self.obj.shape_key_add(name=sk_name, from_mix=False)
        sk.value = 1
        data = utils.get_basis_numpy(self.obj)
        morph.apply(data)
        sk.data.foreach_set("co", data.reshape(-1))
        super().add_asset_morph(name, morph)

    def remove_asset_morph(self, name: str):
        if self.error:
            return
        if self.obj.data.shape_keys and self.obj.data.shape_keys.key_blocks:
            sk_name = "charmorph_asset_" + name
            sk = self.obj.data.shape_keys.key_blocks.get(sk_name)
            if sk:
                self.obj.shape_key_remove(sk)
        super().remove_asset_morph(name)

    def enum_expressions(self):
        if not self.obj.data.shape_keys:
            return

        L2_key = self._get_L2_morph_key() or ""
        full_basis = self.full_basis.reshape(-1)
        arr = numpy.empty(len(self.full_basis) * 3)

        prefix = f"L3_{L2_key}_"
        for sk in self.obj.data.shape_keys.key_blocks:
            if sk.name.startswith(prefix):
                sk.data.foreach_get("co", arr)
                arr -= full_basis
                yield (sk.name[len(prefix):], arr.reshape(-1, 3))


class NumpyMorpher(MorpherCore):
    storage: morphs.MorphStorage
    basis: numpy.ndarray = None
    morphed: numpy.ndarray = None
    morphs_combo: dict[str, morphs.MinMaxMorph] = {}

    def __init__(self, obj, storage=None):
        self.storage = storage
        super().__init__(obj)
        self.asset_morphs = self._get_asset_morphs()
        if len(self.get_basis_alt_topo()) != len(obj.data.vertices):
            self.error = f"Vertex count mismatch {len(self.get_basis_alt_topo())} != {len(obj.data.vertices)}"
            if not self.alt_topo and self.char.faces is not None:
                self.alt_topo_buildable = True

    def _init_storage(self):
        if self.storage is None:
            self.storage = morphs.MorphStorage(self.char)

    def check_vertex_count(self):
        return True

    def has_morphs(self):
        # HACK: used just to prevent morphing when morphing data was removed
        return self.obj.data.get("cm_morpher") == "ext"

    def _update_L1(self):
        if self.L1:
            self.obj.data["cmorph_L1"] = self.L1
            self.basis = self.morphs_l1.get(self.L1)
            if isinstance(self.basis, morphs.LazyMorph):
                self.basis = self.basis.resolve()
            if self.basis is not None:
                self.morphs_l1[self.L1] = self.basis

        if self.basis is None:
            self.basis = self.full_basis

        if self.asset_morphs:
            self.basis = self.basis.copy()
            for morph in self.asset_morphs.values():
                morph.apply(self.basis)

    def get_L1(self):
        morphs_l1 = {morph.name: morph.data for morph in self.storage.enum(1)}
        L1 = self.obj.data.get("cmorph_L1", "")
        if L1 not in morphs_l1:
            L1 = ""
        return L1, morphs_l1

    def get_morphs_L2(self):
        combiner = morphs.MorphCombiner()
        for morph in self.storage.enum(2):
            combiner.add_morph(morph)

        key = self._get_L2_morph_key()
        if key:
            for morph in self.storage.enum(2, key):
                combiner.add_morph(morph)
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
        super().update()
        self._do_all_morphs()

        if not self.alt_topo:
            utils.get_target(self.obj).foreach_set("co", self.morphed.reshape(-1))
            self.obj.data.update()

    def prop_get(self, name):
        return self.obj.data.get("cmorph_L2_" + name, 0.0)

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

    def ensure(self):
        if self.morphed is None:
            self._do_all_morphs()

    def get_final(self):
        self.ensure()
        return self.morphed

    def get_final_alt_topo(self):
        return self.get_final()

    def cleanup_asset_morphs(self):
        lst = self.obj.data.get("charmorph_asset_morphs")
        if not isinstance(lst, list):
            self._del_asset_morphs()
            return
        assets = self.char.assets
        lst = [item for item in lst if assets.get(item, charlib.Asset).morph]
        if lst:
            self.obj.data.get["charmorph_asset_morphs"] = lst
        else:
            self._del_asset_morphs()

    def _get_asset_morphs(self) -> dict[str, morphs.Morph]:
        lst = self.obj.data.get("charmorph_asset_morphs")
        if not isinstance(lst, list):
            return {}
        assets = self.char.assets
        result = {}
        for name in lst:
            morph = assets.get(name, charlib.Asset).morph
            if not morph:
                self.error = f'Asset morph for "{name}" is not found'
                return {}
            result[name] = morph
        return result

    def add_asset_morph(self, name: str, morph: morphs.Morph):
        self.asset_morphs[name] = morph
        super().add_asset_morph(name, morph)
        self.basis = None

    def remove_asset_morph(self, name: str):
        super().remove_asset_morph(name)
        try:
            del self.asset_morphs[name]
        except KeyError:
            pass
        self.basis = None

    def enum_expressions(self):
        arr = numpy.empty(self.full_basis.shape)
        for morph in self.storage.enum(3, self._get_L2_morph_key()):
            arr[:] = 0
            morph.data.resolve().apply(arr)
            yield morph.name, arr


class AltTopoMorpher(NumpyMorpher):
    get_final_alt_topo = MorpherCore.get_final_alt_topo

    def __init__(self, obj, storage=None):
        self.alt_topo = True
        self.alt_topo_basis = charlib.get_basis(obj)
        super().__init__(obj, storage)

    def get_basis_alt_topo(self):
        return self.alt_topo_basis


def get(obj, storage=None):
    if obj.data.get("cm_alt_topo"):
        return AltTopoMorpher(obj, storage)
    if obj.data.get("cm_morpher") == "ext":
        return NumpyMorpher(obj, storage)
    return ShapeKeysMorpher(obj)
