import bpy
import requests
import zipfile
import threading
import io
import json
import os
import shutil
import traceback
import queue
from .lib import charlib
from .lib.charlib import global_data_dir
from bpy.props import StringProperty, BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import PropertyGroup, AddonPreferences, Operator
from bpy_extras.io_utils import ImportHelper
from . import addon_updater_ops 
from .global_logger import logger

undo_modes = [("S", "Simple", "Don't show additional info in undo list")]
undo_default_mode = "S"
undo_update_hook = None


def load_character_list():
   
    # The data directory is at the same level as the script
    json_file = global_data_dir.path("lists.json")
    logger.debug(f"Attempting to load JSON from: {json_file}")
    
    try:
        with open(json_file, 'r') as f:
            data = json.load(f)
        assets = data.get("assets", [])
        logger.debug(f"Loaded assets: {[asset['name'] for asset in assets]}")
        return assets
    except FileNotFoundError:
        logger.error(f"lists.json file not found at {json_file}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON in lists.json: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error loading lists.json: {e}")
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
    
    data_path: StringProperty(
        name="Data Path",
        description="Path to CharMorph data",
        default=global_data_dir.path(), # This is just used when the addon is initially enabled.
        subtype='DIR_PATH',
        update=lambda self, context: self.on_data_path_updated(context)  # Update callback
    )

    def on_data_path_updated(self, context):
        """Callback triggered when data_path is updated."""

        dest_dir = os.path.normcase(os.path.normpath(self.data_path))
        source_dir = os.path.normcase(os.path.normpath(global_data_dir.path()))

        if not dest_dir:
            logger.debug("No directory selected for migration.")
            self.data_path = source_dir # Revert to source_dir on failure
            return {'CANCELLED'}

        # This is a very important check. When anything goes wrong in the migration we set the path back to the original one.
        # That will then cause this to be the same and the migration to stop from run ning into an infinite loop.
        if dest_dir == source_dir:
            logger.debug("New path is the same as the current path. No migration needed.")
            return
        
        # If the source is empty then there is nothing to do.
        if not os.path.exists(source_dir) or not os.listdir(source_dir):
            logger.info("The Source directory is empty or does not exist. So nothing to do except to change the path.")
            global_data_dir.migrate_data(dest_dir, move=False)
            return 

        # Invoke the confirmation dialog
        bpy.ops.charmorph.confirm_migration('INVOKE_DEFAULT')

    character_list: CollectionProperty(type=CharacterItem)

    # add-on updater preferences
    auto_check_update = bpy.props.BoolProperty(
        name="Auto-check for Update",
        description="If enabled, auto-check for updates using an interval",
        default=False,
    )
    updater_interval_months = bpy.props.IntProperty(
        name='Months',
        description="Number of months between checking for updates",
        default=0,
        min=0
    )
    updater_interval_days = bpy.props.IntProperty(
        name='Days',
        description="Number of days between checking for updates",
        default=7,
        min=0,
        max=31
    )
    updater_interval_hours = bpy.props.IntProperty(
        name='Hours',
        description="Number of hours between checking for updates",
        default=0,
        min=0,
        max=23
    )
    updater_interval_minutes = bpy.props.IntProperty(
        name='Minutes',
        description="Number of minutes between checking for updates",
        default=0,
        min=0,
        max=59
    )


    def load_characters(self):
        self.character_list.clear()
        characters = load_character_list()
        logger.debug(f"Loaded characters: {[character['name'] for character in characters]}") 
        for char in characters:
            item = self.character_list.add()
            item.name = char.get("name", "")
            item.license = char.get("license", "")
            item.downloaded = char.get("downloaded", False)
            item.repo = char.get("repo", "")  # Make sure to add this line
        logger.debug(f"Character list after loading: {[character['name'] for character in list(self.character_list)]}")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "undo_mode")
        layout.prop(self, "adult_mode")
        
        layout.prop(self, "data_path")

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

        addon_updater_ops.update_settings_ui(self,context)


class CHARMORPH_OT_confirm_migration(Operator):
    bl_idname = "charmorph.confirm_migration"
    bl_label = "Confirm Data Migration"
    bl_description = "Confirm migration of data to the new directory"

    def execute(self, context):
        """Perform the data migration."""
        prefs = context.preferences.addons[__package__].preferences
        source_dir = os.path.normcase(os.path.normpath(global_data_dir.path()))

        # Normalize the paths for comparison
        dest_dir = os.path.normcase(os.path.normpath(prefs.data_path))

        # Make sure the destination directory exists
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as e:
            self.report({'ERROR'}, f"Error creating destination directory: {e}: {dest_dir}")
            prefs.data_path = source_dir  # Revert to source_dir on failure
            return {'CANCELLED'}
        
        try:
            # Use the migrate_data function from DataDir
            global_data_dir.migrate_data(dest_dir)
            self.report({'INFO'}, f"Data migrated successfully to: {dest_dir}")
        except Exception as e:
            self.report({'ERROR'}, f"Error during data migration: {str(e)}")
            prefs.data_path = source_dir  # Revert to source_dir on failure
            return {'CANCELLED'}

        return {'FINISHED'}

    def invoke(self, context, event):
        """Show a confirmation dialog before migrating data."""
        prefs = context.preferences.addons[__package__].preferences

        # Normalize the paths for comparison
        dest_dir = os.path.normcase(os.path.normpath(prefs.data_path))
        source_dir = os.path.normcase(os.path.normpath(global_data_dir.path()))

        if not os.path.exists(source_dir):
            self.report({'ERROR'}, f"Source directory not found: {source_dir}")
            self.cancel(context)
            return {'CANCELLED'}
        
        return context.window_manager.invoke_confirm(self, event)
    
    def cancel(self, context):
        # This method is called if the user cancels the dialog
        self.report({'INFO'}, "Migration cancelled. Reverting to previous directory. See logs for more detail.")
        prefs = context.preferences.addons[__package__].preferences
        source_dir = os.path.normcase(os.path.normpath(global_data_dir.path()))
        prefs.data_path = source_dir 


# Global variables to track progress
download_progress_value = 0.0
is_downloading = False
download_progress_text = "Idle"
draw_handler_added = False  # Flag to track if the draw handler is added

# Draw handler for status bar
def draw_download_progress(self, context):
    global download_progress_value, download_progress_text, is_downloading
    
    layout = self.layout
    
    if is_downloading:
        # Create three columns: left spacer, center content, right spacer
        row = layout.row()
        
        # Left column - flexible to push content to center
        left_col = row.column()
        left_col.alignment = 'EXPAND'
        left_col.label(text="")
        
        # Center column - contains our progress info
        center_col = row.column()
        center_col.alignment = 'CENTER'
        
        # Create a row for the progress text and bar
        prog_row = center_col.row(align=True)
        prog_row.alignment = 'CENTER'
        prog_row.label(text=download_progress_text)
        prog_row.separator()
        
        # Make the progress bar a reasonable width
        prog_bar = prog_row.column()
        prog_bar.scale_x = 3.0  # Adjust this value to change width
        
        # Ensure progress value is valid (between 0 and 1)
        safe_progress = min(1.0, max(0.0, download_progress_value))
        prog_bar.progress(factor=safe_progress, type='BAR')
        
        # Right column - flexible to push content to center
        right_col = row.column()
        right_col.alignment = 'EXPAND'
        right_col.label(text="")

class CHARMORPH_OT_download_character(Operator):
    bl_idname = "charmorph.download_character"
    bl_label = "Download Character"
    character_name: StringProperty()
    
    # These can be class variables
    timer = None
    downloading = False
    download_thread = None
    download_size = 0
    downloaded_size = 0
    error_message = ""
    progress_queue = None

    def invoke(self, context, event):
        # Initialize variables here
        self.downloading = False
        self.download_thread = None
        self.download_size = 0
        self.downloaded_size = 0
        self.error_message = ""
        self.progress_queue = None
        
        # Then proceed with execute logic
        return self.execute(context)

    def execute(self, context):
        global is_downloading, download_progress_text, draw_handler_added, download_progress_value
        
        prefs = context.preferences.addons[__package__].preferences
        character = next((c for c in prefs.character_list if c.name == self.character_name), None)
        if not character:
            self.report({'ERROR'}, f"Character {self.character_name} not found")
            return {'CANCELLED'}

        # Start download process
        self.downloading = True
        is_downloading = True
        download_progress_value = 0.0
        self.error_message = ""
        self.download_size = 0
        self.downloaded_size = 0
        download_progress_text = f"Preparing to download {self.character_name}..."

        # Create a queue for thread communication
        self.progress_queue = queue.Queue()

        # Add our drawing function to the status bar if not already added
        if not draw_handler_added:
            bpy.types.STATUSBAR_HT_header.append(draw_download_progress)
            draw_handler_added = True

        # Start the download thread
        self.download_thread = threading.Thread(
            target=self.download_and_extract,
            args=(self.character_name, character.repo, global_data_dir.path("characters")),
            daemon=True  # Make thread daemon so it exits when Blender exits
        )
        self.download_thread.start()

        wm = context.window_manager
        self.timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        
        # Force initial redraw to show status bar
        for area in context.screen.areas:
            area.tag_redraw()

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        global download_progress_value, download_progress_text, is_downloading, draw_handler_added

        if event.type == 'TIMER':
            try:
                updated = False
                while not self.progress_queue.empty():
                    updated = True
                    msg_type, msg_data = self.progress_queue.get_nowait()

                    if msg_type == 'size':
                        self.download_size = msg_data
                    elif msg_type == 'progress':
                        self.downloaded_size = msg_data
                        if self.download_size > 0:
                            # Update global progress variables for the status bar
                            download_progress_value = min(1.0, max(0.0, self.downloaded_size / self.download_size))
                            
                            downloaded_mb = self.downloaded_size / (1024 * 1024)
                            total_mb = self.download_size / (1024 * 1024)
                            download_progress_text = f"Downloading {self.character_name}: {int(download_progress_value * 100)}% ({downloaded_mb:.1f} MB / {total_mb:.1f} MB)"
                    elif msg_type == 'complete':
                        self.downloading = False
                        download_progress_text = f"Character {self.character_name} downloaded successfully"
                        download_progress_value = 1.0  # Ensure it shows 100%
                        self.report({'INFO'}, download_progress_text)
                    elif msg_type == 'error':
                        self.error_message = msg_data
                        self.downloading = False
                        download_progress_text = f"Error: {self.error_message}"
                        self.report({'ERROR'}, self.error_message)

                    self.progress_queue.task_done()
                
                # Force UI redraw if there were any updates
                if updated:
                    # Force all areas to redraw to ensure status bar updates
                    for window in context.window_manager.windows:
                        for area in window.screen.areas:
                            area.tag_redraw()
                            
            except queue.Empty:
                pass

            if not self.downloading:
                # Clean up and exit modal
                self.cleanup_status_bar()
                wm = context.window_manager
                wm.event_timer_remove(self.timer)
                
                def clear_status():
                    global download_progress_text, is_downloading
                    download_progress_text = "Idle"
                    is_downloading = False
                    # Force UI redraw to update status bar
                    for window in bpy.context.window_manager.windows:
                        for area in window.screen.areas:
                            area.tag_redraw()
                    return None

                bpy.app.timers.register(clear_status, first_interval=5.0)
                return {'FINISHED'}

        elif event.type in {'ESC'}:
            self.cancel(context)
            self.report({'INFO'}, "Download cancelled")
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def cleanup_status_bar(self):
        global draw_handler_added
        if draw_handler_added:
            try:
                bpy.types.STATUSBAR_HT_header.remove(draw_download_progress)
                draw_handler_added = False
            except Exception as e:
                print(f"Error removing status bar handler: {str(e)}")

    def cancel(self, context):
        global is_downloading, download_progress_text
        
        if self.timer:
            wm = context.window_manager
            wm.event_timer_remove(self.timer)
        
        self.cleanup_status_bar()
        is_downloading = False
        download_progress_text = "Download cancelled"

    # ADD THIS METHOD TO THE CLASS
    def download_and_extract(self, character_name, repo, download_dir):
        global is_downloading, download_progress_text, download_progress_value
        
        try:
            # Fetch release data
            response = requests.get(repo)
            response.raise_for_status()
            release_data = response.json()

            zip_url = release_data['assets'][0]['browser_download_url']

            # Get file size
            response_head = requests.head(zip_url)
            total_size = int(response_head.headers.get('content-length', 0))
            self.progress_queue.put(('size', total_size))

            # Download file
            response = requests.get(zip_url, stream=True)
            response.raise_for_status()

            zip_content = io.BytesIO()
            downloaded_size = 0

            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    zip_content.write(chunk)
                    downloaded_size += len(chunk)
                    self.progress_queue.put(('progress', downloaded_size))

            zip_content.seek(0)

            # Extract file
            with zipfile.ZipFile(zip_content) as zip_ref:
                zip_ref.extractall(download_dir)

            # Update character status
            def update_character_status():
                prefs = bpy.context.preferences.addons[__package__].preferences
                character = next((c for c in prefs.character_list if c.name == character_name), None)
                if character:
                    character.downloaded = True
                return None

            bpy.app.timers.register(update_character_status, first_interval=0.1)

            self.progress_queue.put(('complete', None))

        except requests.exceptions.RequestException as e:
            error_msg = f"Network error downloading character: {str(e)}"
            self.progress_queue.put(('error', error_msg))
        except zipfile.BadZipFile as e:
            error_msg = f"Invalid zip file: {str(e)}"
            self.progress_queue.put(('error', error_msg))
        except KeyError as e:
            error_msg = f"Error parsing release data: {str(e)}"
            self.progress_queue.put(('error', error_msg))
        except Exception as e:
            error_msg = f"Error downloading character: {str(e)}\n{traceback.format_exc()}"
            self.progress_queue.put(('error', error_msg))

class CHARMORPH_OT_delete_character(Operator):
    bl_idname = "charmorph.delete_character"
    bl_label = "Delete Character"
    character_name: StringProperty()
    
    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        character = next((c for c in prefs.character_list if c.name == self.character_name), None)
        
        if not character:
            self.report({'ERROR'}, f"Character {self.character_name} not found")
            return {'CANCELLED'}

        character_dir = global_data_dir.path( "characters", self.character_name)
        
        if os.path.exists(character_dir):
            try:
                shutil.rmtree(character_dir)
                character.downloaded = False
                self.report({'INFO'}, f"Character {self.character_name} deleted successfully")
            except Exception as e:
                self.report({'ERROR'}, f"Error deleting character: {str(e)}")
                return {'CANCELLED'}
        else:
            self.report({'WARNING'}, f"Character directory {character_dir} not found")
            return {'CANCELLED'}
        
        return {'FINISHED'}


def is_adult_mode():
    prefs = bpy.context.preferences.addons[__package__].preferences
    return prefs.adult_mode


def get_prefs():
    return bpy.context.preferences.addons.get(__package__)


# Apply annotations to remove Blender 2.8+ warnings, no effect on 2.7
annotated_classes = [addon_updater_ops.make_annotations(cls) for cls in [
    CharacterItem,
    CharMorphPrefs,
    CHARMORPH_OT_confirm_migration,
    CHARMORPH_OT_download_character,
    CHARMORPH_OT_delete_character]
]


register_classes, unregister_classes = bpy.utils.register_classes_factory(annotated_classes)

def load_characters_timer():
    prefs = bpy.context.preferences.addons[__package__].preferences
    prefs.load_characters()
    return None  # Stop the timer after running once

def register():
    # Addon updater code and configurations.
	# In case of a broken version, try to register the updater first so that
	# users can revert back to a working version.
    addon_updater_ops.register()
    register_classes()


    # Start a timer to load characters after 1 second, because the preferences are not completely available until after registration completes.
    bpy.app.timers.register(load_characters_timer, first_interval=1.0)


def unregister():
    unregister_classes()
    addon_updater_ops.unregister()



if __name__ == "__main__":
    register()