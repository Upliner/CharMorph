import bpy  # pylint: disable=import-error


undo_modes = [("S", "Simple", "Don't show additional info in undo list")]
undo_default_mode = "S"
undo_update_hook = None

if "undo_push" in dir(bpy.ops.ed):
    undo_modes.append(("A", "Advanced", "Undo system with full info. Can cause problems on some systems."))
    undo_default_mode = "A"


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

    def draw(self, _):
        self.layout.prop(self, "undo_mode")
        self.layout.prop(self, "adult_mode")


def get_prefs():
    return bpy.context.preferences.addons.get(__package__)


def is_adult_mode():
    prefs = get_prefs()
    if not prefs:
        return False
    return prefs.preferences.adult_mode
