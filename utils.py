import time, logging
import mathutils # pylint: disable=import-error

from . import yaml

logger = logging.getLogger(__name__)

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
        kd.insert(vert.co, idx)
    kd.balance()
    return kd

def kdtree_from_verts(verts):
    return kdtree_from_verts_enum(enumerate(verts), len(verts))

def get_basis(obj):
    k = obj.data.shape_keys
    if k:
        return k.reference_key.data
    return obj.data.vertices

def is_true(value):
    if isinstance(value, str):
        return value.lower() in ('true', '1', 'y', 'yes')
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return False
