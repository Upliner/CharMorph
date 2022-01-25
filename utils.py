import time, logging, numpy
import bpy, mathutils # pylint: disable=import-error

from . import yaml

logger = logging.getLogger(__name__)

epsilon = 1e-30

class Timer:
    def __init__(self):
        self.t = time.monotonic()
    def time(self, name):
        t2 = time.monotonic()
        logger.debug("%s: %s", name, t2-self.t)
        self.t = t2

# set some yaml styles
class MyDumper(yaml.Dumper):
    pass
MyDumper.add_representer(list, lambda dumper, value: dumper.represent_sequence('tag:yaml.org,2002:seq', value, flow_style=True))
MyDumper.add_representer(float, lambda dumper, value: dumper.represent_float(round(value, 5)))

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

def is_true(value):
    if isinstance(value, str):
        return value.lower() in {'true', '1', 'y', 'yes'}
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return False

class named_lazyprop:
    __slots__ = ("fn", "name")
    def __init__(self, name, fn):
        self.fn = fn
        self.name = name

    def __get__(self, instance, owner):
        value = self.fn(instance)
        setattr(instance, self.name, value)
        return value

class lazyprop(named_lazyprop):
    __slots__ = ()
    def __init__(self, fn):
        super().__init__(fn.__name__, fn)
