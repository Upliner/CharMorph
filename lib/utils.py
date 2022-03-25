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

import time, logging, numpy
import bpy, mathutils # pylint: disable=import-error

from . import yaml

logger = logging.getLogger(__name__)

class Timer:
    def __init__(self):
        self.t = time.perf_counter()
    def time(self, name):
        t2 = time.perf_counter()
        logger.debug("%s: %s", name, t2-self.t)
        self.t = t2

class named_lazyprop:
    __slots__ = ("fn", "name")
    def __init__(self, name, fn):
        self.fn = fn
        self.name = name

    def __get__(self, instance, owner):
        value = self.fn(instance)
        setattr(instance, self.name, value)
        return value

class lazyproperty(named_lazyprop):
    __slots__ = ()
    def __init__(self, fn):
        super().__init__(fn.__name__, fn)

# set some yaml styles
class MyDumper(yaml.Dumper):
    pass
MyDumper.add_representer(list, lambda dumper, value: dumper.represent_sequence('tag:yaml.org,2002:seq', value, flow_style=True))
MyDumper.add_representer(float, lambda dumper, value: dumper.represent_float(round(value, 5)))

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
    pos = len(obj.modifiers)-1
    name = obj.modifiers[pos].name

    for _ in range(pos-i):
        if bpy.ops.object.modifier_move_up.poll(override):
            bpy.ops.object.modifier_move_up(override, modifier=name)

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
            if len(p.hair_keys) != cnt+1:
                if not have_mismatch:
                    logger.error("Particle mismatch %d %d", len(p.hair_keys), cnt)
                    have_mismatch = True
                continue
            marr = morphed[pos:pos+cnt+1]
            marr[0] = p.hair_keys[0].co_local
            pos += cnt
            p.hair_keys.foreach_set("co_local", marr.reshape(-1))
    finally:
        t.time("hair_set")
        bpy.ops.particle.connect_hair(override)
        t.time("connect")
    return True
