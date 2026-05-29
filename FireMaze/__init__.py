bl_info = {
    "name": "FireMaze",
    "author": "FireRat666",
    "version": (2, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > FireRat",
    "description": "Random maze generator with tiled construction",
    "category": "Add Mesh",
}

from . import properties
from . import operators
from . import ui


def register():
    properties.register()
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    properties.unregister()
