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


def _find_role_object(collection, base_name):
    """Find object by exact name in collection, falling back to prefix match for Blender's .001 suffixes."""
    if not collection:
        return None
    obj = collection.objects.get(base_name)
    if obj:
        return obj
    for obj in collection.objects:
        if obj.name == base_name or obj.name.startswith(base_name + "."):
            return obj
    return None


def rebuild_maze_incrementally(
    props: bpy.types.PropertyGroup,
    maze_data: MazeData,
    context: bpy.types.Context,
    collection: bpy.types.Collection,
    dirty_cells: set,
):
    """Incrementally updates existing meshes in collection for dirty_cells in-place."""
    with _bmesh_cache_context():
        _rebuild_maze_incrementally_impl(props, maze_data, context, collection, dirty_cells)


def _rebuild_maze_incrementally_impl(
    props: bpy.types.PropertyGroup,
    maze_data: MazeData,
    context: bpy.types.Context,
    collection: bpy.types.Collection,
    dirty_cells: set,
):
    """Incremental rebuild core: delete dirty faces from existing objects then rebuild only those cells."""
    import bmesh
    from ..utils import get_cell_id
    from .post_processor import _merge_maze_objects
    from .rect_builder import build_maze_objects_impl
    from .bmesh_utils import _prepare_maze_building_context
    
    dirty_cell_ids = {get_cell_id(z, y, x) for (z, y, x) in dirty_cells}
    ctx = _prepare_maze_building_context(props, maze_data, context, collection, force_simple=False)
    
    # Check if cell_id layer is missing on any existing objects
    trigger_full_rebuild = False
    floor_obj_chk = _find_role_object(collection, "FireMaze_Floor")
    if floor_obj_chk:
        bm_check = bmesh.new()
        bm_check.from_mesh(floor_obj_chk.data)
        if bm_check.faces.layers.int.get("cell_id") is None:
            trigger_full_rebuild = True
        bm_check.free()
        
    if not trigger_full_rebuild:
        wall_obj_chk = _find_role_object(collection, "FireMaze_Walls")
        if wall_obj_chk:
            bm_check = bmesh.new()
            bm_check.from_mesh(wall_obj_chk.data)
            if bm_check.faces.layers.int.get("cell_id") is None:
                trigger_full_rebuild = True
            bm_check.free()
            
    if not trigger_full_rebuild:
        cap_obj_chk = _find_role_object(collection, "FireMaze_WallEndCaps")
        if cap_obj_chk:
            bm_check = bmesh.new()
            bm_check.from_mesh(cap_obj_chk.data)
            if bm_check.faces.layers.int.get("cell_id") is None:
                trigger_full_rebuild = True
            bm_check.free()
            
    if not trigger_full_rebuild:
        roof_obj_chk = _find_role_object(collection, "FireMaze_Roof")
        if roof_obj_chk:
            bm_check = bmesh.new()
            bm_check.from_mesh(roof_obj_chk.data)
            if bm_check.faces.layers.int.get("cell_id") is None:
                trigger_full_rebuild = True
            bm_check.free()
            
    if not trigger_full_rebuild:
        stair_obj_chk = _find_role_object(collection, "FireMaze_Stairs")
        if stair_obj_chk:
            bm_check = bmesh.new()
            bm_check.from_mesh(stair_obj_chk.data)
            if bm_check.faces.layers.int.get("cell_id") is None:
                trigger_full_rebuild = True
            bm_check.free()
            
    if not trigger_full_rebuild:
        if _find_role_object(collection, "FireMaze_Merged"):
            trigger_full_rebuild = True
            
    if trigger_full_rebuild:
        import json
        data_dict = {}
        if "fire_maze_data" in collection:
            try:
                data_dict = json.loads(collection["fire_maze_data"])
            except Exception:
                pass
        data_dict.update({
            'width': maze_data.width,
            'depth': maze_data.depth,
            'cells': maze_data.cells,
            'entrance': maze_data.entrance,
            'exits': maze_data.exits,
            'center': maze_data.center,
            'guide_path': maze_data.guide_path,
            'grid_type': maze_data.grid_type,
            'polar_rings': maze_data.polar_rings,
            'ring_sectors': maze_data.ring_sectors,
            'floors': maze_data.floors,
            'stairs': maze_data.stairs,
        })
        collection["fire_maze_data"] = json.dumps(data_dict)
        from ..operators import rebuild_maze_from_collection
        rebuild_maze_from_collection(context, collection)
        return
    
    # 1. Update the visual objects (name_suffix="")
    name_suffix = ""
    created_objects = []
    
    # Floor
    floor_obj = _find_role_object(collection, f"FireMaze_Floor{name_suffix}")
    if floor_obj:
        bm = bmesh.new()
        bm.from_mesh(floor_obj.data)
        cell_layer = bm.faces.layers.int.get("cell_id")
        if cell_layer is not None:
            faces_to_delete = [f for f in bm.faces if f[cell_layer] in dirty_cell_ids]
            bmesh.ops.delete(bm, geom=faces_to_delete, context="FACES")
            verts_to_delete = [v for v in bm.verts if not v.link_faces]
            bmesh.ops.delete(bm, geom=verts_to_delete, context="VERTS")
        uv_layer = bm.loops.layers.uv.active or bm.loops.layers.uv.new("UVMap")
        materials = list(floor_obj.data.materials)
        
        if maze_data.grid_type == 'polar':
            from .polar_builder import _build_polar_floor
            _build_polar_floor(ctx, props, maze_data, created_objects, name_suffix, bm=bm, uv_layer=uv_layer, materials=materials, dirty_cells=dirty_cells)
        else:
            if props.wall_mode == 'cube':
                from .rect_builder import _build_rect_cube_floor
                _build_rect_cube_floor(ctx, maze_data, created_objects, name_suffix, bm=bm, uv_layer=uv_layer, materials=materials, dirty_cells=dirty_cells)
            else:
                from .rect_builder import _build_rect_thin_floor
                _build_rect_thin_floor(ctx, maze_data, created_objects, name_suffix, bm=bm, uv_layer=uv_layer, materials=materials, dirty_cells=dirty_cells)
        
        bm.to_mesh(floor_obj.data)
        bm.free()
        floor_obj.data.update()
    else:
        if maze_data.grid_type == 'polar':
            from .polar_builder import _build_polar_floor
            _build_polar_floor(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
        else:
            if props.wall_mode == 'cube':
                from .rect_builder import _build_rect_cube_floor
                _build_rect_cube_floor(ctx, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
            else:
                from .rect_builder import _build_rect_thin_floor
                _build_rect_thin_floor(ctx, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)

    # Walls & Caps
    wall_obj = _find_role_object(collection, f"FireMaze_Walls{name_suffix}")
    cap_obj = _find_role_object(collection, f"FireMaze_WallEndCaps{name_suffix}")
    
    bm_wall, uv_wall, wall_materials = None, None, None
    if wall_obj:
        bm_wall = bmesh.new()
        bm_wall.from_mesh(wall_obj.data)
        cell_layer = bm_wall.faces.layers.int.get("cell_id")
        if cell_layer is not None:
            faces_to_delete = [f for f in bm_wall.faces if f[cell_layer] in dirty_cell_ids]
            bmesh.ops.delete(bm_wall, geom=faces_to_delete, context="FACES")
            verts_to_delete = [v for v in bm_wall.verts if not v.link_faces]
            bmesh.ops.delete(bm_wall, geom=verts_to_delete, context="VERTS")
        uv_wall = bm_wall.loops.layers.uv.active or bm_wall.loops.layers.uv.new("UVMap")
        wall_materials = list(wall_obj.data.materials)
        
    bm_cap, uv_cap, cap_materials = None, None, None
    if cap_obj:
        bm_cap = bmesh.new()
        bm_cap.from_mesh(cap_obj.data)
        cell_layer_cap = bm_cap.faces.layers.int.get("cell_id")
        if cell_layer_cap is not None:
            faces_to_delete = [f for f in bm_cap.faces if f[cell_layer_cap] in dirty_cell_ids]
            bmesh.ops.delete(bm_cap, geom=faces_to_delete, context="FACES")
            verts_to_delete = [v for v in bm_cap.verts if not v.link_faces]
            bmesh.ops.delete(bm_cap, geom=verts_to_delete, context="VERTS")
        uv_cap = bm_cap.loops.layers.uv.active or bm_cap.loops.layers.uv.new("UVMap")
        cap_materials = list(cap_obj.data.materials)
        
    if maze_data.grid_type == 'polar':
        from .polar_builder import _build_polar_walls
        _build_polar_walls(ctx, props, maze_data, created_objects, name_suffix,
                           bm=bm_wall, uv_layer=uv_wall, materials=wall_materials,
                           bm_cap=bm_cap, uv_layer_cap=uv_cap, materials_cap=cap_materials,
                           dirty_cells=dirty_cells)
    else:
        if props.wall_mode == 'cube':
            from .rect_builder import _build_rect_cube_walls
            _build_rect_cube_walls(ctx, props, maze_data, created_objects, name_suffix,
                                   bm=bm_wall, uv_layer=uv_wall, materials=wall_materials,
                                   dirty_cells=dirty_cells)
        else:
            from .rect_builder import _build_rect_thin_walls
            _build_rect_thin_walls(ctx, props, maze_data, created_objects, name_suffix,
                                   bm=bm_wall, uv_layer=uv_wall, materials=wall_materials,
                                   bm_cap=bm_cap, uv_layer_cap=uv_cap, materials_cap=cap_materials,
                                   dirty_cells=dirty_cells)
                                   
    if wall_obj and bm_wall:
        bm_wall.to_mesh(wall_obj.data)
        bm_wall.free()
        wall_obj.data.update()
    if cap_obj and bm_cap:
        bm_cap.to_mesh(cap_obj.data)
        bm_cap.free()
        cap_obj.data.update()
        
    # Roof
    roof_obj = _find_role_object(collection, f"FireMaze_Roof{name_suffix}")
    if roof_obj:
        bm = bmesh.new()
        bm.from_mesh(roof_obj.data)
        cell_layer = bm.faces.layers.int.get("cell_id")
        if cell_layer is not None:
            faces_to_delete = [f for f in bm.faces if f[cell_layer] in dirty_cell_ids]
            bmesh.ops.delete(bm, geom=faces_to_delete, context="FACES")
            verts_to_delete = [v for v in bm.verts if not v.link_faces]
            bmesh.ops.delete(bm, geom=verts_to_delete, context="VERTS")
        uv_layer = bm.loops.layers.uv.active or bm.loops.layers.uv.new("UVMap")
        materials = list(roof_obj.data.materials)
        
        if maze_data.grid_type == 'polar':
            from .polar_builder import _build_polar_roof
            _build_polar_roof(ctx, props, maze_data, created_objects, name_suffix, bm=bm, uv_layer=uv_layer, materials=materials, dirty_cells=dirty_cells)
        else:
            if props.wall_mode == 'cube':
                from .rect_builder import _build_rect_cube_roof
                _build_rect_cube_roof(ctx, props, maze_data, created_objects, name_suffix, bm=bm, uv_layer=uv_layer, materials=materials, dirty_cells=dirty_cells)
            else:
                from .rect_builder import _build_rect_thin_roof
                _build_rect_thin_roof(ctx, props, maze_data, created_objects, name_suffix, bm=bm, uv_layer=uv_layer, materials=materials, dirty_cells=dirty_cells)
                
        bm.to_mesh(roof_obj.data)
        bm.free()
        roof_obj.data.update()
    else:
        if maze_data.grid_type == 'polar':
            from .polar_builder import _build_polar_roof
            _build_polar_roof(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
        else:
            if props.wall_mode == 'cube':
                from .rect_builder import _build_rect_cube_roof
                _build_rect_cube_roof(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
            else:
                from .rect_builder import _build_rect_thin_roof
                _build_rect_thin_roof(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)

    # Stairs
    stair_obj = _find_role_object(collection, f"FireMaze_Stairs{name_suffix}")
    if stair_obj:
        bm = bmesh.new()
        bm.from_mesh(stair_obj.data)
        cell_layer = bm.faces.layers.int.get("cell_id")
        if cell_layer is not None:
            faces_to_delete = [f for f in bm.faces if f[cell_layer] in dirty_cell_ids]
            bmesh.ops.delete(bm, geom=faces_to_delete, context="FACES")
            verts_to_delete = [v for v in bm.verts if not v.link_faces]
            bmesh.ops.delete(bm, geom=verts_to_delete, context="VERTS")
        uv_layer = bm.loops.layers.uv.active or bm.loops.layers.uv.new("UVMap")
        materials = list(stair_obj.data.materials)
        
        if maze_data.grid_type == 'polar':
            from .polar_builder import _build_polar_stairs
            _build_polar_stairs(ctx, props, maze_data, created_objects, name_suffix, bm=bm, uv_layer=uv_layer, materials=materials, dirty_cells=dirty_cells)
        else:
            from .rect_builder import _build_rect_stairs
            _build_rect_stairs(ctx, props, maze_data, created_objects, name_suffix, bm=bm, uv_layer=uv_layer, materials=materials, dirty_cells=dirty_cells)
            
        bm.to_mesh(stair_obj.data)
        bm.free()
        stair_obj.data.update()
    else:
        if maze_data.grid_type == 'polar':
            from .polar_builder import _build_polar_stairs
            _build_polar_stairs(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
        else:
            from .rect_builder import _build_rect_stairs
            _build_rect_stairs(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)

    # Rebuild guide path dynamically
    guide_obj = _find_role_object(collection, "FireMaze_Guide")
    if guide_obj:
        curve = guide_obj.data
        bpy.data.objects.remove(guide_obj, do_unlink=True)
        if curve.users == 0:
            bpy.data.curves.remove(curve)
            
    from .bmesh_utils import _build_guide_path
    _build_guide_path(props, maze_data, collection, ctx['materials'])

    # 2. Update helper object "_FireMaze_Edit_Helper"
    helper_obj = _find_role_object(collection, "_FireMaze_Edit_Helper")
    if helper_obj:
        # Delete old helper to rebuild it from the updated cell data
        mesh = helper_obj.data
        bpy.data.objects.remove(helper_obj, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
            
    # Rebuild helper fully
    build_maze_objects_impl(props, maze_data, context, collection=collection, force_simple=True, name_suffix="_EditHelper")

