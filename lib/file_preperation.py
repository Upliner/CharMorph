import bpy
import shutil
import os
from . import charlib

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")

class CHARACTER_OT_PrepareFile(bpy.types.Operator):
    bl_idname = "object.prepare_character_file"
    bl_label = "Prepare Character File"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH") # type: ignore

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        stored_dirpath = charlib.load_directory_path()
        if stored_dirpath is None:
            stored_dirpath = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "data"))  # Default path if no config is found
        
        # Define the target path for the .blend file
        path = f"{stored_dirpath}/characters/"
        os.mkdir(f"{path}/ExampleHuman")
        target_path = f"{path}/ExampleHuman/char.blend"  # Adjust path as needed
        shutil.copyfile(os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".", "config.yaml")), f"{path}/config.yaml")
        os.mkdir(f"{path}/morphs")
        os.mkdir(f"{path}/morphs/L1")
        os.mkdir(f"{path}/morphs/L2")

        # Ensure directory exists
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        # Copy the selected file to the target location
        if os.path.isfile(self.filepath):
            shutil.copy(self.filepath, target_path)
        else:
            self.report({'ERROR'}, "Selected file does not exist.")
            return {'CANCELLED'}

        # Open the copied file, ensuring it ends with .blend
        if target_path.endswith('.blend'):
            bpy.ops.wm.open_mainfile(filepath=target_path)
        else:
            self.report({'ERROR'}, "Invalid file format.")
            return {'CANCELLED'}

        # Operations to apply on the opened file
        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                for mod in obj.modifiers:
                    bpy.context.view_layer.objects.active = obj
                    bpy.ops.object.modifier_apply(modifier=mod.name)
                if obj.data.shape_keys:
                    bpy.context.view_layer.objects.active = obj
                    bpy.ops.object.shape_key_remove(all=True)

        bpy.ops.wm.save_mainfile()
        return {'FINISHED'}

class CHARACTER_PT_PreparationPanel(bpy.types.Panel):
    bl_label = "Character Preparation Panel"
    bl_idname = "CHARACTER_PT_preparation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Character Prep'

    def draw(self, context):
        layout = self.layout
        layout.operator("object.prepare_character_file")

classes = [
    CHARACTER_OT_PrepareFile,
    CHARACTER_PT_PreparationPanel
]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
