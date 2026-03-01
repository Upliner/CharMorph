import bpy
import requests
import zipfile
import io
import json
import os
import shutil
from .lib import charlib
from .lib.charlib import DataDir
from bpy.props import StringProperty, BoolProperty, CollectionProperty, EnumProperty
from bpy.types import PropertyGroup, AddonPreferences, Operator
from bpy_extras.io_utils import ImportHelper


undo_modes = [("S", "Simple", "Don't show additional info in undo list")]
undo_default_mode = "S"
undo_update_hook = None

def load_character_list():
    # Get the directory of the current script (prefs.py)
    script_dir = os.path.dirname(os.path.realpath(__file__))
    
    # The data directory is at the same level as the script
    data_dir = DataDir.dirpath
    json_file = os.path.join(data_dir, "lists.json")
    
    print(f"Attempting to load JSON from: {json_file}")  # Debug print
    
    try:
        with open(json_file, 'r') as f:
            data = json.load(f)
        assets = data.get("assets", [])
        print(f"Loaded assets: {assets}")  # Debug print
        return assets
    except FileNotFoundError:
        print(f"lists.json file not found at {json_file}")
        return []
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON in lists.json: {e}")
        return []
    except Exception as e:
        print(f"Unexpected error loading lists.json: {e}")
        return []

class CharacterItem(PropertyGroup):
    name: StringProperty(name="Character Name")
    license: StringProperty(name="License")
    downloaded: BoolProperty(name="Downloaded", default=False)
    repo: StringProperty(name="Repository URL")  # Added repo property

if "undo_push" in dir(bpy.ops.ed):
    undo_modes.append(("A", "Advanced", "Undo system with full info. Can cause problems on some systems."))
    undo_default_mode = "A"

class CharMorphPrefs(AddonPreferences):
    bl_idname = __package__

    undo_mode: EnumProperty(
        name="Undo mode",
        description="Undo mode",
        items=[
            ("S", "Simple", "Don't show additional info in undo list"),
            ("A", "Advanced", "Undo system with full info. Can cause problems on some systems.")
        ],
        default="S"
    )
    adult_mode: BoolProperty(
        name="Adult mode",
        description="No censors, enable adult assets (genitals, pubic hair)",
        default=False
    )
    
    character_list: CollectionProperty(type=CharacterItem)

    data_path: StringProperty(
        name="Data Path",
        description="Path to CharMorph data",
        default="",
        subtype='DIR_PATH'
    )

    def __init__(self):
        print("CharMorphPrefs __init__ called")  # Debug print
        super().__init__()
        self.load_characters()



    def load_characters(self):
        print("load_characters method called")  # Debug print
        self.character_list.clear()
        characters = load_character_list()
        print(f"Loaded characters: {characters}")  # Debug print
        for char in characters:
            item = self.character_list.add()
            item.name = char.get("name", "")
            item.license = char.get("license", "")
            item.downloaded = char.get("downloaded", False)
            item.repo = char.get("repo", "")  # Make sure to add this line
        print(f"Character list after loading: {list(self.character_list)}")  # Debug print

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "undo_mode")
        layout.prop(self, "adult_mode")
        
        row = layout.row()
        row.prop(self, "data_path")
        row.operator("charmorph.select_directory", text="Migrate Data")

        layout.label(text="Available Characters:")
        if len(self.character_list) == 0:
            layout.label(text="No characters available")
        else:
            for char in self.character_list:
                row = layout.row()
                row.label(text=char.name)
                row.label(text=char.license)
                if char.downloaded:
                    row.operator("charmorph.delete_character", text="Delete", icon='TRASH').character_name = char.name
                else:
                    row.operator("charmorph.download_character", text="Download", icon='IMPORT').character_name = char.name

class CHARMORPH_OT_select_directory(Operator, ImportHelper):
    bl_idname = "charmorph.select_directory"
    bl_label = "Select Directory for Data Migration"
    
    filename_ext = ""
    use_filter_folder = True
    
    directory: StringProperty(
        name="Destination Folder",
        description="Select the destination folder for data migration",
        subtype='DIR_PATH'
    )
    
    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        source_dir = os.path.expanduser(os.path.join("~", charlib.DataDir.dirpath))
        print(f"Source directory: {source_dir}")  # Debug print
        if not os.path.exists(source_dir):
            self.report({'ERROR'}, f"Source directory not found: {source_dir}")
            return {'CANCELLED'}
        
        try:
            # Use the migrate_data function from DataDir
            charlib.global_data_dir.migrate_data(self.directory)
            prefs.data_path = self.directory
            self.report({'INFO'}, f"Data migrated successfully to: {self.directory}")
        except Exception as e:
            self.report({'ERROR'}, f"Error during data migration: {str(e)}")
            return {'CANCELLED'}
        
        return {'FINISHED'}

class CHARMORPH_OT_download_character(Operator):
    bl_idname = "charmorph.download_character"
    bl_label = "Download Character"
    character_name: StringProperty()
    
    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        character = next((c for c in prefs.character_list if c.name == self.character_name), None)
        
        if not character:
            self.report({'ERROR'}, f"Character {self.character_name} not found")
            return {'CANCELLED'}
        
        try:
            # Get the latest release info
            response = requests.get(character.repo)
            release_data = response.json()
            
            # Get the ZIP file URL
            zip_url = release_data['assets'][0]['browser_download_url']
            
            # Download the ZIP file
            response = requests.get(zip_url)
            zip_content = io.BytesIO(response.content)
            
            # Extract the ZIP file to the data directory
            with zipfile.ZipFile(zip_content) as zip_ref:
                zip_ref.extractall(os.path.join(charlib.DataDir.dirpath, "characters"))
            
            character.downloaded = True
            self.report({'INFO'}, f"Character {self.character_name} downloaded successfully")
        except Exception as e:
            self.report({'ERROR'}, f"Error downloading character: {str(e)}")
            return {'CANCELLED'}
        
        return {'FINISHED'}

class CHARMORPH_OT_delete_character(Operator):
    bl_idname = "charmorph.delete_character"
    bl_label = "Delete Character"
    character_name: StringProperty()
    
    def execute(self, context):
        # Implement character deletion logic here
        return {'FINISHED'}

classes = (
    CharacterItem,
    CharMorphPrefs,
    CHARMORPH_OT_select_directory,
    CHARMORPH_OT_download_character,
    CHARMORPH_OT_delete_character,
)

def is_adult_mode():
    prefs = bpy.context.preferences.addons[__package__].preferences
    return prefs.adult_mode

def get_prefs():
    return bpy.context.preferences.addons.get(__package__)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    # Load characters after registration
    print("About to load characters")  # Debug print
    prefs = bpy.context.preferences.addons[__package__].preferences
    prefs.load_characters()
    print(f"Characters after loading: {list(prefs.character_list)}")  # Debug print

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()

