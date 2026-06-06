"""FireMaze - Random maze generator with tiled construction for Blender 4.2+.

Generates, edits, and customizes tile-based mazes with rectangular
and polar (circular) grids, multiple wall modes, procedural rooms,
pathfinding guides, vertex painting, and session save/load.
"""

import logging

bl_info = {
    "name": "FireMaze",
    "author": "FireRat666",
    "version": (3, 1, 1),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > FireRat",
    "description": "Random maze generator with tiled construction",
    "category": "Add Mesh",
    "wiki_url": "https://github.com/FireRat666/FireMaze/",
    "tracker_url": "https://github.com/FireRat666/FireMaze/issues",
}

from . import properties
from . import operators
from . import ui


def _setup_logging():
    """Configure logging for FireMaze modules to output warnings and errors to console."""
    logger = logging.getLogger("FireMaze")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('[%(name)s] %(levelname)s: %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)


def register():
    """Register all FireMaze addon classes and properties."""
    _setup_logging()
    properties.register()
    operators.register()
    ui.register()


def unregister():
    """Unregister all FireMaze addon classes and properties."""
    ui.unregister()
    operators.unregister()
    properties.unregister()
