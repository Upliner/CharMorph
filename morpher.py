import bpy

def morph_prop_simple(name, skmin,skmax):
    def setter(self, value):
        if value<0:
            if skmax != None: skmax.value = 0
            if skmin != None: skmin.value = -value
        else:
            if skmin != None: skmin.value = 0
            if skmax != None: skmax.value = value
    return bpy.props.FloatProperty(name=name,
        min = -1.0, max = 1.0,
        soft_min = -1.0, soft_max = 1.0,
        precision = 3,
        subtype="FACTOR",
        get=lambda self: (0 if skmax==None else skmax.value) - (0 if skmin==None else skmin.value),
        set=setter)

def get_obj_morphs(obj):
    if obj.type != "MESH":
        return None
    result={}
    for sk in obj.data.shape_keys.key_blocks:
        if not sk.name.startswith("L2__") or not (sk.name.endswith("_min") or sk.name.endswith("_max")):
            continue
        name = sk.name[4:-4]
        if name in result:
            k = result[name]
        else:
            k = [None,None]
            result[name]=k

        if sk.name.endswith("_min"): k[0]=sk
        if sk.name.endswith("_max"): k[1]=sk
    return result

def create_charmorphs(morphs):
    del_charmorphs()
    if not morphs:
        return
    propGroup = type("CharMorpher_Dyn_PropGroup",
        (bpy.types.PropertyGroup,),
        {"__annotations__":
            dict(("prop_"+k,morph_prop_simple(k,v[0],v[1])) for k,v in morphs.items())})
    bpy.utils.register_class(propGroup)
    bpy.types.Scene.charmorphs = bpy.props.PointerProperty(type=propGroup,options={"SKIP_SAVE"})

def del_charmorphs():
    if not hasattr(bpy.types.Scene,"charmorphs"):
        return
    propGroup = bpy.types.Scene.charmorphs[1]['type']
    del bpy.types.Scene.charmorphs
    bpy.utils.unregister_class(propGroup)
