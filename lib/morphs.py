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

from genericpath import isfile
from ntpath import join
import os, abc, json, logging, numpy

from . import utils

logger = logging.getLogger(__name__)


class Morph:
    __slots__ = ()

    def apply(self, verts: numpy.ndarray, _=None):
        return verts


class FullMorph(Morph):
    __slots__ = ("delta",)

    def __init__(self, delta):
        self.delta = delta

    def get_delta(self, value):
        return self.delta if value is None else self.delta * value

    def apply(self, verts, value=None):
        verts += self.get_delta(value)
        return verts


class PartialMorph(FullMorph):
    __slots__ = ("idx",)

    def __init__(self, idx, delta):
        super().__init__(delta)
        self.idx = idx

    def apply(self, verts, value=None):
        verts[self.idx] += self.get_delta(value)
        return verts


def np_ro64(a: numpy.ndarray):
    if a is None:
        return None
    a = a.astype(numpy.float64, casting="same_kind")
    a.flags.writeable = False
    return a


def load(file):
    if not os.path.isfile(file):
        return None
    data = numpy.load(file)
    if isinstance(data, numpy.ndarray):
        return FullMorph(np_ro64(data))
    return PartialMorph(data["idx"], np_ro64(data["delta"]))


def detect_npy_npz(base):
    for ext in (".npy", ".npz"):
        path = base + ext
        if os.path.isfile(path):
            return path
    return None


def load_noext(basename):
    file = detect_npy_npz(basename)
    return load(file) if file else None


class LazyMorph(metaclass=abc.ABCMeta):
    __slots__ = ()

    @abc.abstractmethod
    def resolve(self):
        pass


class MinMaxMorphData:
    __slots__ = "name", "data", "min", "max"

    def __init__(self, name, data, minval=0, maxval=1):
        self.name = name
        self.data = data
        self.min = minval
        self.max = maxval


class MinMaxMorph(MinMaxMorphData):
    __slots__ = ()
    data: list

    def get_morph(self, idx) -> Morph:
        item = self.data[idx]
        if isinstance(item, LazyMorph):
            item = item.resolve()
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
    __slots__ = ()
    name = ""


class MorphPack:
    data: list = None

    def __init__(self, file, namedict):
        self.file = file
        self.namedict = namedict
        if not namedict:
            self._load()

    def _load(self):
        logger.debug("loading pack: %s", self.file)
        z = numpy.load(self.file)
        names = utils.np_names(z)
        self.data = []
        idx = z["idx"]
        delta = z["delta"]
        full = z.get("full")
        if self.namedict is None:
            self.namedict = {}

        full_pos = 0
        part_pos = 0
        for name, i in zip(names, z["cnt"]):
            if i >= 0:
                pos2 = part_pos+i
                item = (idx[part_pos:pos2], delta[part_pos:pos2])
                part_pos = pos2
            else:
                if i == -1:
                    item = full[full_pos]
                    full_pos += 1
                elif i == -2:
                    item = Separator
            self.namedict[name] = len(self.data)
            self.data.append(item)


    def __getitem__(self, idx):
        if self.data is None:
            self._load()
        if isinstance(idx, str):
            idx = self.namedict[idx]
        item = self.data[idx]
        if isinstance(item, tuple):
            return PartialMorph(item[0], np_ro64(item[1]))
        if isinstance(item, numpy.ndarray):
            return FullMorph(np_ro64(item))
        return item


class LazyVertsFile(LazyMorph, str):
    __slots__ = ()

    def resolve(self):
        return numpy.load(self).astype(numpy.float64, casting="same_kind")


class LazyMorphFile(LazyMorph):
    __slots__ = ("file",)

    def __init__(self, file):
        self.file = file

    def resolve(self):
        return load(self.file)


class LazyPackedMorph(LazyMorph):
    __slots__ = "pack", "idx"
    pack: MorphPack
    idx: int

    def __init__(self, pack, idx):
        self.pack = pack
        self.idx = idx

    def resolve(self):
        return self.pack[self.idx]


class MorphStorage:
    def __init__(self, char):
        self.char = char
        self.path = char.path("morphs")
        self.packs = {}

    def get_path(self, level, *names):
        return os.path.join(self.path, f"L{level}", *names)

    def get_pack_path(self, level, *names):
        if not names:
            for file in (f"L{level}", os.path.join(f"L{level}_packed", "__main__")):
                file = os.path.join(self.path, file) + ".npz"
                if os.path.isfile(file):
                    return file
            return None

        file = os.path.join(self.path, f"L{level}_packed", *names) + ".npz"
        if os.path.isfile(file):
            return file

    def _get_pack(self, level, *names):
        pack = self.packs.get((level, *names))
        if pack:
            return pack
        path = self.get_pack_path(level, *names)
        if not path:
            return None
        namedict = self.char.pack_cache.get(path)
        pack = MorphPack(path, namedict)
        if not namedict:
            self.char.pack_cache[path] = pack.namedict
        self.packs[(level, *names)] = pack
        return pack

    def get_lazy(self, level, *names) -> LazyMorph:
        if not names[-1]:
            return None
        if level == 1 and names[0] == self.char.basis:
            return self.char.np_basis
        file = detect_npy_npz(self.get_path(level, *names))
        if file:
            if level == 1:
                return LazyVertsFile(file)
            return LazyMorphFile(file)
        pack = self._get_pack(level, names[:-1])
        if pack:
            idx = pack.namedict.get(names[-1])
            if idx is not None:
                return LazyPackedMorph(pack, idx)
        return None

    def get(self, level, *names):
        l = self.get_lazy(level, *names)
        return l.resolve() if l else None

    def _json_to_morph(self, item, level, *names):
        if item.get("separator"):
            return Separator
        name = item.get("morph")
        return MinMaxMorphData(name, self.get_lazy(level, *(names + (name,))), item.get("min", 0), item.get("max", 1))

    def _enum_dir(self, path: str, lazy_class, existing_names: set):
        if not os.path.isdir(path):
            return
        for name in sorted(os.listdir(path)):
            pathname = os.path.join(path, name)
            if (name.endswith(".npz") or name.endswith(".npy")) and os.path.isfile(pathname):
                existing_names.add(name)
                yield MinMaxMorphData(name[:-4], lazy_class(pathname))

    def _enum_fs(self, path, level, *names):
        existing_names = set()
        yield from self._enum_dir(path, LazyMorphFile, existing_names)

        pack = self._get_pack(level, *names)
        if pack:
            for name, idx in pack.namedict.items():
                if name not in existing_names:
                    yield MinMaxMorphData(name, LazyPackedMorph(pack, idx))

    def enum(self, level, *names):
        path = self.get_path(level, *names)
        if level == 1:
            return self._enum_dir(path, LazyVertsFile, set())

        jslist = utils.parse_file(os.path.join(path, "morphs.json"), json.load, None)
        if jslist is not None:
            return (self._json_to_morph(item, level, *names) for item in jslist)

        return self._enum_fs(path, level, *names)


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

    def _import_to_sk(self, morph: MinMaxMorphData, basis: numpy.ndarray, level, *names):
        sk = self._create_morph_sk("_".join((f"L{level}",) + names) + "_", morph)
        if not sk:
            return "--separator--", None

        data = morph.data.resolve()
        if isinstance(data, Morph):
            data = data.apply(basis.copy())
        if level == 2 and names[0]:
            sk.relative_key = self.obj.data.shape_keys.key_blocks["L1_" + names[0]]

        sk.data.foreach_set("co", data.reshape(-1))
        return sk.name, data

    def import_morphs(self, progress):
        basis = self._ensure_basis()

        cnt = 0
        L1 = []
        L1_children = []
        for L1_morph in list(self.storage.enum(1)):
            L2_list = list(self.storage.enum(2, L1_morph.name))
            cnt += len(L2_list) + 1
            L1.append(L1_morph)
            L1_children.append(L2_list)

        L2_base = list(self.storage.enum(2))
        cnt += len(L2_base)

        self._counter_lev = 2
        self._counter_cnt = 1

        progress.enter_substeps(cnt, "Importing morphs")

        L1_data = []
        for morph in L1:
            name, data = self._import_to_sk(morph, None, 1)
            L1_data.append(data)
            progress.step(name)

        for morph in L2_base:
            progress.step(self._import_to_sk(morph, basis, 2, "")[0])

        for L1_morph, basis, children in zip(L1, L1_data, L1_children):
            for morph in children:
                progress.step(self._import_to_sk(morph, basis, 2, L1_morph.name)[0])

        progress.leave_substeps("Morphs done")

    def import_expressions(self, progress):
        basis = self._ensure_basis()
        lst = list(self.storage.enum(3))
        self._counter_lev = 3
        self._counter_cnt = 1
        progress.enter_substeps(len(lst), "Importing expressions")
        for morph in lst:
            progress.step(self._import_to_sk(morph, basis, 3)[0])
        progress.leave_substeps("Expressions done")


def convertSigns(signs):
    try:
        return sum({"min": 0, "max": 1}[sign] << i for i, sign in enumerate(signs))
    except KeyError:
        return -1


class MorphCombiner:
    def __init__(self):
        self.morphs_dict = {}
        self.morphs_list = []
        self.morphs_combo = {}

    def add_morph(self, morph):
        if morph is Separator:
            self.morphs_list.append(Separator)
            return

        nameParts = morph.name.split("_")

        signIdx = -1
        if len(nameParts) == 3:
            signArr = nameParts[2].split("-")
            signIdx = convertSigns(signArr)

        if signIdx < 0:
            result = MinMaxMorph(morph.name, [morph.data], morph.min, morph.max)
            self.morphs_dict[morph.name] = result
            self.morphs_list.append(result)
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
            target_morph = MinMaxMorph(morph_name, [None] * cnt, 0, 0)
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

        target_morph.data[signIdx] = morph.data


def mblab_to_charmorph(data):
    return {
        "morphs": {k: v * 2 - 1 for k, v in data.get("structural", {}).items()},
        "materials": data.get("materialproperties", {}),
        "meta": {
            (k[10:] if k.startswith("character_") else k): v
            for k, v in data.get("metaproperties", {}).items()
            if not k.startswith("last_character_")
        },
        "type": data.get("type", ()),
    }


def charmorph_to_mblab(data):
    return {
        "structural": {k: (v + 1) / 2 for k, v in data.get("morphs", {}).items()},
        "metaproperties": {
            k: v
            for sublist, v in (
                ([("character_" + k), ("last_character_" + k)], v)
                for k, v in data.get("meta", {}).items()
            ) for k in sublist
        },
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
