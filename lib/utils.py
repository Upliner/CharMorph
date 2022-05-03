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
# Copyright (C) 2021-2022 Michael Vigovsky

import os, time, logging, numpy
import bpy, mathutils  # pylint: disable=import-error

logger = logging.getLogger(__name__)

generative_modifiers = frozenset((
    "ARRAY", "BEVEL", "BOOLEAN", "BUILD", "DECIMATE", "EDGE_SPLIT", "MASK", "MIRROR", "MULTIRES",
    "REMESH", "SCREW", "SKIN", "SOLIDIFY", "SUBSURF", "WELD", "WIREFRAME"))

# YAML stuff

try:
    from yaml import load as yload, dump as ydump, CSafeLoader as SafeLoader, Dumper
except ImportError:
    from .yaml import load as yload, dump as ydump, SafeLoader, Dumper
    logger.debug("Using bundled yaml library!")


# set some yaml styles
class MyDumper(Dumper):
    pass


MyDumper.add_representer(
    list, lambda dumper, value:
        dumper.represent_sequence('tag:yaml.org,2002:seq', value, flow_style=True))
MyDumper.add_representer(
    float, lambda dumper, value:
        dumper.represent_float(round(value, 5)))


def load_yaml(data):
    return yload(data, Loader=SafeLoader)


def dump_yaml(data, f):
    return ydump(data, f, Dumper=MyDumper)


#########
class Timer:
    def __init__(self):
        self.t = time.perf_counter()

    def time(self, name):
        t2 = time.perf_counter()
        logger.debug("%s: %s", name, t2 - self.t)
        self.t = t2


class named_lazyprop:
    __slots__ = ("fn", "name")

    def __init__(self, name, fn):
        self.fn = fn
        self.name = name

    def __get__(self, instance, owner):
        if instance is None:
            return None
        value = self.fn(instance)
        setattr(instance, self.name, value)
        return value


class lazyproperty(named_lazyprop):
    __slots__ = ()

    def __init__(self, fn):
        super().__init__(fn.__name__, fn)


def parse_file(path, parse_func, default):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return parse_func(f)
    except Exception as e:
        logger.error(e)
        return default


def parse_color(val):
    if isinstance(val, list):
        if len(val) == 3:
            return val + [1]
        return val
    return [0, 0, 0, 0]


def reset_transforms(obj):
    obj.location = (0, 0, 0)
    obj.delta_location = (0, 0, 0)
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = (1, 0, 0, 0)
    obj.delta_rotation_quaternion = (1, 0, 0, 0)
    obj.scale = (1, 1, 1)
    obj.delta_scale = (1, 1, 1)


def copy_transforms(target, source):
    target.location = source.location
    target.rotation_mode = source.rotation_mode
    target.rotation_euler = source.rotation_euler
    target.rotation_quaternion = source.rotation_quaternion
    target.scale = source.scale


def apply_transforms(obj):
    obj.data.transform(obj.matrix_world)
    reset_transforms(obj)


def lock_obj(obj, is_lock):
    obj.lock_location = (is_lock, is_lock, is_lock)
    obj.lock_rotation = (is_lock, is_lock, is_lock)
    obj.lock_rotation_w = is_lock
    obj.lock_rotations_4d = is_lock
    obj.lock_scale = (is_lock, is_lock, is_lock)


def kdtree_from_verts_enum(verts, cnt):
    kd = mathutils.kdtree.KDTree(cnt)
    for idx, vert in verts:
        kd.insert(vert, idx)
    kd.balance()
    return kd


def kdtree_from_verts(verts):
    return kdtree_from_verts_enum(((idx, vert.co) for idx, vert in enumerate(verts)), len(verts))


def kdtree_from_np(verts):
    return kdtree_from_verts_enum(enumerate(verts), len(verts))


def get_basis_verts(data):
    if isinstance(data, bpy.types.Object):
        data = data.data
    k = data.shape_keys
    if k:
        return k.reference_key.data
    return data.vertices


def verts_to_numpy(data):
    arr = numpy.empty(len(data) * 3)
    data.foreach_get("co", arr)
    return arr.reshape(-1, 3)


def get_basis_numpy(data):
    return verts_to_numpy(get_basis_verts(data))


def get_morphed_shape_key(obj):
    k = obj.data.shape_keys
    if k and k.key_blocks:
        result = k.key_blocks.get("charmorph_final")
        if result:
            return result, False

    # Creating mixed shape key every time causes some minor UI glitches. Any better idea?
    return obj.shape_key_add(from_mix=True), True


def get_morphed_numpy(obj):
    if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
        return get_basis_numpy(obj)
    morphed_shapekey, temporary = get_morphed_shape_key(obj)
    try:
        return verts_to_numpy(morphed_shapekey.data)
    finally:
        if temporary:
            obj.shape_key_remove(morphed_shapekey)


def get_target(obj):
    if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
        return obj.data.vertices
    sk = obj.data.shape_keys.key_blocks.get("charmorph_final")
    if sk is None:
        sk = obj.shape_key_add(name="charmorph_final", from_mix=False)
    if sk.value < 0.75:
        sk.value = 1
    return sk.data


# Temporarily disable all modifiers that can make vertex mapping impossible
def disable_modifiers(obj, predicate=lambda m: m.type in generative_modifiers):
    lst = []
    for m in obj.modifiers:
        if predicate(m) and m.show_viewport:
            m.show_viewport = False
            lst.append(m)
    return lst


def is_true(value):
    if isinstance(value, str):
        return value.lower() in {'true', '1', 'y', 'yes'}
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return False


def get_prefs():
    return bpy.context.preferences.addons.get("CharMorph")


def visible_mesh_poll(_, obj):
    return obj.type == "MESH" and obj.visible_get()


def is_adult_mode():
    prefs = get_prefs()
    if not prefs:
        return False
    return prefs.preferences.adult_mode


def reposition_modifier(obj, i):
    override = {"object": obj}
    pos = len(obj.modifiers) - 1
    name = obj.modifiers[pos].name

    for _ in range(pos - i):
        if bpy.ops.object.modifier_move_up.poll(override):
            bpy.ops.object.modifier_move_up(override, modifier=name)


def reposition_armature_modifier(char):
    for i, mod in enumerate(char.modifiers):
        if mod.type != "ARMATURE":
            reposition_modifier(char, i)
            return


def reposition_cs_modifier(char):
    i = len(char.modifiers) - 1
    while i >= 0:
        if char.modifiers[i].type == "ARMATURE":
            reposition_modifier(char, i + 1)
            return
        i -= 1


def reposition_subsurf_modifier(char):
    i = len(char.modifiers) - 1
    while i >= 0:
        if char.modifiers[i].type in ["ARMATURE", "CORRECTIVE_SMOOTH", "MASK"]:
            reposition_modifier(char, i + 1)
            return
        i -= 1


def import_obj(file, obj, typ="MESH", link=True):
    with bpy.data.libraries.load(file) as (data_from, data_to):
        if obj not in data_from.objects:
            if len(data_from.objects) == 1:
                obj = data_from.objects[0]
            else:
                logger.error("object %s is not found in %s", obj, file)
                return None
        data_to.objects = [obj]
    obj = data_to.objects[0]
    if obj.type != typ:
        bpy.data.objects.remove(obj)
        return None
    if link:
        bpy.context.collection.objects.link(obj)
    return obj


def np_matrix_transform(arr, mat):
    arr.dot(numpy.array(mat.to_3x3().transposed(), dtype=arr.dtype), arr)
    arr += numpy.array(mat.translation)


def set_hair_points(obj, cnts, morphed):
    t = Timer()
    np_matrix_transform(morphed[1:], obj.matrix_world)
    psys = obj.particle_systems.active
    have_mismatch = False
    t.time("hcalc")

    # I wish I could just get a transformation matrix for every particle and avoid these disconnects/connects!
    override = {"object": obj}
    bpy.ops.particle.disconnect_hair(override)
    t.time("disconnect")
    try:
        pos = 0
        for p, cnt in zip(psys.particles, cnts):
            if len(p.hair_keys) != cnt + 1:
                if not have_mismatch:
                    logger.error("Particle mismatch %d %d", len(p.hair_keys), cnt)
                    have_mismatch = True
                continue
            marr = morphed[pos:pos + cnt + 1]
            marr[0] = p.hair_keys[0].co_local
            pos += cnt
            p.hair_keys.foreach_set("co_local", marr.reshape(-1))
    finally:
        t.time("hair_set")
        bpy.ops.particle.connect_hair(override)
        t.time("connect")
    return True


def get_vg_data(char, new, accumulate, verts=None):
    if verts is None:
        verts = char.data.vertices

    if isinstance(verts, numpy.ndarray):
        def get_co(i):
            return mathutils.Vector(verts[i])
    else:
        def get_co(i):
            return verts[i].co

    data = {}
    for v in char.data.vertices:
        for gw in v.groups:
            vg = char.vertex_groups[gw.group]
            if not vg.name.startswith("joint_"):
                continue
            data_item = data.get(vg.name)
            if not data_item:
                data_item = new()
                data[vg.name] = data_item
            accumulate(data_item, v, get_co(v.index), gw)
    return data


def get_vg_avg(char, verts=None):
    def accumulate(data_item, _, co, gw):
        data_item[0] += gw.weight
        data_item[1] += co * gw.weight
    return get_vg_data(char, lambda: [0, mathutils.Vector()], accumulate, verts)


def vg_weights_to_arrays(obj, name_filter):
    m = {}
    names = []
    idx = []
    weights = []
    for vg in obj.vertex_groups:
        if name_filter(vg.name):
            m[vg.index] = len(idx)
            names.append(vg.name)
            idx.append([])
            weights.append([])

    if len(names) > 0:
        for v in obj.data.vertices:
            for g in v.groups:
                i = m.get(g.group)
                if i is None:
                    continue
                idx[i].append(v.index)
                weights[i].append(g.weight)

    return names, idx, weights


def np_names(file):
    if isinstance(file, str):
        file = numpy.load(file)
    return [n.decode("utf-8") for n in bytes(file["names"]).split(b'\0')]


def vg_read_npz(z):
    idx = z["idx"]
    weights = z["weights"]
    i = 0
    for name, cnt in zip(np_names(z), z["cnt"]):
        i2 = i + cnt
        yield name, idx[i:i2], weights[i:i2]
        i = i2


def vg_read(z):
    if z is None:
        return ()
    if isinstance(z, str):
        return vg_read_npz(numpy.load(z))
    if hasattr(z, "zip"):
        return vg_read_npz(z)
    if hasattr(z, "__next__"):
        return z
    raise Exception("Invalid type for vg_read: " + z)


def char_weights_npz(obj, char):
    rig_type = obj.data.get("charmorph_rig_type")
    if rig_type is None:
        obj = obj.find_armature()
        if obj:
            rig_type = obj.data.get("charmorph_rig_type")
    if rig_type is None:
        return None
    conf = char.armature.get(rig_type)
    if conf is None:
        return None
    return conf.weights_npz


def char_rig_vg_names(char, rig):
    weights = char_weights_npz(rig, char)
    if weights:
        return np_names(weights)
    return []


def import_vg(obj, file, overwrite):
    for name, idx, weights in vg_read(file):
        if name in obj.vertex_groups:
            if overwrite:
                obj.vertex_groups.remove(obj.vertex_groups[name])
            else:
                continue
        vg = obj.vertex_groups.new(name=name)
        for i, weight in zip(idx, weights):
            vg.add([int(i)], weight, 'REPLACE')
