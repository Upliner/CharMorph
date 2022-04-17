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

import os, json, logging, numpy

from . import utils

logger = logging.getLogger(__name__)

class Morph:
    __slots__ = ()
    @staticmethod
    def apply(verts, _ = None):
        return verts

class FullMorph(Morph):
    __slots__ = ("delta",)
    def __init__(self, delta):
        self.delta = delta

    def get_delta(self, value):
        return self.delta if value is None else self.delta * value

    def apply(self, verts, value = None):
        verts += self.get_delta(value)
        return verts

class PartialMorph(FullMorph):
    __slots__ = "delta", "idx"
    def __init__(self, idx, delta):
        super().__init__(delta)
        self.idx = idx

    def apply(self, verts, value = None):
        verts[self.idx] += self.get_delta(value)
        return verts

def np_ro64(a):
    if a is None:
        return None
    a = a.astype(dtype=numpy.float64, casting="same_kind")
    a.flags.writeable = False
    return a

def load_morph(file):
    if not os.path.isfile(file):
        return None
    data = numpy.load(file)
    if isinstance(data, numpy.ndarray):
        return FullMorph(np_ro64(data))
    return PartialMorph(data["idx"], np_ro64(data["delta"]))

def detect_npy_npz(base):
    for ext in (".npy", ".npz"):
        path = base+ext
        if os.path.isfile(path):
            return path
    return None

def load_morph_noext(basename):
    file = detect_npy_npz(basename)
    return load_morph(file) if file else None

class MinMaxMorphData:
    __slots__ = "min", "max", "name"
    def __init__(self, name, minval=0, maxval=0):
        self.min = minval
        self.max = maxval
        self.name = name

class MinMaxMorph(MinMaxMorphData):
    __slots__ = "min", "max", "name", "data"

    def __init__(self, name, data, minval=0, maxval=0):
        super().__init__(name, minval, maxval)
        self.data = data

    def get_morph(self, idx) -> Morph:
        item = self.data[idx]
        if isinstance(item, str):
            item = load_morph(item)
            self.data[idx] = item
        return item

    def apply(self, verts, value):
        if not self.data or abs(value) < 0.001:
            return
        if len(self.data) == 1:
            self.get_morph(0).apply(verts, value)
            return
        if len(self.data) == 2:
            if value < 0:
                self.get_morph(0).apply(verts, -value)
            else:
                self.get_morph(1).apply(verts, value)


class Separator(Morph):
    name=""

def json_to_morph(item):
    if item["separator"]:
        return Separator
    return MinMaxMorphData(item.get("morph"), item.get("min", 0), item.get("max", 1))

class MorphStorage:
    def __init__(self, char):
        self.char = char
        self.path = char.path("morphs")

    def get_path(self, level, *names):
        return os.path.join(self.path, f"L{level}", *names)

    def get_lazy(self, level, *names):
        if not names[-1]:
            return None
        if level == 1 and names[0] == self.char.basis:
            return self.char.np_basis
        return detect_npy_npz(self.get_path(level, *names))

    @staticmethod
    def resolve_lazy_L1(data):
        if not isinstance(data, str):
            return data
        if not os.path.isfile(data):
            return None
        return np_ro64(numpy.load(data))

    @staticmethod
    def resolve_lazy(data):
        if not isinstance(data, str):
            return data
        return load_morph(data)

    def get(self, level, *names):
        lazy = self.get_lazy(level, *names)
        if level == 1:
            return self.resolve_lazy_L1(lazy)
        return self.resolve_lazy(lazy)

    def enum(self, level, *names):
        path = self.get_path(level, *names)
        if not os.path.isdir(path):
            return ()
        jslist = utils.parse_file(os.path.join(path, "morphs.json"), json.load, None)
        if jslist is not None:
            return (json_to_morph(item) for item in jslist)

        return (MinMaxMorphData(name[:-4], 0, 1) for name in sorted(os.listdir(path))
            if (name.endswith(".npz") or name.endswith(".npy")) and os.path.isfile(os.path.join(path, name)))

class MorphImporter:
    _counter_lev: int
    _counter_cnt: int

    def __init__(self, storage: MorphStorage, obj):
        self.storage = storage
        self.obj = obj

    def _ensure_basis(self):
        basis = self.storage.char.np_basis
        if not self.obj.data.shape_keys or not self.obj.data.shape_keys.key_blocks:
            sk = self.obj.shape_key_add(name="Basis", from_mix=False)
            if basis is not None:
                sk.data.foreach_set("co", basis.reshape(-1))

        if basis is None:
            basis = utils.get_basis_numpy(self.obj)
        return basis

    def _create_morph_sk(self, prefix, morph):
        if morph is Separator:
            self.obj.shape_key_add(name=f"---- sep-{self._counter_lev}-{self._counter_cnt} ----", from_mix=False)
            self._counter_cnt += 1
            return None

        sk = self.obj.shape_key_add(name=prefix + morph.name, from_mix=False)
        sk.slider_min = morph.min
        sk.slider_max = morph.max
        return sk

    def _import_to_sk(self, morph, basis, level, *names):
        sk = self._create_morph_sk("_".join((f"L{level}",) + names) + "_", morph)
        if not sk:
            return None
        names += (morph.name,)
        if level == 1:
            data = self.storage.get(level, *names)
        else:
            data = self.storage.get(level, *names).apply(basis.copy())
            if level == 2 and names[0]:
                sk.relative_key = self.obj.data.shape_keys.key_blocks["L1_" + names[0]]

        sk.data.foreach_set("co", data.reshape(-1))
        return data

    def import_morphs(self):
        basis = self._ensure_basis()
        L1 = [(morph.name, self._import_to_sk(morph, None, 1)) for morph in self.storage.enum(1)]

        self._counter_lev = 2
        self._counter_cnt = 1
        for morph in self.storage.enum(2):
            self._import_to_sk(morph, basis, 2, "")

        for L1, basis in L1:
            if L1:
                for morph in self.storage.enum(2, L1):
                    self._import_to_sk(morph, basis, 2, L1)

    def import_expressions(self):
        basis = self._ensure_basis()
        self._counter_lev = 3
        self._counter_cnt = 1
        for morph in self.storage.enum(3):
            self._import_to_sk(morph, basis, 3)

d_minmax = {"min": 0, "max": 1}
def convertSigns(signs):
    try:
        return sum(d_minmax[sign] << i for i, sign in enumerate(signs))
    except KeyError:
        return -1

class MorphCombiner:
    def __init__(self):
        self.morphs_dict = {}
        self.morphs_list = []
        self.morphs_combo = {}

    def add_morph(self, morph, data):
        if morph is Separator:
            self.morphs_list.append(Separator)
            return

        nameParts = morph.name.split("_")

        signIdx = -1
        if len(nameParts) == 3:
            signArr = nameParts[2].split("-")
            signIdx = convertSigns(signArr)

        if signIdx < 0:
            self.morphs_dict[morph.name] = MinMaxMorph(morph.name, data, morph.min, morph.max)
            return

        names = nameParts[1].split("-")

        if len(names) == 0 or len(names) != len(signArr):
            logger.error("Invalid L2 morph name: %s, skipping", morph.name)
            return

        morph_name = "_".join(nameParts[:2])
        cnt = 2 ** len(names)

        if len(names) == 1:
            target = self.morphs_dict
        else:
            target = self.morphs_combo

        if morph_name in target:
            target_morph = target[morph_name]
            if len(target_morph.data) != cnt:
                logger.error("L2 combo morph conflict: different dimension count on %s, skipping", morph.name)
                return
        else:
            target_morph = MinMaxMorph(morph_name, [None] * cnt)
            target[morph_name] = target_morph
            if len(names) == 1:
                self.morphs_list.append(target_morph)
            else:
                for name in names:
                    full_name = "_".join((nameParts[0], name))
                    if full_name in self.morphs_dict:
                        continue
                    new_morph = MinMaxMorph(full_name, None, -1, 1)
                    self.morphs_dict[new_morph.name] = new_morph
                    self.morphs_list.append(new_morph)

        for sign in signArr:
            if sign == "min":
                target_morph.min = min(target_morph.min, -morph.max)
            elif sign == "max":
                target_morph.max = max(target_morph.max, morph.max)

        target_morph.data[signIdx] = data


def mblab_to_charmorph(data):
    return {
        "morphs": {k:v*2-1 for k, v in data.get("structural", {}).items()},
        "materials": data.get("materialproperties", {}),
        "meta": {(k[10:] if k.startswith("character_") else k):v for k, v in data.get("metaproperties", {}).items() if not k.startswith("last_character_")},
        "type": data.get("type", ()),
    }

def charmorph_to_mblab(data):
    return {
        "structural": {k:(v+1)/2 for k, v in data.get("morphs", {}).items()},
        "metaproperties": {k:v for sublist, v in (([("character_"+k), ("last_character_"+k)], v) for k, v in data.get("meta", {}).items()) for k in sublist},
        "materialproperties": data.get("materials"),
        "type": data.get("type", ()),
    }

def load_morph_data(fn):
    with open(fn, "r", encoding="utf-8") as f:
        if fn[-5:] == ".yaml":
            return utils.load_yaml(f)
        if fn[-5:] == ".json":
            return mblab_to_charmorph(json.load(f))
    return None