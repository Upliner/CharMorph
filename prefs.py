import bpy  # pylint: disable=import-error
from . import addon_updater_ops

undo_modes = [("S", "Simple", "Don't show additional info in undo list")]
undo_default_mode = "S"
undo_update_hook = None

if "undo_push" in dir(bpy.ops.ed):
    undo_modes.append(("A", "Advanced", "Undo system with full info. Can cause problems on some systems."))
    undo_default_mode = "A"


@addon_updater_ops.make_annotations
class CharMorphPrefs(bpy.types.AddonPreferences):
    bl_idname = __package__
    
    undo_mode: bpy.props.EnumProperty(
        name="Undo mode",
        description="Undo mode",
        items=undo_modes,
        default=undo_default_mode,
        update=lambda _ui, _ctx: undo_update_hook and undo_update_hook(),
    )
    adult_mode: bpy.props.BoolProperty(
        name="Adult mode",
        description="No censors, enable adult assets (genitails, pubic hair)",
        default=False,
    )
    # addon updater preferences
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
    def draw(self, context):
        self.layout.prop(self, "undo_mode")
        self.layout.prop(self, "adult_mode")
        addon_updater_ops.update_settings_ui(self,context)
        
        


def get_prefs():
    return bpy.context.preferences.addons.get(__package__)


def is_adult_mode():
    prefs = get_prefs()
    if not prefs:
        return False
    return prefs.preferences.adult_mode
