# NOTE: Unfortunately, Blender does not allow the bl_info to be a variable. It must be a literal.
#       And because we need the bl_info in submodule we need to define it here - again. We cannot pass __init__.py to 
#       a submodule because those submodules are loaded into __init__.py - which will cause a circular reference..
bl_info = {
    "name": "CharMorph",
    "author": "Michael Vigovsky",
    "version": (0, 3, 5),
    "blender": (3, 3, 0),
    "location": "View3D > Tools > CharMorph",
    "description": "Character creation and morphing, cloth fitting and rigging tools",
    'wiki_url': "",
    'tracker_url': 'https://github.com/Upliner/CharMorph/issues',
    "category": "Characters"
}
