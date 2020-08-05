import os, logging
import bpy

from . import morphing

logger = logging.getLogger(__name__)

data_dir=""
has_dir = False

data_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "data")
logger.debug("Looking for the database in the folder %s...", data_dir)

if not os.path.isdir(data_dir):
    logger.error("Charmorph data is not found at {}".format(data_dir))
else:
    has_dir=True

class CHARMORPH_PT_Creation(bpy.types.Panel):
    bl_label = "Creation"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'

    def draw(self, context):
        if data_dir == "" or not has_dir:
            self.layout.label(text= "Data dir is not found at {}. Creation is not available.".format(data_dir))
            return
        ui = context.scene.charmorph_ui
        self.layout.prop(ui, 'base_model')
        self.layout.prop(ui, 'material_mode')
        self.layout.operator('charmorph.create', icon='ARMATURE_DATA')

def import_obj(file, obj):
    with bpy.data.libraries.load(os.path.join(data_dir, file)) as (data_from, data_to):
        if obj not in data_from.objects:
            raise(obj + " object is not found")
        data_to.objects = [obj]
    bpy.context.collection.objects.link(data_to.objects[0])
    return data_to.objects[0]

class CharMorphCreate(bpy.types.Operator):
    bl_idname = "charmorph.create"
    bl_label = "Create character"
    bl_order = 1

    def execute(self, context):
        global last_object
        base_model = str(context.scene.charmorph_ui.base_model)
        if not base_model:
            raise("Please select base model")
        obj = import_obj("characters/{}/char.blend".format(base_model),"char")
        if obj == None:
            raise("Object is not found")
        obj["charmorph_template"] = base_model
        last_object = obj
        morphing.create_charmorphs(obj)
        return {"FINISHED"}

classes = [CHARMORPH_PT_Creation, CharMorphCreate]
