"""BMesh-based maze geometry construction and post-processing for FireMaze.

Builds floor, wall, roof, stair, guide path, collider and edit-helper
meshes for both rectangular and polar grids. Includes vertex painting,
lightmap UV generation, coplanar optimisation, and prop/decor spawning.
"""

import bpy
from ..maze_data import MazeData
from .bmesh_utils import _bmesh_cache_context
from .rect_builder import build_maze_objects_impl


def build_maze_objects(
    props: bpy.types.PropertyGroup,
    maze_data: MazeData,
    context: bpy.types.Context,
    collection: bpy.types.Collection = None,
    force_simple: bool = False,
    name_suffix: str = "",
) -> bpy.types.Collection:
    """Top-level entry point for maze mesh construction.

    Manages the BMesh cache lifecycle and dispatches to the appropriate
    polar or rectangular builder.
    """
    with _bmesh_cache_context():
        return build_maze_objects_impl(
            props, maze_data, context, collection, force_simple, name_suffix
        )
