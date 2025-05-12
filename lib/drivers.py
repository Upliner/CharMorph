import bpy  # pylint: disable=import-error
import traceback
import json
from CharMorph.global_logger import logger  # Import logger from global_logger.py

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
        raise DriverException(f"Object {t.name} not found in cm_map")


def target_data(t):
    if t.id_type != "OBJECT":
        raise DriverException(f"Invalid target id_type {t.id_type}")
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
        return []
    return [{
            "data_path": d.data_path,
            "array_index": d.array_index,
            "driver": driver_data(d.driver),
        } for d in item.animation_data.drivers]


def get_child_meshes(obj):
    """Return a list of all child meshes for the given object."""
    child_meshes = []
    logger.debug(f"Checking children of {obj.name}")
    for child in obj.children:
        logger.debug(f"Found child: {child.name} (type: {child.type})")
        if child.type == "MESH":
            child_meshes.append(child)
            logger.info(f"Confirmed child mesh: {child.name}")
    if not child_meshes:
        logger.info(f"No child meshes found for {obj.name}")
        # Fallback: search all objects in the scene for meshes parented to obj
        for o in bpy.data.objects:
            if o.type == "MESH" and o.parent == obj:
                child_meshes.append(o)
                logger.info(f"Found child mesh in scene: {o.name}")
    if not child_meshes:
        logger.info(f"No child meshes found in entire scene for {obj.name}")
    return child_meshes


def driver_items(name, obj):
    d = []
    logger.debug(f"Processing object: {name}")
    # Process the main object and its data
    main_items = [
        (name, get_drivers(obj)),
        (name + ".data", get_drivers(obj.data)),
    ]
    if obj.type == "MESH" and obj.data.shape_keys:
        item = get_drivers(obj.data.shape_keys)
        if item:
            main_items.append((name + ".data.shape_keys", item))
            logger.info(f"Found {len(item)} drivers for {name}.data.shape_keys")
    d.extend(m for m in main_items if m[1])

    # Process all child meshes
    for child in get_child_meshes(obj):
        child_name = f"{name}.{child.name}"
        logger.debug(f"Processing child mesh: {child_name}")
        child_items = [
            (child_name, get_drivers(child)),
            (child_name + ".data", get_drivers(child.data)),
        ]
        if child.type == "MESH" and child.data.shape_keys:
            item = get_drivers(child.data.shape_keys)
            if item:
                child_items.append((child_name + ".data.shape_keys", item))
                logger.info(f"Found {len(item)} drivers for {child_name}.data.shape_keys")
        d.extend(m for m in child_items if m[1])

    return d


def export(**args):
    try:
        for k, v in args.items():
            cm_map[v.name] = k
            logger.debug(f"Mapping {v.name} to {k} in cm_map")
        result = dict(it for m in (driver_items(k, v) for k, v in args.items()) for it in m)
        logger.info(f"Exported drivers: {list(result.keys())}")
        return result
    finally:
        cm_map.clear()


def clear_item(item):
    ad = item.animation_data
    if not ad:
        return
    for d in ad.drivers:
        item.driver_remove(d.data_path, d.array_index)


def clear_obj(obj):
    logger.info(f"Clearing drivers for {obj.name}")
    clear_item(obj)
    clear_item(obj.data)
    if obj.type == "MESH" and obj.data.shape_keys:
        clear_item(obj.data.shape_keys)
    for child in get_child_meshes(obj):
        logger.info(f"Clearing drivers for child {child.name}")
        clear_item(child)
        clear_item(child.data)
        if child.type == "MESH" and child.data.shape_keys:
            clear_item(child.data.shape_keys)


def cm_to_id(t: str):
    if t.lower() == "none":
        return None
    try:
        return cm_map[t]
    except KeyError as e:
        raise DriverException(f"Identifier {t} not found in cm_map")


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
        raise DriverException(f"Target count mismatch: {len(t.targets)} vs {len(dt)}")
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
    """Resolve a dotted path to a Blender object or data structure."""
    parts = name.split(".")
    logger.debug(f"Resolving object path: {name}")
    result = cm_to_id(parts[0])  # Resolve base identifier (e.g., 'char' to cm_vitruvian)
    if not result:
        raise DriverException(f"Base identifier {parts[0]} not found in cm_map")

    for k in parts[1:]:
        # Check if k is a property (e.g., 'data', 'shape_keys')
        if hasattr(result, k):
            result = getattr(result, k)
            logger.debug(f"Resolved property: {k}")
        else:
            # Check if k is a child object
            if hasattr(result, "children"):
                for child in result.children:
                    if child.name == k:
                        result = child
                        logger.debug(f"Resolved child object: {k}")
                        break
                else:
                    raise DriverException(f"Child object {k} not found under {result.name}")
            else:
                raise DriverException(f"Cannot resolve {k} on {result.name}: no children attribute")
    
    return result


def add_shape_key(obj, key_name):
    """Add a shape key to the object if it doesn't exist."""
    if not obj.data.shape_keys:
        obj.shape_key_add(name="Basis")
        logger.info(f"Added Basis shape key to {obj.name}")
    if key_name not in obj.data.shape_keys.key_blocks:
        obj.shape_key_add(name=key_name)
        logger.info(f"Added shape key {key_name} to {obj.name}")
    else:
        logger.debug(f"Shape key {key_name} already exists in {obj.name}")


def copy_drivers_to_child_meshes(obj, driver_data, overwrite=True, create_shape_keys=False):
    """Copy drivers from the main mesh's shape keys to child meshes."""
    child_meshes = get_child_meshes(obj)
    if not child_meshes:
        logger.info("No child meshes to copy drivers to")
        return
    for child in child_meshes:
        if not child.data.shape_keys and not create_shape_keys:
            logger.debug(f"Child mesh {child.name} has no shape keys, skipping")
            continue
        for drv in driver_data.get("char.data.shape_keys", []):
            shape_key_name = drv["data_path"].split('["')[1].split('"]')[0]
            if create_shape_keys:
                add_shape_key(child, shape_key_name)
            if shape_key_name not in child.data.shape_keys.key_blocks:
                logger.debug(f"Shape key {shape_key_name} not found in {child.name}, skipping")
                continue
            logger.info(f"Copying driver for {shape_key_name} to {child.name}")
            try:
                # Clear existing driver if overwrite is enabled
                if overwrite:
                    child.data.shape_keys.driver_remove(f'key_blocks["{shape_key_name}"].value')
                    logger.debug(f"Removed existing driver for {shape_key_name} in {child.name}")
                fc = child.data.shape_keys.driver_add(f'key_blocks["{shape_key_name}"].value')
                fill_driver(fc.driver, drv["driver"])
                logger.info(f"Copied driver for {shape_key_name} to {child.name}")
            except Exception as e:
                logger.error(f"Error copying driver for {shape_key_name} to {child.name}: {e}")
                logger.debug(traceback.format_exc())


def dimport(d: dict, overwrite: bool, create_shape_keys: bool = False, **args):
    try:
        for k, v in args.items():
            cm_map[k] = v
            logger.debug(f"Mapping {k} to {v.name} in cm_map")
        
        # Apply drivers to main object
        for k, v in d.items():
            logger.info(f"Importing drivers for: {k}")
            t = name_to_obj(k)
            if not t:
                raise DriverException(f"Invalid object {k}")
            for drv in v:
                try:
                    shape_key_name = drv["data_path"].split('["')[1].split('"]')[0]
                    logger.info(f"Adding driver for shape key: {shape_key_name}")
                    if "key_blocks" in drv["data_path"]:
                        # Shape key drivers don't use array_index
                        if overwrite:
                            t.driver_remove(drv["data_path"])
                            logger.debug(f"Removed existing driver: {drv['data_path']}")
                        fc = t.driver_add(drv["data_path"])
                        logger.debug(f"Added driver: {drv['data_path']} to {t.name}")
                    else:
                        # Other properties may use array_index
                        if overwrite:
                            t.driver_remove(drv["data_path"], drv["array_index"])
                            logger.debug(f"Removed existing driver: {drv['data_path']}")
                        fc = t.driver_add(drv["data_path"], drv["array_index"])
                        logger.debug(f"Added driver: {drv['data_path']} to {t.name}")
                    fill_driver(fc.driver, drv["driver"])
                    logger.debug(f"Filled driver details for {drv['data_path']} on {t.name}")
                except Exception as e:
                    logger.error(f"Error adding driver {drv['data_path']} to {t.name}: {e}")
                    logger.debug(traceback.format_exc())
                    continue

        # Copy drivers to child meshes
        main_obj = args.get("char")
        if main_obj:
            copy_drivers_to_child_meshes(main_obj, d, overwrite=overwrite, create_shape_keys=create_shape_keys)

    finally:
        cm_map.clear()