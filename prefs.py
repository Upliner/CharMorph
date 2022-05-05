import bpy  # pylint: disable=import-error


class CharMorphPrefs(bpy.types.AddonPreferences):
    bl_idname = __package__

    adult_mode: bpy.props.BoolProperty(
        name="Adult mode",
        description="No censors, enable adult assets (genitails, pubic hair)",
    )

    def draw(self, _):
        self.layout.prop(self, "adult_mode")


def get_prefs():
    return bpy.context.preferences.addons.get(__package__)


def is_adult_mode():
    prefs = get_prefs()
    if not prefs:
        return False
    return prefs.preferences.adult_mode
