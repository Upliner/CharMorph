import bpy  # pylint: disable=import-error

VARS_DRIVER = ("expression", "type", "use_self")
VARS_TARGET = ("context_property", "bone_target", "data_path", "rotation_mode", "transform_space", "transform_type")

cm_map = {}


class DriverException(Exception):
    pass


def id_to_cm(t):
    if t is None:
        return "none"
    try:
        return cm_map[t.name]
    except KeyError as e:
        raise DriverException(e.args[0])


def target_data(t):
    if t.id_type != "OBJECT":
        raise DriverException("Invalid target id_type " + t.id_type)
    result = {k: getattr(t, k) for k in VARS_TARGET}
    result["cm_id"] = id_to_cm(t.id)
    return result


def variables_data(item):
    return {v.name: {
            "type": v.type,
            "targets": [target_data(t) for t in v.targets],
        } for v in item}


def driver_data(d):
    result = {k: getattr(d, k) for k in VARS_DRIVER}
    result["variables"] = variables_data(d.variables)
    return result


def get_drivers(item):
    ad = item.animation_data
    if not ad:
        return {}
    return [{
            "data_path": d.data_path,
            "array_index": d.array_index,
            "driver": driver_data(d.driver),
        } for d in item.animation_data.drivers]


def driver_items(name, obj):
    d = [m for m in (
            (name, get_drivers(obj)),
            (name+".data", get_drivers(obj.data)),
        ) if m[1]]
    if obj.type == "MESH":
        item = get_drivers(obj.data.shape_keys)
        if item:
            d.append((name+".data.shape_keys", item))
    return d


def export(**args):
    try:
        for k, v in args.items():
            cm_map[v.name] = k
        return dict(it for m in (driver_items(k, v) for k, v in args.items()) for it in m)
    finally:
        cm_map.clear()


def clear_item(item):
    ad = item.animation_data
    if not ad:
        return
    for d in ad.drivers:
        item.driver_remove(d.data_path, d.array_index)


def clear_obj(obj):
    clear_item(obj)
    clear_item(obj.data)
    if obj.type == "MESH":
        clear_item(obj.data.shape_keys)


def cm_to_id(t: str):
    if t.lower() == "none":
        return None
    try:
        return cm_map[t]
    except KeyError as e:
        raise DriverException(e.args[0])


def fill_target(t, d):
    try:
        t.id_type = "OBJECT"
    except AttributeError:
        pass
    t.id = cm_to_id(d["cm_id"])
    for k in VARS_TARGET:
        v = d.get(k)
        if v is not None:
            setattr(t, k, v)


def fill_variable(t, d):
    t.type = d["type"]
    dt = d["targets"]
    if len(t.targets) != len(dt):
        raise DriverException("Target count mismatch")
    for dst, src in zip(t.targets, dt):
        fill_target(dst, src)


def fill_driver(t, d):
    for k in VARS_DRIVER:
        v = d.get(k)
        if v is not None:
            setattr(t, k, v)
    for k, v in d["variables"].items():
        var = t.variables.new()
        var.name = k
        fill_variable(var, v)


def name_to_obj(name: str):
    parts = name.split(".")
    result = cm_to_id(parts[0])
    for k in parts[1:]:
        result = getattr(result, k)
    return result


def dimport(d, **args):
    try:
        for k, v in args.items():
            cm_map[k] = v
        for k, v in d.items():
            t = name_to_obj(k)
            for drv in v:
                try:
                    fc = t.driver_add(drv["data_path"], drv["array_index"])
                except TypeError:
                    fc = t.driver_add(drv["data_path"])
                fill_driver(fc.driver, drv["driver"])
    finally:
        cm_map.clear()
