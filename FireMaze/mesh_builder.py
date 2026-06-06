"""BMesh-based maze geometry construction and post-processing for FireMaze.

Builds floor, wall, roof, stair, guide path, collider and edit-helper
meshes for both rectangular and polar grids. Includes vertex painting,
lightmap UV generation, coplanar optimisation, and prop/decor spawning.
"""

import math
import random as _real_random
random = _real_random.Random()
import bpy
import bmesh
import logging
import threading
from collections import deque
from contextlib import contextmanager
from mathutils import Matrix, Vector
from .utils import is_valid_ref, _resolve_cells_3d, _get_stair_footprint_coords

logger = logging.getLogger(__name__)


def _build_stair_top_bottom_sets(maze_data):
    """Return bottom_set (stair start level) and top_set (landing level) sets of (z, y, x).

    Args:
        maze_data: MazeData instance.

    Returns:
        Tuple of (bottom_set, top_set) where each is a set of (z, y, x) tuples.
    """
    bottom_set = set()
    top_set = set()
    is_polar = (maze_data.grid_type == 'polar')
    rings = maze_data.polar_rings if is_polar else 0
    for s in maze_data.stairs:
        z = s.get('z', 0)
        sx, sy = s.get('x', 0), s.get('y', 0)
        if is_polar:
            stheta, sr = sx, sy
            if sy >= rings and sx < rings:
                stheta, sr = sy, sx
            bottom_set.add((z, sr, stheta))
            top_set.add((z + 1, sr, stheta))
        else:
            fp = s.get('footprint', '1x1')
            orient = s.get('orientation', 'N')
            coords = _get_stair_footprint_coords(sx, sy, fp, orient)
            for cx, cy in coords:
                bottom_set.add((z, cy, cx))
                top_set.add((z + 1, cy, cx))
    return bottom_set, top_set


_local_cache = threading.local()


@contextmanager
def _bmesh_cache_context():
    """Context manager for BMesh cache lifecycle."""
    global _local_cache
    _local_cache.cache = {}
    try:
        yield
    finally:
        cache = getattr(_local_cache, 'cache', None)
        if cache:
            for bm in cache.values():
                try:
                    bm.free()
                except Exception as e:
                    logger.warning(f"Failed to free BMesh: {e}")
        _local_cache.cache = None


def _create_bmesh_element(element_type: str, materials_dict: dict):
    """Create and initialize a BMesh for floor/wall/roof/stairs.

    Args:
        element_type: 'floor', 'wall', 'roof', or 'stairs'
        materials_dict: Dict mapping element type to material

    Returns:
        (bmesh, uv_layer, materials_list)
    """
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")
    mat_list = [materials_dict.get(element_type)]
    return bm, uv_layer, mat_list


def _prepare_maze_building_context(props, maze_data, context, collection, force_simple):
    """Consolidate rectangular and polar builder setup code.

    Returns a dict containing all configuration, materials, collections, matrices, and stairs lists.
    """
    ts = props.tile_size
    tiled = props.wall_height_tiled
    tiles_high = props.wall_height_tiles if tiled else 1
    wh = ts * tiles_high if tiled else props.wall_height
    seg_h = ts if tiled else wh
    wt = props.tile_size if props.wall_mode == 'cube' else props.wall_thickness

    if force_simple:
        custom_floor = None
        custom_wall = None
        custom_roof = None
        custom_stair_mesh = None
        custom_ramp_mesh = None
        centered = props.tiles_centered
    else:
        custom_floor = props.custom_floor_mesh if is_valid_ref(props.custom_floor_mesh) else None
        custom_wall = props.custom_wall_mesh if is_valid_ref(props.custom_wall_mesh) else None
        custom_roof = props.custom_roof_mesh if is_valid_ref(props.custom_roof_mesh) else None
        custom_stair_mesh = props.custom_stair_mesh if is_valid_ref(props.custom_stair_mesh) else None
        custom_ramp_mesh = props.custom_ramp_mesh if is_valid_ref(props.custom_ramp_mesh) else None
        centered = props.tiles_centered

    mat_floor_offset = _get_offset_matrix(props.floor_translate, props.floor_rotate, props.floor_scale)
    mat_wall_offset = _get_offset_matrix(props.wall_translate, props.wall_rotate, props.wall_scale)
    mat_roof_offset = _get_offset_matrix(props.roof_translate, props.roof_rotate, props.roof_scale)

    wall_meshes_list = []
    if not force_simple and is_valid_ref(props.custom_wall_collection):
        wall_meshes_list = [obj.data for obj in props.custom_wall_collection.objects if obj.type == 'MESH' and obj.data]
    floor_meshes_list = []
    if not force_simple and is_valid_ref(props.custom_floor_collection):
        floor_meshes_list = [obj.data for obj in props.custom_floor_collection.objects if obj.type == 'MESH' and obj.data]
    roof_meshes_list = []
    if not force_simple and is_valid_ref(props.custom_roof_collection):
        roof_meshes_list = [obj.data for obj in props.custom_roof_collection.objects if obj.type == 'MESH' and obj.data]

    if collection is None:
        col = bpy.data.collections.new("FireMaze")
        context.scene.collection.children.link(col)
    else:
        col = collection
        if col.name not in context.scene.collection.children:
            context.scene.collection.children.link(col)

    materials = _ensure_materials()
    cells_3d_orig, floors = _resolve_cells_3d(maze_data)
    import copy
    cells_3d = copy.deepcopy(cells_3d_orig)
    z_range = range(props.edit_floor_level, props.edit_floor_level + 1) if props.is_editing else range(floors)
    stair_bottom_cells, stair_top_cells = _build_stair_top_bottom_sets(maze_data)
    stair_cells = stair_bottom_cells | stair_top_cells

    if props.wall_mode == 'cube':
        # Apply non-active floor overrides for entrance/exits
        if maze_data.entrance:
            en_val = maze_data.entrance
            if maze_data.grid_type == 'polar':
                en_r, en_theta = en_val[0], en_val[1]
                for z in range(1, floors):
                    if (z, en_r, en_theta) not in stair_cells:
                        if 0 <= en_r < len(cells_3d[z]) and 0 <= en_theta < len(cells_3d[z][en_r]):
                            cells_3d[z][en_r][en_theta][0] = True
            else:
                en_x, en_y = en_val[0], en_val[1]
                for z in range(1, floors):
                    if (z, en_y, en_x) not in stair_cells:
                        if 0 <= en_y < len(cells_3d[z]) and 0 <= en_x < len(cells_3d[z][en_y]):
                            cells_3d[z][en_y][en_x][0] = True

        if maze_data.exits:
            for ex_val in maze_data.exits:
                if maze_data.grid_type == 'polar':
                    ex_r, ex_theta = ex_val[0], ex_val[1]
                    for z in range(floors - 1):
                        if (z, ex_r, ex_theta) not in stair_cells:
                            if 0 <= ex_r < len(cells_3d[z]) and 0 <= ex_theta < len(cells_3d[z][ex_r]):
                                cells_3d[z][ex_r][ex_theta][0] = True
                else:
                    ex_x, ex_y = ex_val[0], ex_val[1]
                    for z in range(floors - 1):
                        if (z, ex_y, ex_x) not in stair_cells:
                            if 0 <= ex_y < len(cells_3d[z]) and 0 <= ex_x < len(cells_3d[z][ex_y]):
                                cells_3d[z][ex_y][ex_x][0] = True


    return {
        'ts': ts,
        'tiled': tiled,
        'tiles_high': tiles_high,
        'wh': wh,
        'seg_h': seg_h,
        'wt': wt,
        'custom_floor': custom_floor,
        'custom_wall': custom_wall,
        'custom_roof': custom_roof,
        'custom_stair_mesh': custom_stair_mesh,
        'custom_ramp_mesh': custom_ramp_mesh,
        'centered': centered,
        'mat_floor_offset': mat_floor_offset,
        'mat_wall_offset': mat_wall_offset,
        'mat_roof_offset': mat_roof_offset,
        'wall_meshes_list': wall_meshes_list,
        'floor_meshes_list': floor_meshes_list,
        'roof_meshes_list': roof_meshes_list,
        'col': col,
        'materials': materials,
        'cells_3d': cells_3d,
        'floors': floors,
        'z_range': z_range,
        'stair_bottom_cells': stair_bottom_cells,
        'stair_top_cells': stair_top_cells,
        'stair_cells': stair_cells,
    }



def _get_wall_segments(maze_data, cells=None):
    """Collect unique wall segment positions from thin-mode cell data.

    Args:
        maze_data: MazeData instance.
        cells: Optional 2D cell list (uses maze_data.cells if None).

    Returns:
        Set of (type, a, b) tuples where type is 'H' (horizontal) or 'V' (vertical).
    """
    segments = set()
    if cells is None:
        cells = maze_data.cells
    for y in range(maze_data.depth):
        for x in range(maze_data.width):
            c = cells[y][x]
            if c[0]:
                segments.add(('H', x, y + 1))
            if c[1]:
                segments.add(('H', x, y))
            if c[2]:
                segments.add(('V', x + 1, y))
            if c[3]:
                segments.add(('V', x, y))
    return segments

def _get_offset_matrix(translate, rotate, scale):
    """Build a 4x4 transform matrix from translate, rotate (degrees), scale vectors."""
    mat_t = Matrix.Translation(Vector(translate))
    rx = math.radians(rotate[0])
    ry = math.radians(rotate[1])
    rz = math.radians(rotate[2])
    mat_rx = Matrix.Rotation(rx, 4, 'X')
    mat_ry = Matrix.Rotation(ry, 4, 'Y')
    mat_rz = Matrix.Rotation(rz, 4, 'Z')
    mat_r = mat_rz @ mat_ry @ mat_rx
    mat_s = Matrix.Identity(4)
    mat_s[0][0] = scale[0]
    mat_s[1][1] = scale[1]
    mat_s[2][2] = scale[2]
    return mat_t @ mat_r @ mat_s

def _add_wall_face_transformed(bm, uv_layer, cx, cy, ts, wh, direction, mat_offset, z_base=0):
    """Add a single wall quad face to a BMesh at a grid-line position.

    Args:
        bm: Target BMesh.
        uv_layer: UV map layer.
        cx: World X of the cell corner.
        cy: World Y of the cell corner.
        ts: Tile size.
        wh: Wall height.
        direction: '+Y', '-Y', '+X', or '-X'.
        mat_offset: Additional transform matrix.
        z_base: Base Z offset.
    """
    ccx = cx + ts / 2
    ccy = cy + ts / 2
    ccz = z_base + wh / 2
    T = Matrix.Translation(Vector((ccx, ccy, ccz))) @ mat_offset

    t2 = ts / 2
    w2 = wh / 2

    if direction == '+Y':
        pts = [(-t2, t2, -w2), (-t2, t2, w2), (t2, t2, w2), (t2, t2, -w2)]
        uvs = [(0, 0), (0, 1), (1, 1), (1, 0)]
    elif direction == '-Y':
        pts = [(-t2, -t2, -w2), (t2, -t2, -w2), (t2, -t2, w2), (-t2, -t2, w2)]
        uvs = [(0, 0), (1, 0), (1, 1), (0, 1)]
    elif direction == '+X':
        pts = [(t2, -t2, -w2), (t2, t2, -w2), (t2, t2, w2), (t2, -t2, w2)]
        uvs = [(0, 0), (1, 0), (1, 1), (0, 1)]
    elif direction == '-X':
        pts = [(-t2, -t2, -w2), (-t2, -t2, w2), (-t2, t2, w2), (-t2, t2, -w2)]
        uvs = [(0, 0), (0, 1), (1, 1), (1, 0)]

    verts = [bm.verts.new(T @ Vector(p)) for p in pts]
    face = bm.faces.new(verts)

    for loop, uv in zip(face.loops, uvs):
        loop[uv_layer].uv = uv

def _merge_bmesh_geometries(src_bm, dst_bm):
    """Transfer all geometry from src_bm into dst_bm via a temporary mesh datablock."""
    temp_mesh = bpy.data.meshes.new("temp_merge_mesh")
    try:
        src_bm.to_mesh(temp_mesh)
        dst_bm.from_mesh(temp_mesh)
    finally:
        bpy.data.meshes.remove(temp_mesh)


def _get_bmesh_from_cache(src_mesh):
    """Retrieve a copy of a BMesh from cache, or create and cache it if not present."""
    cache = _get_bmesh_cache()
    if cache is not None and src_mesh in cache:
        return cache[src_mesh].copy()
    
    temp_bm = bmesh.new()
    temp_bm.from_mesh(src_mesh)
    if cache is not None:
        cache[src_mesh] = temp_bm.copy()
    return temp_bm


def _get_bmesh_cache():
    """Return the appropriate BMesh cache (parameter or global module cache)."""
    global _local_cache
    return getattr(_local_cache, 'cache', None)


def _add_mesh_at(bm, src_mesh, matrix, uv_layer, final_materials_list=None):
    """Transform a source mesh and merge it into the target BMesh.

    Args:
        bm: Target BMesh.
        src_mesh: Source Mesh datablock.
        matrix: 4x4 transform to apply.
        uv_layer: UV map layer on the target BMesh.
        final_materials_list: Accumulator list for material deduplication.
    """
    # Map the source mesh's materials to the final combined materials list
    material_map = []
    if final_materials_list is not None and src_mesh:
        for mat in src_mesh.materials:
            if mat:
                if mat not in final_materials_list:
                    final_materials_list.append(mat)
                material_map.append(final_materials_list.index(mat))
            else:
                material_map.append(0)

    temp_bm = _get_bmesh_from_cache(src_mesh)

    bmesh.ops.transform(temp_bm, matrix=matrix, verts=temp_bm.verts)

    if final_materials_list is not None and material_map:
        for f in temp_bm.faces:
            if f.material_index < len(material_map):
                f.material_index = material_map[f.material_index]
            else:
                f.material_index = 0

    _merge_bmesh_geometries(temp_bm, bm)
    temp_bm.free()

def _add_floor_tile_transformed(bm, uv_layer, x, y, ts, mat_offset, z_offset=0.0):
    """Add a single quad floor tile at cell (x, y) with optional transform and Z offset."""
    cx = x * ts + ts / 2
    cy = y * ts + ts / 2
    T = Matrix.Translation(Vector((cx, cy, z_offset))) @ mat_offset

    t2 = ts / 2
    pts = [(-t2, -t2, 0.0), (t2, -t2, 0.0), (t2, t2, 0.0), (-t2, t2, 0.0)]
    verts = [bm.verts.new(T @ Vector(p)) for p in pts]
    face = bm.faces.new(verts)

    uvs = [(0, 0), (1, 0), (1, 1), (0, 1)]
    for loop, uv in zip(face.loops, uvs):
        loop[uv_layer].uv = uv




def _add_horizontal_roof_face_transformed(bm, uv_layer, x, y, ts, wh, wt, mat_offset):
    """Add a horizontal roof cap quad along a thin wall segment at grid-line (x, y)."""
    xc = x * ts + ts / 2
    yc = y * ts
    T = Matrix.Translation(Vector((xc, yc, wh))) @ mat_offset
    tw = wt / 2
    t2 = ts / 2
    v0 = T @ Vector((-t2, -tw, 0))
    v1 = T @ Vector((t2, -tw, 0))
    v2 = T @ Vector((t2, tw, 0))
    v3 = T @ Vector((-t2, tw, 0))
    face = bm.faces.new([bm.verts.new(v0), bm.verts.new(v1), bm.verts.new(v2), bm.verts.new(v3)])

    for loop, uv in zip(face.loops, [(0, 0), (ts, 0), (ts, wt), (0, wt)]):
        loop[uv_layer].uv = uv




def _add_vertical_roof_face_transformed(bm, uv_layer, x, y, ts, wh, wt, mat_offset, trim_south=False, trim_north=False):
    """Add a vertical roof cap face with optional trimming at T-junctions."""
    xc = x * ts
    yc = y * ts + ts / 2
    T = Matrix.Translation(Vector((xc, yc, wh))) @ mat_offset
    tw = wt / 2
    t2 = ts / 2
    y0 = -t2 + (tw if trim_south else 0)
    y1 = t2 - (tw if trim_north else 0)
    dy = y1 - y0

    v0 = T @ Vector((-tw, y0, 0))
    v1 = T @ Vector((tw, y0, 0))
    v2 = T @ Vector((tw, y1, 0))
    v3 = T @ Vector((-tw, y1, 0))
    face = bm.faces.new([bm.verts.new(v0), bm.verts.new(v1), bm.verts.new(v2), bm.verts.new(v3)])
    for loop, uv in zip(face.loops, [(0, 0), (wt, 0), (wt, dy), (0, dy)]):
        loop[uv_layer].uv = uv

def _add_vertical_roof_filler_transformed(bm, uv_layer, xc, yc, wh, tw, y_lo_rel, y_hi_rel, hx0_rel, hx1_rel, mat_offset):
    """Fill a roof gap at a wall-end with a procedurally sized quad."""
    T = Matrix.Translation(Vector((xc, yc, wh))) @ mat_offset
    v0 = T @ Vector((hx0_rel, y_lo_rel, 0))
    v1 = T @ Vector((hx1_rel, y_lo_rel, 0))
    v2 = T @ Vector((hx1_rel, y_hi_rel, 0))
    v3 = T @ Vector((hx0_rel, y_hi_rel, 0))
    f = bm.faces.new([bm.verts.new(v0), bm.verts.new(v1), bm.verts.new(v2), bm.verts.new(v3)])
    df = y_hi_rel - y_lo_rel
    for loop, uv in zip(f.loops, [(0,0),(tw,0),(tw,df),(0,df)]):
        loop[uv_layer].uv = uv

def _add_cube_roof_face_transformed(bm, uv_layer, cx, cy, sx, sy, sz, mat_offset):
    """Add a single quad roof face centred at (cx, cy) for cube-mode walls."""
    T = Matrix.Translation(Vector((cx, cy, sz))) @ mat_offset
    pts = [(-sx/2, -sy/2, 0.0), (sx/2, -sy/2, 0.0), (sx/2, sy/2, 0.0), (-sx/2, sy/2, 0.0)]
    verts = [bm.verts.new(T @ Vector(p)) for p in pts]
    face = bm.faces.new(verts)

    for loop, uv in zip(face.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop[uv_layer].uv = uv

def _create_object_from_bm(bm, name, collection, material):
    """Convert a BMesh to a Blender mesh object, link to collection, tag as fire_maze."""
    mesh = bpy.data.meshes.new(name)
    bm.normal_update()
    bm.to_mesh(mesh)
    mesh.update()
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    if material:
        obj.data.materials.append(material)
    obj["fire_maze"] = True
    return obj

def _build_guide_path(props, maze_data, collection, materials):
    """Build a guide path curve, tube or ribbon from the BFS shortest path data."""
    if not props.generate_guide or not maze_data.guide_path:
        return None

    ts = props.tile_size
    tiled = props.wall_height_tiled
    tiles_high = props.wall_height_tiles if tiled else 1
    wh = ts * tiles_high if tiled else props.wall_height
    ho = props.guide_height_offset
    amp = props.guide_wave_amplitude
    freq = props.guide_wave_frequency

    curve_data = bpy.data.curves.new("FireMaze_Guide", type='CURVE')
    curve_data.dimensions = '3D'

    spline = curve_data.splines.new(type='POLY')
    spline.points.add(len(maze_data.guide_path) - 1)

    for i, coord in enumerate(maze_data.guide_path):
        if maze_data.grid_type == 'polar':
            if len(coord) == 3:
                z_coord, r, theta = coord
            else:
                r, theta = coord
                z_coord = 0
            if r == 0:
                px, py = 0.0, 0.0
            else:
                r_mid = r * ts
                Nr = maze_data.ring_sectors[r]
                alpha_r = 2 * math.pi / Nr
                theta_mid = (theta + 0.5) * alpha_r
                px = r_mid * math.cos(theta_mid)
                py = r_mid * math.sin(theta_mid)
        else:
            if len(coord) == 3:
                z_coord, y, x = coord
            else:
                x, y = coord
                z_coord = 0
            px = x * ts + ts / 2
            py = y * ts + ts / 2

        pz = ho + z_coord * wh
        if amp > 0:
            pz += amp * math.sin(freq * i)
        spline.points[i].co = (px, py, pz, 1.0)


    if props.guide_type == 'tube':
        curve_data.bevel_depth = props.guide_width / 2
        curve_data.bevel_resolution = 4
        curve_data.fill_mode = 'FULL'
    elif props.guide_type == 'ribbon':
        curve_data.extrude = props.guide_width / 2
        curve_data.fill_mode = 'FULL'
    else:  # curve
        curve_data.bevel_depth = 0.005
        curve_data.fill_mode = 'FULL'

    obj = bpy.data.objects.new("FireMaze_Guide", curve_data)
    collection.objects.link(obj)

    if "guide" in materials:
        obj.data.materials.append(materials["guide"])

    obj["fire_maze"] = True
    return obj

def _remove_doubles_on_obj(obj):
    """Remove duplicate vertices on a mesh object in-place."""
    if not obj or obj.type != 'MESH':
        return
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    if not bm.verts:
        bm.free()
        return
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)
    bm.normal_update()
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()

def _safe_remove_doubles(bm, dist=0.001):
    """Remove degenerate faces and duplicate vertices from a BMesh."""
    if not bm.verts:
        return
    degenerate = []
    for f in bm.faces:
        if len(f.verts) < 3:
            degenerate.append(f)
            continue
        v0 = f.verts[0].co
        v1 = f.verts[1].co
        v2 = f.verts[2].co
        area = (v1 - v0).cross(v2 - v0).length
        if area < 1e-8:
            degenerate.append(f)
    for f in degenerate:
        bm.faces.remove(f)
    bm.normal_update()
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)

def _generate_lightmap_on_obj(obj, context, method='smart'):
    """Generate a second 'Lightmap' UV map on a mesh object via smart project or lightmap pack."""
    if not obj or obj.type != 'MESH':
        return
        
    # Store currently active object, selection, and mode
    original_active = context.view_layer.objects.active
    original_selected = list(context.selected_objects)
    original_mode = context.object.mode if context.object else 'OBJECT'
    
    # Make sure we are in object mode before adding UV map
    if original_mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception as e:
            logger.debug(f"Failed to switch to Object mode: {e}")
            
    # Add a new UV map named 'Lightmap'
    uv_map = obj.data.uv_layers.get("Lightmap")
    if not uv_map:
        uv_map = obj.data.uv_layers.new(name="Lightmap")
    
    # Set the new UV map as active for unwrapping
    original_active_uv = obj.data.uv_layers.active
    obj.data.uv_layers.active = uv_map
    
    # Select only this object and make it active
    bpy.ops.object.select_all(action='DESELECT')
    try:
        obj.select_set(True)
    except RuntimeError:
        # Object may not be in the active ViewLayer; try linking it
        try:
            context.view_layer.layer_collection.collection.objects.link(obj)
            obj.select_set(True)
        except Exception as e:
            logger.debug(f"Failed to link or select object: {e}")
            return
    context.view_layer.objects.active = obj
    
    try:
        # Switch to Edit Mode
        bpy.ops.object.mode_set(mode='EDIT')
        # Select all geometry
        bpy.ops.mesh.select_all(action='SELECT')
        # Perform unwrap based on selected method
        if method == 'pack':
            try:
                bpy.ops.uv.lightmap_pack(PREF_CONTEXT='SEL_FACES', PREF_PACK_IN_ONE=True, PREF_NEW_UVLAYER=False)
            except Exception as e:
                logger.debug(f"Lightmap pack with custom args failed, using defaults: {e}")
                bpy.ops.uv.lightmap_pack()
        else:
            # Perform Smart UV Project (angle_limit 66, island_margin 0.02)
            bpy.ops.uv.smart_project(angle_limit=66.0, island_margin=0.02)
        # Switch back to Object Mode
        bpy.ops.object.mode_set(mode='OBJECT')
    except Exception as e:
        logger.error(f"Failed to unwrap lightmap for {obj.name}: {e}")
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception as cleanup_err:
            logger.debug(f"Failed to restore Object mode after lightmap error: {cleanup_err}")
            
    # Restore original active UV map
    if original_active_uv:
        obj.data.uv_layers.active = original_active_uv
        
    # Restore original active object and selection
    bpy.ops.object.select_all(action='DESELECT')
    for o in original_selected:
        try:
            o.select_set(True)
        except Exception as e:
            logger.debug(f"Failed to restore object selection: {e}")
    context.view_layer.objects.active = original_active

    if original_active:
        try:
            bpy.ops.object.mode_set(mode=original_mode)
        except Exception as e:
            logger.debug(f"Failed to restore original mode: {e}")

def _merge_maze_objects(objects, context, name="FireMaze_Merged"):
    """Join a list of mesh objects into a single mesh object using the join operator."""
    objects = [obj for obj in objects if obj is not None]
    if len(objects) == 0:
        return None
    if len(objects) == 1:
        objects[0].name = name
        return objects[0]

    try:
        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode='OBJECT')

        bpy.ops.object.select_all(action='DESELECT')
        for obj in objects:
            try:
                obj.select_set(True)
            except Exception as e:
                logger.debug(f"Failed to select object for merging: {e}")

        context.view_layer.objects.active = objects[0]
        bpy.ops.object.join()
        
        merged_obj = context.view_layer.objects.active
        merged_obj.name = name
        return merged_obj
    except Exception as e:
        logger.error(f"Object merging failed: {e}")
        return objects[0]

def _optimize_coplanar_on_obj(obj):
    """Dissolve coplanar faces on a mesh object to reduce polygon count."""
    if not obj or obj.type != 'MESH':
        return
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    # Merge double vertices first so adjacent faces share edges and vertices
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)
    # Perform limited dissolve to simplify coplanar geometry
    bmesh.ops.dissolve_limit(
        bm,
        angle_limit=math.radians(0.5),
        verts=bm.verts,
        edges=bm.edges
    )
    bm.normal_update()
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()

def _compute_grid_distances(maze_data, wall_mode):
    """BFS distance from entrance to every reachable cell (used for distance-gradient vertex paint)."""
    cells_3d, floors = _resolve_cells_3d(maze_data.cells)
    
    if maze_data.grid_type == 'polar':
        start_r = maze_data.entrance[0]
        start_theta = maze_data.entrance[1]
        start_z = 0
        
        distances = {}
        queue = deque([(start_z, start_r, start_theta, 0)])
        distances[(start_z, start_r, start_theta)] = 0
        
        rings = maze_data.polar_rings
        ring_sectors = maze_data.ring_sectors
        
        # Build stair edge map for polar 3D traversal
        stair_up = {}
        stair_down = {}
        for s in maze_data.stairs:
            sz = s['z']
            sx, sy = s['x'], s['y']
            stheta, sr = sx, sy
            if sy >= rings and sx < rings:
                stheta, sr = sy, sx
            key_up = (sz, sr, stheta)
            key_down = (sz + 1, sr, stheta)
            stair_up[key_up] = (sz + 1, sr, stheta)
            stair_down[key_down] = (sz, sr, stheta)
            
        while queue:
            cz, r, theta, d = queue.popleft()
            Nr = ring_sectors[r]
            
            accessible = []
            # Neighbors in current floor cz
            if r >= 1 and not cells_3d[cz][r][theta][0]:
                accessible.append((cz, r, (theta + 1) % Nr))
            if r >= 1 and not cells_3d[cz][r][(theta - 1) % Nr][0]:
                accessible.append((cz, r, (theta - 1) % Nr))
            if r > 0 and not cells_3d[cz][r][theta][1]:
                N_in = ring_sectors[r - 1]
                if N_in == Nr:
                    accessible.append((cz, r - 1, theta))
                elif N_in == 1:
                    accessible.append((cz, r - 1, 0))
                else:
                    accessible.append((cz, r - 1, theta // 2))
            if r < rings - 1:
                N_out = ring_sectors[r + 1]
                if N_out == Nr:
                    if not cells_3d[cz][r + 1][theta][1]:
                        accessible.append((cz, r + 1, theta))
                elif Nr == 1:
                    for t in range(N_out):
                        if not cells_3d[cz][r + 1][t][1]:
                            accessible.append((cz, r + 1, t))
                else:
                    if not cells_3d[cz][r + 1][2 * theta][1]:
                        accessible.append((cz, r + 1, 2 * theta))
                    if not cells_3d[cz][r + 1][2 * theta + 1][1]:
                        accessible.append((cz, r + 1, 2 * theta + 1))
                        
            # Stair connections (cz + 1 and cz - 1)
            node = (cz, r, theta)
            if node in stair_up:
                accessible.append(stair_up[node])
            if node in stair_down:
                accessible.append(stair_down[node])
                
            for nz, nr, ntheta in accessible:
                nn = (nz, nr, ntheta)
                if nn not in distances:
                    distances[nn] = d + 1
                    queue.append((nz, nr, ntheta, d + 1))
        return distances

    width = maze_data.width
    depth = maze_data.depth
    start_x = maze_data.entrance[0]
    start_y = maze_data.entrance[1]
    start_z = 0
    
    distances = {}
    queue = deque([(start_z, start_y, start_x, 0)])
    distances[(start_z, start_y, start_x)] = 0
    
    # Build stair edge map for rectangular 3D traversal
    stair_up = {}
    stair_down = {}
    for s in maze_data.stairs:
        sz = s['z']
        sx, sy = s['x'], s['y']
        fp = s.get('footprint', '1x1')
        orient = s.get('orientation', 'N')
        coords = _get_stair_footprint_coords(sx, sy, fp, orient)
        for cx, cy in coords:
            if 0 <= cy < depth and 0 <= cx < width:
                key_up = (sz, cy, cx)
                key_down = (sz + 1, cy, cx)
                stair_up[key_up] = (sz + 1, cy, cx)
                stair_down[key_down] = (sz, cy, cx)
                
    while queue:
        cz, cy, cx, d = queue.popleft()
        
        # Determine neighbors in current floor cz
        neighbors = []
        if wall_mode == 'thin':
            c = cells_3d[cz][cy][cx]
            # North
            if not c[0] and cy + 1 < depth:
                neighbors.append((cz, cy + 1, cx))
            # South
            if not c[1] and cy - 1 >= 0:
                neighbors.append((cz, cy - 1, cx))
            # East
            if not c[2] and cx + 1 < width:
                neighbors.append((cz, cy, cx + 1))
            # West
            if not c[3] and cx - 1 >= 0:
                neighbors.append((cz, cy, cx - 1))
        else: # cube
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < width and 0 <= ny < depth:
                    if not cells_3d[cz][ny][nx][0]:
                        neighbors.append((cz, ny, nx))
                        
        # Stair connections
        node = (cz, cy, cx)
        if node in stair_up:
            neighbors.append(stair_up[node])
        if node in stair_down:
            neighbors.append(stair_down[node])
            
        for nz, ny, nx in neighbors:
            nn = (nz, ny, nx)
            if nn not in distances:
                distances[nn] = d + 1
                queue.append((nz, ny, nx, d + 1))
                
    return distances

def _apply_vertex_painting_on_obj(obj, props, maze_data):
    """Paint vertex colors on a mesh object based on the selected mode (AO, blend, path, distance)."""
    if not obj or obj.type != 'MESH':
        return
        
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    
    # Access or create loop float color layer (RGBA)
    color_layer = bm.loops.layers.float_color.get("Color")
    if not color_layer:
        color_layer = bm.loops.layers.float_color.new("Color")
        
    mode = props.vertex_paint_mode
    intensity = props.vertex_paint_intensity
    ts = props.tile_size
    wall_mode = props.wall_mode
    
    tiled = props.wall_height_tiled
    tiles_high = props.wall_height_tiles if tiled else 1
    wh = ts * tiles_high if tiled else props.wall_height
    if wh <= 0:
        wh = 2.0
        
    # Pre-calculate bounding box heights for relative height gradients
    coords_z = [v.co.z for v in bm.verts]
    z_min = min(coords_z) if coords_z else 0.0
    z_max = max(coords_z) if coords_z else 1.0
    z_range = z_max - z_min
    if z_range < 0.001:
        z_range = 1.0
        
    # Precompute distances if doing distance mode
    if mode == 'distance':
        distances = _compute_grid_distances(maze_data, wall_mode)
        max_d = max(distances.values()) if distances else 1
        if max_d == 0:
            max_d = 1
            
    # Identify dead ends (3D)
    dead_ends = set()
    if mode == 'blend':
        cells_3d, floors = _resolve_cells_3d(maze_data.cells)
        if maze_data.grid_type == 'polar':
            rings = maze_data.polar_rings
            ring_sectors = maze_data.ring_sectors
            for z in range(floors):
                for r in range(rings):
                    for theta in range(ring_sectors[r]):
                        Nr = ring_sectors[r]
                        accessible_count = 0
                        if r >= 1 and not cells_3d[z][r][theta][0]:
                            accessible_count += 1
                        if r >= 1 and not cells_3d[z][r][(theta - 1) % Nr][0]:
                            accessible_count += 1
                        if r > 0 and not cells_3d[z][r][theta][1]:
                            N_in = ring_sectors[r - 1]
                            if N_in == Nr:
                                accessible_count += 1
                            elif N_in == 1:
                                accessible_count += 1
                            else:
                                accessible_count += 1
                        if r < rings - 1:
                            N_out = ring_sectors[r + 1]
                            if N_out == Nr:
                                if not cells_3d[z][r + 1][theta][1]:
                                    accessible_count += 1
                            elif Nr == 1:
                                for t in range(N_out):
                                    if not cells_3d[z][r + 1][t][1]:
                                        accessible_count += 1
                            else:
                                if not cells_3d[z][r + 1][2 * theta][1]:
                                    accessible_count += 1
                                if not cells_3d[z][r + 1][2 * theta + 1][1]:
                                    accessible_count += 1
                        if accessible_count == 1:
                            # Exclude entrance and exits (on active floors)
                            is_ent_or_exit = False
                            if z == 0 and maze_data.entrance and (r, theta) == maze_data.entrance[0:2]:
                                    is_ent_or_exit = True
                            if z == floors - 1 and maze_data.exits:
                                for ex_r, ex_theta, _ in maze_data.exits:
                                    if (r, theta) == (ex_r, ex_theta):
                                        is_ent_or_exit = True
                            if not is_ent_or_exit and r > 0:
                                dead_ends.add((z, r, theta))
        else:
            for z in range(floors):
                for cy in range(maze_data.depth):
                    for cx in range(maze_data.width):
                        is_ent_or_exit = False
                        if z == 0 and maze_data.entrance and (cx, cy) == (maze_data.entrance[0], maze_data.entrance[1]):
                            is_ent_or_exit = True
                        if z == floors - 1 and maze_data.exits:
                            for ex_val in maze_data.exits:
                                if (cx, cy) == (ex_val[0], ex_val[1]):
                                    is_ent_or_exit = True
                        if is_ent_or_exit:
                            continue

                        if wall_mode == 'thin':
                            c = cells_3d[z][cy][cx]
                            if sum(c[:4]) == 3:
                                dead_ends.add((z, cy, cx))
                        else: # cube
                            if not cells_3d[z][cy][cx][0]:
                                open_neighbors = 0
                                for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                                    nx, ny = cx + dx, cy + dy
                                    if 0 <= nx < maze_data.width and 0 <= ny < maze_data.depth:
                                        if not cells_3d[z][ny][nx][0]:
                                            open_neighbors += 1
                                if open_neighbors == 1:
                                    dead_ends.add((z, cy, cx))

    # Pre-calculate guide path cell set for faster lookup in path mode
    guide_cells = set(maze_data.guide_path) if maze_data.guide_path else set()
    guide_world_coords = []
    if mode == 'path' and guide_cells:
        for coord in guide_cells:
            # Handles both 2D and 3D paths
            if len(coord) == 3:
                gz_c, gr, gtheta = coord
            else:
                gz_c, gr, gtheta = 0, coord[0], coord[1]
            if maze_data.grid_type == 'polar':
                if gr == 0:
                    gcx_world, gcy_world = 0.0, 0.0
                else:
                    gr_mid = gr * ts
                    gNr = maze_data.ring_sectors[gr]
                    galpha = 2 * math.pi / gNr
                    gtheta_mid = (gtheta + 0.5) * galpha
                    gcx_world = gr_mid * math.cos(gtheta_mid)
                    gcy_world = gr_mid * math.sin(gtheta_mid)
            else:
                # rect coordinate (last two values)
                gcx, gcy = coord[-2], coord[-1]
                gcx_world = gcx * ts + ts / 2
                gcy_world = gcy * ts + ts / 2
            guide_world_coords.append((gz_c, gcx_world, gcy_world))

    for face in bm.faces:
        for loop in face.loops:
            co = loop.vert.co
            px, py, pz = co.x, co.y, co.z
            h_rel = (pz - z_min) / z_range
            
            # Map vertex to floor level z
            z = max(0, min(maze_data.floors - 1, int(pz / wh)))
            
            # Map vertex to cell
            if maze_data.grid_type == 'polar':
                R = math.sqrt(px**2 + py**2)
                phi = math.atan2(py, px) % (2 * math.pi)
                r = int(R / ts + 0.5)
                r = max(0, min(maze_data.polar_rings - 1, r))
                Nr = maze_data.ring_sectors[r]
                alpha_r = 2 * math.pi / Nr
                theta = int(phi / alpha_r)
                theta = max(0, min(Nr - 1, theta))
                cx, cy = r, theta
            else:
                cx = max(0, min(maze_data.width - 1, int(px // ts)))
                cy = max(0, min(maze_data.depth - 1, int(py // ts)))
            
            r_col, g_col, b_col, a_col = 1.0, 1.0, 1.0, 1.0
            
            if mode == 'ao':
                # Floor and roof proximity
                seam_w = 0.15 * ts
                f_floor = max(0.0, 1.0 - (pz - z_min) / seam_w)
                f_roof = max(0.0, 1.0 - (z_max - pz) / seam_w)
                
                # Corner distance
                if maze_data.grid_type == 'polar':
                    R = math.sqrt(px**2 + py**2)
                    phi = math.atan2(py, px) % (2 * math.pi)
                    R_in = (cx - 0.5) * ts
                    R_out = (cx + 0.5) * ts
                    theta_start = cy * alpha_r
                    theta_end = (cy + 1) * alpha_r
                    
                    d_corner = float('inf')
                    for r_boundary in [R_in, R_out]:
                        for theta_boundary in [theta_start, theta_end]:
                            d_phi = (phi - theta_boundary) % (2 * math.pi)
                            if d_phi > math.pi:
                                d_phi = 2 * math.pi - d_phi
                            d = math.sqrt((r_boundary - R)**2 + (R * d_phi)**2)
                            if d < d_corner:
                                d_corner = d
                else:
                    rx, ry = px / ts, py / ts
                    gx, gy = round(rx), round(ry)
                    d_corner = math.sqrt((px - gx * ts)**2 + (py - gy * ts)**2)
                    
                f_corner = max(0.0, 1.0 - d_corner / seam_w)
                
                ao = max(f_floor, f_roof, f_corner)
                factor = 1.0 - ao * intensity * 0.7
                r_col, g_col, b_col, a_col = factor, factor, factor, 1.0
                
            elif mode == 'blend':
                # Moss (R): near floor
                r_val = max(0.0, 1.0 - h_rel / 0.25)
                # Cracks (G): near corners/seams
                if maze_data.grid_type == 'polar':
                    R = math.sqrt(px**2 + py**2)
                    phi = math.atan2(py, px) % (2 * math.pi)
                    R_in = (cx - 0.5) * ts
                    R_out = (cx + 0.5) * ts
                    theta_start = cy * alpha_r
                    theta_end = (cy + 1) * alpha_r
                    
                    d_corner = float('inf')
                    for r_boundary in [R_in, R_out]:
                        for theta_boundary in [theta_start, theta_end]:
                            d_phi = (phi - theta_boundary) % (2 * math.pi)
                            if d_phi > math.pi:
                                d_phi = 2 * math.pi - d_phi
                            d = math.sqrt((r_boundary - R)**2 + (R * d_phi)**2)
                            if d < d_corner:
                                d_corner = d
                else:
                    rx, ry = px / ts, py / ts
                    gx, gy = round(rx), round(ry)
                    d_corner = math.sqrt((px - gx * ts)**2 + (py - gy * ts)**2)
                    
                g_val = max(0.0, 1.0 - d_corner / (0.15 * ts))
                # Wetness (B): flat floors
                b_val = 0.0
                if loop.vert.normal.z > 0.9 and h_rel < 0.02:
                    b_val = 1.0
                elif h_rel < 0.05:
                    b_val = max(0.0, 1.0 - h_rel / 0.05)
                # Soot (A): dead ends
                de_key = (z, cy, cx) if maze_data.grid_type != 'polar' else (z, r, theta)
                a_val = 1.0 if de_key in dead_ends else 0.0
                
                r_col = r_val * intensity
                g_col = g_val * intensity
                b_col = b_val * intensity
                a_col = a_val * intensity
                
            elif mode == 'path':
                if not guide_world_coords:
                    r_col, g_col, b_col, a_col = 1.0, 1.0, 1.0, 1.0
                else:
                    min_d = float('inf')
                    for gz_c, gcx_world, gcy_world in guide_world_coords:
                        if gz_c == z:
                            d = math.sqrt((px - gcx_world)**2 + (py - gcy_world)**2)
                            if d < min_d:
                                min_d = d
                    
                    radius = 0.75 * ts
                    f_path = max(0.0, 1.0 - min_d / radius)
                    r_col = 0.0
                    g_col = f_path * intensity
                    b_col = 0.0
                    a_col = 0.0
                    
            elif mode == 'distance':
                d_key = (z, cy, cx) if maze_data.grid_type != 'polar' else (z, r, theta)
                d_val = distances.get(d_key, 0)
                norm_d = d_val / max_d
                val = norm_d * intensity
                r_col, g_col, b_col, a_col = val, val, val, 1.0
                
            loop[color_layer] = (r_col, g_col, b_col, a_col)
            
    bm.normal_update()
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()

def _spawn_decorations(props, maze_data, context, parent_collection):
    """Place torch, chest and door prop objects in the maze based on wall/floor topology and density settings."""
    torch_mesh = props.prop_torch_mesh if is_valid_ref(props.prop_torch_mesh) else None
    chest_mesh = props.prop_chest_mesh if is_valid_ref(props.prop_chest_mesh) else None
    door_mesh = props.prop_door_mesh if is_valid_ref(props.prop_door_mesh) else None

    if not (torch_mesh or chest_mesh or door_mesh):
        return
 
    # Create or get props collection
    props_col = bpy.data.collections.get("FireMaze_Props")
    if not props_col:
        props_col = bpy.data.collections.new("FireMaze_Props")
        parent_collection.children.link(props_col)
 
    ts = props.tile_size
    wh = props.wall_height
    if props.wall_height_tiled:
        wh = ts * props.wall_height_tiles
 
    wall_mode = props.wall_mode
    rng = _real_random.Random(props.seed + 1000 if props.seed else None)
 
    def place_prop(src_obj, pos, rot_z):
        """Copy a source prop object into the maze collection at the given position and rotation."""
        new_obj = src_obj.copy()
        props_col.objects.link(new_obj)
        new_obj.location = Vector(pos)
        new_obj.rotation_euler = Vector((src_obj.rotation_euler.x, src_obj.rotation_euler.y, rot_z))
        new_obj.scale = src_obj.scale
        new_obj["fire_maze"] = True # Mark as fire_maze so it is automatically cleared!

    if maze_data.grid_type == 'polar':
        rings = maze_data.polar_rings
        ring_sectors = maze_data.ring_sectors
        
        # 1. Torches
        if torch_mesh:
            torch_src = torch_mesh
            density = props.prop_torch_density
            offset = 0.02 * ts
            
            for r in range(1, rings):
                Nr = ring_sectors[r]
                alpha_r = 2 * math.pi / Nr
                for theta in range(Nr):
                    cw_wall = maze_data.cells[r][theta][0]
                    in_wall = maze_data.cells[r][theta][1]
                    
                    r_mid = r * ts
                    theta_mid = (theta + 0.5) * alpha_r
                    
                    # Clockwise wall torch
                    if cw_wall and rng.random() < density:
                        phi_cw = (theta + 1) * alpha_r
                        pos_x = r_mid * math.cos(phi_cw) + offset * math.sin(phi_cw)
                        pos_y = r_mid * math.sin(phi_cw) - offset * math.cos(phi_cw)
                        pos = (pos_x, pos_y, 0.6 * wh)
                        place_prop(torch_src, pos, phi_cw - math.pi / 2)
                        
                    # Inward wall torch
                    if in_wall and rng.random() < density:
                        r_in = (r - 0.5) * ts
                        pos_x = (r_in + offset) * math.cos(theta_mid)
                        pos_y = (r_in + offset) * math.sin(theta_mid)
                        pos = (pos_x, pos_y, 0.6 * wh)
                        place_prop(torch_src, pos, theta_mid)
 
                    # Outer boundary torch
                    if r == rings - 1:
                        is_entrance = (theta == 0)
                        is_exit = False
                        if maze_data.exits:
                            for ex_r, ex_theta, _ in maze_data.exits:
                                if ex_r == r and ex_theta == theta:
                                    is_exit = True
                                    break
                        if not is_entrance and not is_exit and rng.random() < density:
                            r_out = (r + 0.5) * ts
                            pos_x = (r_out - offset) * math.cos(theta_mid)
                            pos_y = (r_out - offset) * math.sin(theta_mid)
                            pos = (pos_x, pos_y, 0.6 * wh)
                            place_prop(torch_src, pos, theta_mid + math.pi)
 
        # 2. Chests
        if chest_mesh:
            chest_src = chest_mesh
            density = props.prop_chest_density
            chest_offset = 0.15 * ts
            
            for r in range(rings):
                for theta in range(ring_sectors[r]):
                    Nr = ring_sectors[r]
                    accessible = []
                    
                    if r >= 1 and not maze_data.cells[r][theta][0]:
                        accessible.append(('CW', (r, (theta + 1) % Nr)))
                    if r >= 1 and not maze_data.cells[r][(theta - 1) % Nr][0]:
                        accessible.append(('CCW', (r, (theta - 1) % Nr)))
                    if r > 0 and not maze_data.cells[r][theta][1]:
                        N_in = ring_sectors[r - 1]
                        if N_in == Nr:
                            accessible.append(('IN', (r - 1, theta)))
                        elif N_in == 1:
                            accessible.append(('IN', (r - 1, 0)))
                        else:
                            accessible.append(('IN', (r - 1, theta // 2)))
                    if r < rings - 1:
                        N_out = ring_sectors[r + 1]
                        if N_out == Nr:
                            if not maze_data.cells[r + 1][theta][1]:
                                accessible.append(('OUT', (r + 1, theta)))
                        elif Nr == 1:
                            for t in range(N_out):
                                if not maze_data.cells[r + 1][t][1]:
                                    accessible.append(('OUT', (r + 1, t)))
                        else:
                            if not maze_data.cells[r + 1][2 * theta][1]:
                                accessible.append(('OUT', (r + 1, 2 * theta)))
                            if not maze_data.cells[r + 1][2 * theta + 1][1]:
                                accessible.append(('OUT', (r + 1, 2 * theta + 1)))
                                
                    if len(accessible) == 1 and r > 0:
                        is_ent_or_exit = False
                        if maze_data.entrance and (r, theta) == maze_data.entrance[0:2]:
                            is_ent_or_exit = True
                        if maze_data.exits:
                            for ex_r, ex_theta, _ in maze_data.exits:
                                if (r, theta) == (ex_r, ex_theta):
                                    is_ent_or_exit = True
                        
                        if not is_ent_or_exit and rng.random() < density:
                            direction = accessible[0][0]
                            r_mid = r * ts
                            alpha_r = 2 * math.pi / Nr
                            theta_mid = (theta + 0.5) * alpha_r
                            
                            if direction == 'IN':
                                pos_x = (r_mid + chest_offset) * math.cos(theta_mid)
                                pos_y = (r_mid + chest_offset) * math.sin(theta_mid)
                                place_prop(chest_src, (pos_x, pos_y, 0.0), theta_mid + math.pi)
                            elif direction == 'OUT':
                                pos_x = (r_mid - chest_offset) * math.cos(theta_mid)
                                pos_y = (r_mid - chest_offset) * math.sin(theta_mid)
                                place_prop(chest_src, (pos_x, pos_y, 0.0), theta_mid)
                            elif direction == 'CW':
                                pos_x = r_mid * math.cos(theta_mid - alpha_r / 4)
                                pos_y = r_mid * math.sin(theta_mid - alpha_r / 4)
                                place_prop(chest_src, (pos_x, pos_y, 0.0), theta_mid - math.pi / 2)
                            else: # CCW
                                pos_x = r_mid * math.cos(theta_mid + alpha_r / 4)
                                pos_y = r_mid * math.sin(theta_mid + alpha_r / 4)
                                place_prop(chest_src, (pos_x, pos_y, 0.0), theta_mid + math.pi / 2)

        # 3. Doors
        if door_mesh:
            door_src = door_mesh
            
            if maze_data.entrance:
                er, etheta, eside = maze_data.entrance
                eNr = ring_sectors[er]
                ealpha = 2 * math.pi / eNr
                etheta_mid = (etheta + 0.5) * ealpha
                r_door = (er + 0.5) * ts
                pos_x = r_door * math.cos(etheta_mid)
                pos_y = r_door * math.sin(etheta_mid)
                place_prop(door_src, (pos_x, pos_y, 0.0), etheta_mid)
                
            if maze_data.exits:
                for ex_r, ex_theta, ex_side in maze_data.exits:
                    exNr = ring_sectors[ex_r]
                    exalpha = 2 * math.pi / exNr
                    extheta_mid = (ex_theta + 0.5) * exalpha
                    if ex_side == 'CENTER':
                        r_door = 0.5 * ts
                    else:
                        r_door = (ex_r + 0.5) * ts
                    pos_x = r_door * math.cos(extheta_mid)
                    pos_y = r_door * math.sin(extheta_mid)
                    place_prop(door_src, (pos_x, pos_y, 0.0), extheta_mid)
        return

    # 1. Torches
    if torch_mesh:
        torch_src = torch_mesh
        density = props.prop_torch_density
        offset = 0.02 * ts
        
        for y in range(maze_data.depth):
            for x in range(maze_data.width):
                if wall_mode == 'thin':
                    c = maze_data.cells[y][x]
                    # North wall
                    if c[0] and rng.random() < density:
                        pos = (x * ts + ts/2, (y + 1) * ts - offset, 0.6 * wh)
                        place_prop(torch_src, pos, math.pi)
                    # South wall
                    if c[1] and rng.random() < density:
                        pos = (x * ts + ts/2, y * ts + offset, 0.6 * wh)
                        place_prop(torch_src, pos, 0.0)
                    # East wall
                    if c[2] and rng.random() < density:
                        pos = ((x + 1) * ts - offset, y * ts + ts/2, 0.6 * wh)
                        place_prop(torch_src, pos, math.pi / 2)
                    # West wall
                    if c[3] and rng.random() < density:
                        pos = (x * ts + offset, y * ts + ts/2, 0.6 * wh)
                        place_prop(torch_src, pos, -math.pi / 2)
                else: # cube
                    if maze_data.cells[y][x][0]: # wall cube
                        # North
                        if y + 1 < maze_data.depth and not maze_data.cells[y+1][x][0]:
                            if rng.random() < density:
                                pos = (x * ts + ts/2, (y + 1) * ts + offset, 0.6 * wh)
                                place_prop(torch_src, pos, 0.0)
                        # South
                        if y - 1 >= 0 and not maze_data.cells[y-1][x][0]:
                            if rng.random() < density:
                                pos = (x * ts + ts/2, y * ts - offset, 0.6 * wh)
                                place_prop(torch_src, pos, math.pi)
                        # East
                        if x + 1 < maze_data.width and not maze_data.cells[y][x+1][0]:
                            if rng.random() < density:
                                pos = ((x + 1) * ts + offset, y * ts + ts/2, 0.6 * wh)
                                place_prop(torch_src, pos, math.pi / 2)
                        # West
                        if x - 1 >= 0 and not maze_data.cells[y][x-1][0]:
                            if rng.random() < density:
                                pos = (x * ts - offset, y * ts + ts/2, 0.6 * wh)
                                place_prop(torch_src, pos, -math.pi / 2)

    # 2. Chests (Dead-Ends)
    if chest_mesh:
        chest_src = chest_mesh
        density = props.prop_chest_density
        chest_offset = 0.15 * ts
        
        for y in range(maze_data.depth):
            for x in range(maze_data.width):
                is_dead = False
                open_dir = None
                
                if wall_mode == 'thin':
                    c = maze_data.cells[y][x]
                    if sum(c[:4]) == 3:
                        is_dead = True
                        if not c[0]: open_dir = 'N'
                        elif not c[1]: open_dir = 'S'
                        elif not c[2]: open_dir = 'E'
                        else: open_dir = 'W'
                else: # cube
                    if not maze_data.cells[y][x][0]:
                        neighbors = []
                        for d, dx, dy in [('N', 0, 1), ('S', 0, -1), ('E', 1, 0), ('W', -1, 0)]:
                            nx, ny = x + dx, y + dy
                            if 0 <= nx < maze_data.width and 0 <= ny < maze_data.depth:
                                if not maze_data.cells[ny][nx][0]:
                                    neighbors.append(d)
                        if len(neighbors) == 1:
                            is_dead = True
                            open_dir = neighbors[0]
                            
                if is_dead and rng.random() < density:
                    if open_dir == 'N':
                        pos = (x * ts + ts/2, y * ts + chest_offset, 0.0)
                        place_prop(chest_src, pos, 0.0)
                    elif open_dir == 'S':
                        pos = (x * ts + ts/2, (y + 1) * ts - chest_offset, 0.0)
                        place_prop(chest_src, pos, math.pi)
                    elif open_dir == 'E':
                        pos = (x * ts + chest_offset, y * ts + ts/2, 0.0)
                        place_prop(chest_src, pos, math.pi / 2)
                    elif open_dir == 'W':
                        pos = ((x + 1) * ts - chest_offset, y * ts + ts/2, 0.0)
                        place_prop(chest_src, pos, -math.pi / 2)

    # 3. Doors (Entrance / Exits)
    if door_mesh:
        door_src = door_mesh
        entries = []
        if maze_data.entrance:
            entries.append(maze_data.entrance)
        if maze_data.exits:
            entries.extend(maze_data.exits)
            
        for ex, ey, side in entries:
            if side == 'N':
                pos = (ex * ts + ts/2, (ey + 1) * ts, 0.0)
                place_prop(door_src, pos, 0.0)
            elif side == 'S':
                pos = (ex * ts + ts/2, ey * ts, 0.0)
                place_prop(door_src, pos, 0.0)
            elif side == 'E':
                pos = ((ex + 1) * ts, ey * ts + ts/2, 0.0)
                place_prop(door_src, pos, math.pi / 2)
            elif side == 'W':
                pos = (ex * ts, ey * ts + ts/2, 0.0)
                place_prop(door_src, pos, -math.pi / 2)

def _add_polar_center_fan(bm, uv_layer, ts, z_base, is_roof=False, flip_normal=False):
    """Build a triangulated fan at the polar centre (ring 0) for the floor or roof."""
    radius = 0.5 * ts
    segments = 24
    
    # Create center vertex
    v_center = bm.verts.new((0.0, 0.0, z_base))
    
    # Create outer vertices
    outer_verts = []
    for i in range(segments):
        phi = i * (2 * math.pi / segments)
        x = radius * math.cos(phi)
        y = radius * math.sin(phi)
        outer_verts.append(bm.verts.new((x, y, z_base)))
        
    for i in range(segments):

        v1 = outer_verts[i]
        v2 = outer_verts[(i + 1) % segments]
        
        # Face winding
        if flip_normal:
            f = bm.faces.new([v_center, v2, v1])
        else:
            f = bm.faces.new([v_center, v1, v2])
            
        # UV mapping
        for loop in f.loops:
            co = loop.vert.co
            u = (co.x / ts) + 0.5
            v = (co.y / ts) + 0.5
            loop[uv_layer].uv = (u, v)

def _add_polar_floor_wedge(bm, uv_layer, r, theta, Nr, ts, z_base, is_roof=False, flip_normal=False):
    """Build a subdivided wedge quad for a polar floor or roof tile at ring r, sector theta."""
    R_in = (r - 0.5) * ts
    R_out = (r + 0.5) * ts
    alpha_r = 2 * math.pi / Nr
    phi_start = theta * alpha_r
    
    subdivs = 8
    r_mid = r * ts
    
    for i in range(subdivs):
        phi_1 = phi_start + (i / subdivs) * alpha_r
        phi_2 = phi_start + ((i + 1) / subdivs) * alpha_r
        
        ix1, iy1 = R_in * math.cos(phi_1), R_in * math.sin(phi_1)
        ix2, iy2 = R_in * math.cos(phi_2), R_in * math.sin(phi_2)
        ox1, oy1 = R_out * math.cos(phi_1), R_out * math.sin(phi_1)
        ox2, oy2 = R_out * math.cos(phi_2), R_out * math.sin(phi_2)
        
        vi1 = bm.verts.new((ix1, iy1, z_base))
        vo1 = bm.verts.new((ox1, oy1, z_base))
        vo2 = bm.verts.new((ox2, oy2, z_base))
        vi2 = bm.verts.new((ix2, iy2, z_base))
        
        
        # Floor default: +Z normal (up). Roof default: +Z normal (up, caps walls in cube mode).
        # In thin-wall mode the roof acts as a ceiling, so flip to -Z (down).
        if flip_normal:
            f = bm.faces.new([vi1, vi2, vo2, vo1])
        else:
            f = bm.faces.new([vi1, vo1, vo2, vi2])
            
        uv_map_dict = {
            vi1: (0.0, r_mid * phi_1 / ts),
            vo1: (1.0, r_mid * phi_1 / ts),
            vo2: (1.0, r_mid * phi_2 / ts),
            vi2: (0.0, r_mid * phi_2 / ts)
        }
        for loop in f.loops:
            loop[uv_layer].uv = uv_map_dict[loop.vert]

def _add_circular_wall(bm, uv_layer, radius, phi_start, phi_end, ts, h, wt, z_base, flip=False):
    """Build a thick circular wall arc with inner/outer faces and top/bottom/end caps."""
    subdivs = 8
    alpha_total = phi_end - phi_start
    
    R_a = max(radius - wt / 2, ts * 0.01)
    R_b = radius + wt / 2
    
    verts_a_bot = []
    verts_a_top = []
    verts_b_bot = []
    verts_b_top = []
    
    for i in range(subdivs + 1):
        phi = phi_start + (i / subdivs) * alpha_total
        cos_phi = math.cos(phi)
        sin_phi = math.sin(phi)
        
        verts_a_bot.append(bm.verts.new((R_a * cos_phi, R_a * sin_phi, z_base)))
        verts_a_top.append(bm.verts.new((R_a * cos_phi, R_a * sin_phi, z_base + h)))
        verts_b_bot.append(bm.verts.new((R_b * cos_phi, R_b * sin_phi, z_base)))
        verts_b_top.append(bm.verts.new((R_b * cos_phi, R_b * sin_phi, z_base + h)))
        
    
    # 1. Inner curved face panels
    for i in range(subdivs):
        if flip:
            f = bm.faces.new([
                verts_a_bot[i],
                verts_a_bot[i + 1],
                verts_a_top[i + 1],
                verts_a_top[i]
            ])
        else:
            f = bm.faces.new([
                verts_a_bot[i + 1],
                verts_a_bot[i],
                verts_a_top[i],
                verts_a_top[i + 1]
            ])
        u0 = R_a * (phi_start + (i / subdivs) * alpha_total) / ts
        u1 = R_a * (phi_start + ((i + 1) / subdivs) * alpha_total) / ts
        if flip:
            uvs = [(u0, 0.0), (u1, 0.0), (u1, h / ts), (u0, h / ts)]
        else:
            uvs = [(u1, 0.0), (u0, 0.0), (u0, h / ts), (u1, h / ts)]
        for loop, uv in zip(f.loops, uvs):
            loop[uv_layer].uv = uv
            
    # 2. Outer curved face panels
    for i in range(subdivs):
        if flip:
            f = bm.faces.new([
                verts_b_bot[i + 1],
                verts_b_bot[i],
                verts_b_top[i],
                verts_b_top[i + 1]
            ])
        else:
            f = bm.faces.new([
                verts_b_bot[i],
                verts_b_bot[i + 1],
                verts_b_top[i + 1],
                verts_b_top[i]
            ])
        u0 = R_b * (phi_start + (i / subdivs) * alpha_total) / ts
        u1 = R_b * (phi_start + ((i + 1) / subdivs) * alpha_total) / ts
        if flip:
            uvs = [(u1, 0.0), (u0, 0.0), (u0, h / ts), (u1, h / ts)]
        else:
            uvs = [(u0, 0.0), (u1, 0.0), (u1, h / ts), (u0, h / ts)]
        for loop, uv in zip(f.loops, uvs):
            loop[uv_layer].uv = uv
            
    # 3. Bottom cap panels (normal -Z, visible from below)
    for i in range(subdivs):
        if flip:
            f = bm.faces.new([
                verts_a_bot[i],
                verts_b_bot[i],
                verts_b_bot[i + 1],
                verts_a_bot[i + 1]
            ])
        else:
            f = bm.faces.new([
                verts_a_bot[i + 1],
                verts_b_bot[i + 1],
                verts_b_bot[i],
                verts_a_bot[i]
            ])
        u0 = R_a * (phi_start + (i / subdivs) * alpha_total) / ts
        u1 = R_a * (phi_start + ((i + 1) / subdivs) * alpha_total) / ts
        if flip:
            uvs = [(0.0, u0), (wt / ts, u0), (wt / ts, u1), (0.0, u1)]
        else:
            uvs = [(0.0, u1), (wt / ts, u1), (wt / ts, u0), (0.0, u0)]
        for loop, uv in zip(f.loops, uvs):
            loop[uv_layer].uv = uv
            
    # 4. Top cap panels (normal +Z, visible from above)
    for i in range(subdivs):
        if flip:
            f = bm.faces.new([
                verts_a_top[i],
                verts_a_top[i + 1],
                verts_b_top[i + 1],
                verts_b_top[i]
            ])
        else:
            f = bm.faces.new([
                verts_a_top[i],
                verts_b_top[i],
                verts_b_top[i + 1],
                verts_a_top[i + 1]
            ])
        u0 = R_a * (phi_start + (i / subdivs) * alpha_total) / ts
        u1 = R_a * (phi_start + ((i + 1) / subdivs) * alpha_total) / ts
        if flip:
            uvs = [(0.0, u0), (0.0, u1), (wt / ts, u1), (wt / ts, u0)]
        else:
            uvs = [(0.0, u0), (wt / ts, u0), (wt / ts, u1), (0.0, u1)]
        for loop, uv in zip(f.loops, uvs):
            loop[uv_layer].uv = uv
            
    # 5. Start end-cap
    if flip:
        f_start = bm.faces.new([
            verts_a_bot[0],
            verts_a_top[0],
            verts_b_top[0],
            verts_b_bot[0]
        ])
        uvs_start = [(0.0, 0.0), (0.0, h / ts), (wt / ts, h / ts), (wt / ts, 0.0)]
    else:
        f_start = bm.faces.new([
            verts_a_bot[0],
            verts_b_bot[0],
            verts_b_top[0],
            verts_a_top[0]
        ])
        uvs_start = [(0.0, 0.0), (wt / ts, 0.0), (wt / ts, h / ts), (0.0, h / ts)]
    for loop, uv in zip(f_start.loops, uvs_start):
        loop[uv_layer].uv = uv
        
    # 6. End end-cap
    if flip:
        f_end = bm.faces.new([
            verts_b_bot[subdivs],
            verts_b_top[subdivs],
            verts_a_top[subdivs],
            verts_a_bot[subdivs]
        ])
        uvs_end = [(0.0, 0.0), (0.0, h / ts), (wt / ts, h / ts), (wt / ts, 0.0)]
    else:
        f_end = bm.faces.new([
            verts_b_bot[subdivs],
            verts_a_bot[subdivs],
            verts_a_top[subdivs],
            verts_b_top[subdivs]
        ])
        uvs_end = [(0.0, 0.0), (wt / ts, 0.0), (wt / ts, h / ts), (0.0, h / ts)]
    for loop, uv in zip(f_end.loops, uvs_end):
        loop[uv_layer].uv = uv

def _add_circular_wall_flat(bm, uv_layer, radius, phi_start, phi_end, ts, h, z_base, facing_outward: bool):
    """Build a thin (zero-thickness) circular wall arc segment facing inward or outward."""
    subdivs = 8
    alpha_total = phi_end - phi_start
    
    verts_bot = []
    verts_top = []
    
    for i in range(subdivs + 1):
        phi = phi_start + (i / subdivs) * alpha_total
        cos_phi = math.cos(phi)
        sin_phi = math.sin(phi)
        
        verts_bot.append(bm.verts.new((radius * cos_phi, radius * sin_phi, z_base)))
        verts_top.append(bm.verts.new((radius * cos_phi, radius * sin_phi, z_base + h)))
        
    
    for i in range(subdivs):
        if facing_outward:
            f = bm.faces.new([
                verts_bot[i],
                verts_bot[i + 1],
                verts_top[i + 1],
                verts_top[i]
            ])
            u0 = radius * (phi_start + (i / subdivs) * alpha_total) / ts
            u1 = radius * (phi_start + ((i + 1) / subdivs) * alpha_total) / ts
            uvs = [(u0, 0.0), (u1, 0.0), (u1, h / ts), (u0, h / ts)]
        else:
            f = bm.faces.new([
                verts_bot[i + 1],
                verts_bot[i],
                verts_top[i],
                verts_top[i + 1]
            ])
            u0 = radius * (phi_start + (i / subdivs) * alpha_total) / ts
            u1 = radius * (phi_start + ((i + 1) / subdivs) * alpha_total) / ts
            uvs = [(u1, 0.0), (u0, 0.0), (u0, h / ts), (u1, h / ts)]
            
        for loop, uv in zip(f.loops, uvs):
            loop[uv_layer].uv = uv

def _add_radial_wall_flat(bm, uv_layer, phi, r_in, r_out, ts, h, z_base, facing_clockwise: bool):
    """Build a thin radial wall face from r_in to r_out at angle phi."""
    ux = math.cos(phi)
    uy = math.sin(phi)
    
    v_in_bot = bm.verts.new((r_in * ux, r_in * uy, z_base))
    v_out_bot = bm.verts.new((r_out * ux, r_out * uy, z_base))
    v_in_top = bm.verts.new((r_in * ux, r_in * uy, z_base + h))
    v_out_top = bm.verts.new((r_out * ux, r_out * uy, z_base + h))
    
    
    L = r_out - r_in
    if facing_clockwise:
        face = bm.faces.new([v_in_bot, v_out_bot, v_out_top, v_in_top])
        uvs = [(0.0, 0.0), (L / ts, 0.0), (L / ts, h / ts), (0.0, h / ts)]
    else:
        face = bm.faces.new([v_in_bot, v_in_top, v_out_top, v_out_bot])
        uvs = [(0.0, 0.0), (0.0, h / ts), (L / ts, h / ts), (L / ts, 0.0)]
        
    for loop, uv in zip(face.loops, uvs):
        loop[uv_layer].uv = uv

def _add_radial_wall(bm, uv_layer, phi, r_in, r_out, ts, h, wt, z_base):
    """Build a thick radial wall with left, right, inner, outer, top and bottom faces."""
    ux = math.cos(phi)
    uy = math.sin(phi)
    vx = -math.sin(phi)
    vy = math.cos(phi)
    
    tw = wt / 2
    
    v_l_in_bot = bm.verts.new((r_in * ux + tw * vx, r_in * uy + tw * vy, z_base))
    v_l_out_bot = bm.verts.new((r_out * ux + tw * vx, r_out * uy + tw * vy, z_base))
    v_r_in_bot = bm.verts.new((r_in * ux - tw * vx, r_in * uy - tw * vy, z_base))
    v_r_out_bot = bm.verts.new((r_out * ux - tw * vx, r_out * uy - tw * vy, z_base))
    
    v_l_in_top = bm.verts.new((r_in * ux + tw * vx, r_in * uy + tw * vy, z_base + h))
    v_l_out_top = bm.verts.new((r_out * ux + tw * vx, r_out * uy + tw * vy, z_base + h))
    v_r_in_top = bm.verts.new((r_in * ux - tw * vx, r_in * uy - tw * vy, z_base + h))
    v_r_out_top = bm.verts.new((r_out * ux - tw * vx, r_out * uy - tw * vy, z_base + h))
    
    
    # Left face (at +v side, normal must point in +v direction = toward CCW neighbour)
    f_left = bm.faces.new([v_l_in_top, v_l_out_top, v_l_out_bot, v_l_in_bot])
    uvs_left = [(0.0, h / ts), (1.0, h / ts), (1.0, 0.0), (0.0, 0.0)]
    for loop, uv in zip(f_left.loops, uvs_left):
        loop[uv_layer].uv = uv
        
    # Right face (at -v side, normal must point in -v direction = toward CW neighbour)
    f_right = bm.faces.new([v_r_out_top, v_r_in_top, v_r_in_bot, v_r_out_bot])
    uvs_right = [(0.0, h / ts), (1.0, h / ts), (1.0, 0.0), (0.0, 0.0)]
    for loop, uv in zip(f_right.loops, uvs_right):
        loop[uv_layer].uv = uv
        
    # Inner face (at r_in, normal must point inward = toward center)
    f_inner = bm.faces.new([v_r_in_top, v_l_in_top, v_l_in_bot, v_r_in_bot])
    uvs_inner = [(0.0, h / ts), (wt / ts, h / ts), (wt / ts, 0.0), (0.0, 0.0)]
    for loop, uv in zip(f_inner.loops, uvs_inner):
        loop[uv_layer].uv = uv
        
    # Outer face (at r_out, normal must point outward = away from center)
    f_outer = bm.faces.new([v_l_out_top, v_r_out_top, v_r_out_bot, v_l_out_bot])
    uvs_outer = [(0.0, h / ts), (wt / ts, h / ts), (wt / ts, 0.0), (0.0, 0.0)]
    for loop, uv in zip(f_outer.loops, uvs_outer):
        loop[uv_layer].uv = uv
        
    # Bottom face (normal must point downward)
    f_bot = bm.faces.new([v_l_out_bot, v_r_out_bot, v_r_in_bot, v_l_in_bot])
    uvs_bot = [(0.0, 1.0), (wt / ts, 1.0), (wt / ts, 0.0), (0.0, 0.0)]
    for loop, uv in zip(f_bot.loops, uvs_bot):
        loop[uv_layer].uv = uv
        
    # Top face (normal must point upward)
    f_top = bm.faces.new([v_l_in_top, v_r_in_top, v_r_out_top, v_l_out_top])
    uvs_top = [(0.0, 0.0), (wt / ts, 0.0), (wt / ts, 1.0), (0.0, 1.0)]
    for loop, uv in zip(f_top.loops, uvs_top):
        loop[uv_layer].uv = uv

def _add_radial_wall_caps(bm_cap, uv_cap, phi, r_in, r_out, wt, h, z_base):
    """Add end-cap faces for a thick radial wall at the inner and outer boundaries."""
    ux = math.cos(phi)
    uy = math.sin(phi)
    vx = -math.sin(phi)
    vy = math.cos(phi)
    tw = wt / 2
    
    v_l_in_bot = bm_cap.verts.new((r_in * ux + tw * vx, r_in * uy + tw * vy, z_base))
    v_r_in_bot = bm_cap.verts.new((r_in * ux - tw * vx, r_in * uy - tw * vy, z_base))
    v_l_in_top = bm_cap.verts.new((r_in * ux + tw * vx, r_in * uy + tw * vy, z_base + h))
    v_r_in_top = bm_cap.verts.new((r_in * ux - tw * vx, r_in * uy - tw * vy, z_base + h))
    
    v_l_out_bot = bm_cap.verts.new((r_out * ux + tw * vx, r_out * uy + tw * vy, z_base))
    v_r_out_bot = bm_cap.verts.new((r_out * ux - tw * vx, r_out * uy - tw * vy, z_base))
    v_l_out_top = bm_cap.verts.new((r_out * ux + tw * vx, r_out * uy + tw * vy, z_base + h))
    v_r_out_top = bm_cap.verts.new((r_out * ux - tw * vx, r_out * uy - tw * vy, z_base + h))
    
    
    f_inner = bm_cap.faces.new([v_r_in_top, v_l_in_top, v_l_in_bot, v_r_in_bot])
    f_outer = bm_cap.faces.new([v_l_out_top, v_r_out_top, v_r_out_bot, v_l_out_bot])
    
    for f in (f_inner, f_outer):
        for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
            loop[uv_cap].uv = uv

def _add_circular_wall_caps(bm_cap, uv_cap, radius, phi_start, phi_end, wt, h, z_base):
    """Add end-cap faces for a thick circular wall arc at the start and end angles."""
    R_a = max(radius - wt / 2, 0.001)
    R_b = radius + wt / 2
    
    v_a_bot_s = bm_cap.verts.new((R_a * math.cos(phi_start), R_a * math.sin(phi_start), z_base))
    v_b_bot_s = bm_cap.verts.new((R_b * math.cos(phi_start), R_b * math.sin(phi_start), z_base))
    v_a_top_s = bm_cap.verts.new((R_a * math.cos(phi_start), R_a * math.sin(phi_start), z_base + h))
    v_b_top_s = bm_cap.verts.new((R_b * math.cos(phi_start), R_b * math.sin(phi_start), z_base + h))
    
    v_a_bot_e = bm_cap.verts.new((R_a * math.cos(phi_end), R_a * math.sin(phi_end), z_base))
    v_b_bot_e = bm_cap.verts.new((R_b * math.cos(phi_end), R_b * math.sin(phi_end), z_base))
    v_a_top_e = bm_cap.verts.new((R_a * math.cos(phi_end), R_a * math.sin(phi_end), z_base + h))
    v_b_top_e = bm_cap.verts.new((R_b * math.cos(phi_end), R_b * math.sin(phi_end), z_base + h))
    
    
    f_start = bm_cap.faces.new([v_a_bot_s, v_b_bot_s, v_b_top_s, v_a_top_s])
    f_end = bm_cap.faces.new([v_b_bot_e, v_a_bot_e, v_a_top_e, v_b_top_e])
    
    for f in (f_start, f_end):
        for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
            loop[uv_cap].uv = uv

def _add_mesh_polar_center(bm, src_mesh, mat_offset, uv_layer, final_materials_list, ts, z_off, centered, reverse_faces=False):
    """Place a custom mesh at the polar centre, subdividing and mapping a squircle-to-circle transformation."""
    material_map = []
    if final_materials_list is not None and src_mesh:
        for mat in src_mesh.materials:
            if mat:
                if mat not in final_materials_list:
                    final_materials_list.append(mat)
                material_map.append(final_materials_list.index(mat))
            else:
                material_map.append(0)

    temp_bm = bmesh.new()
    temp_bm.from_mesh(src_mesh)
    
    cent = Matrix.Translation(Vector((-ts / 2, -ts / 2, 0))) if not centered else Matrix.Identity(4)
    mat_combined = mat_offset @ cent
    bmesh.ops.transform(temp_bm, matrix=mat_combined, verts=temp_bm.verts)
    
    # Subdivide edges so the squircle-to-circle mapping has enough vertices
    # to approximate a smooth circle (otherwise a 4-vertex quad produces a
    # rounded-square gap at the ring-1 inner boundary).
    bmesh.ops.subdivide_edges(
        temp_bm,
        edges=list(temp_bm.edges),
        cuts=5,
        use_grid_fill=True,
    )

    half_ts = 0.5 * ts
    for v in temp_bm.verts:
        co = v.co
        nx = co.x / half_ts if half_ts > 0 else 0.0
        ny = co.y / half_ts if half_ts > 0 else 0.0
        
        nx = max(-1.0, min(nx, 1.0))
        ny = max(-1.0, min(ny, 1.0))
        
        nx_new = nx * math.sqrt(1.0 - (ny ** 2) / 2.0)
        ny_new = ny * math.sqrt(1.0 - (nx ** 2) / 2.0)
        
        v.co.x = nx_new * half_ts
        v.co.y = ny_new * half_ts
        v.co.z = co.z + z_off

    if final_materials_list is not None and material_map:
        for f in temp_bm.faces:
            if f.material_index < len(material_map):
                f.material_index = material_map[f.material_index]
            else:
                f.material_index = 0

    if reverse_faces:
        bmesh.ops.reverse_faces(temp_bm, faces=list(temp_bm.faces))

    _merge_bmesh_geometries(temp_bm, bm)
    temp_bm.free()

def _add_mesh_polar_bend_with_matrix(bm, src_mesh, mat_combined, uv_layer, final_materials_list, r, theta, Nr, ts, z_off, scale_angular=True, cuts=4, reverse_faces=True):
    """Bend a custom tile mesh along a polar wedge using an angular warp transformation."""
    material_map = []
    if final_materials_list is not None and src_mesh:
        for mat in src_mesh.materials:
            if mat:
                if mat not in final_materials_list:
                    final_materials_list.append(mat)
                material_map.append(final_materials_list.index(mat))
            else:
                material_map.append(0)

    # 1. Create temp BMesh, load, transform, subdivide, warp, flip normals, map materials
    cache = _get_bmesh_cache()
    if cache is not None and src_mesh in cache:
        temp_bm = cache[src_mesh].copy()
    else:
        temp_bm = bmesh.new()
        temp_bm.from_mesh(src_mesh)
        if cache is not None:
            cache[src_mesh] = temp_bm.copy()

    bmesh.ops.transform(temp_bm, matrix=mat_combined, verts=temp_bm.verts)

    # Subdivide edges of temp_bm to allow smooth bending.
    if cuts > 0:
        # Cap cuts to prevent vertex explosion on detailed/custom tiles
        if src_mesh and len(src_mesh.vertices) > 8:
            cuts = min(2, cuts)
        else:
            cuts = min(4, cuts)
        bmesh.ops.subdivide_edges(temp_bm, edges=list(temp_bm.edges), cuts=cuts, use_grid_fill=True)

    r_mid = r * ts
    alpha_r = 2 * math.pi / Nr
    theta_mid = (theta + 0.5) * alpha_r

    scale_x = (r_mid * alpha_r) / ts if (r_mid > 0 and scale_angular) else 1.0

    for v in temp_bm.verts:
        co = v.co
        x_rel = co.x
        y_rel = co.y
        
        if r_mid > 0:
            r_local = r_mid + y_rel
            theta_local = theta_mid + (x_rel * scale_x / r_mid)
            v.co.x = r_local * math.cos(theta_local)
            v.co.y = r_local * math.sin(theta_local)
        else:
            cos_t = math.cos(theta_mid)
            sin_t = math.sin(theta_mid)
            v.co.x = (r_mid + y_rel) * cos_t - x_rel * sin_t
            v.co.y = (r_mid + y_rel) * sin_t + x_rel * cos_t
        v.co.z = co.z + z_off

    if reverse_faces:
        bmesh.ops.reverse_faces(temp_bm, faces=list(temp_bm.faces))

    if final_materials_list is not None and material_map:
        for f in temp_bm.faces:
            if f.material_index < len(material_map):
                f.material_index = material_map[f.material_index]
            else:
                f.material_index = 0

    _merge_bmesh_geometries(temp_bm, bm)
    temp_bm.free()


def _add_mesh_polar_bend(bm, src_mesh, mat_offset, uv_layer, final_materials_list, r, theta, Nr, ts, z_off, centered, cuts=4, reverse_faces=True):
    """Wrap _add_mesh_polar_bend_with_matrix with a centering offset applied to the combined matrix."""
    cent = Matrix.Translation(Vector((-ts / 2, -ts / 2, 0))) if not centered else Matrix.Identity(4)
    mat_combined = mat_offset @ cent
    _add_mesh_polar_bend_with_matrix(bm, src_mesh, mat_combined, uv_layer, final_materials_list, r, theta, Nr, ts, z_off, scale_angular=True, cuts=cuts, reverse_faces=reverse_faces)


def _add_wall_polar_bend(bm, src_mesh, mat_wall_offset, uv_layer, final_materials_list, wall_type, r, theta, Nr, ts, z_off, centered, cuts=4, flip_out=False, reverse_faces=True, thin_wall_offset=0.0):
    """Place a custom wall mesh on a polar-grid boundary (CW, CCW, IN, or OUT) using bending alignment."""
    cent = Matrix.Translation(Vector((-ts / 2, -ts / 2, 0))) if not centered else Matrix.Identity(4)
    alpha_r = 2 * math.pi / Nr
    theta_mid = (theta + 0.5) * alpha_r
    r_mid = r * ts

    # Lift the wall by ts/2 to correct the Z-sinking caused by rotation
    z_lifted = z_off + ts / 2

    if wall_type in ('CW', 'CCW'):
        phi = theta_mid + (alpha_r / 2) if wall_type == 'CW' else theta_mid - (alpha_r / 2)
        shift_val = thin_wall_offset if wall_type == 'CW' else -thin_wall_offset
        mat_base = Matrix.Translation(Vector((r_mid * math.cos(phi), r_mid * math.sin(phi), z_lifted))) @ Matrix.Rotation(phi, 4, 'Z') @ Matrix.Translation(Vector((0, shift_val, 0)))
        # Stand tile upright with Rotation(X): original X stays radial, original Y becomes Z height.
        if wall_type == 'CW':
            mat_local = Matrix.Rotation(math.radians(90), 4, 'X')
        else:  # CCW
            mat_local = Matrix.Rotation(math.radians(180), 4, 'Z') @ Matrix.Rotation(math.radians(90), 4, 'X')
        mat_combined = mat_base @ mat_wall_offset @ mat_local @ cent
        _add_mesh_at(bm, src_mesh, mat_combined, uv_layer, final_materials_list)
    else:
        y_loc = -ts/2 + thin_wall_offset if wall_type == 'IN' else ts/2 + thin_wall_offset
        if flip_out:
            mat_place = Matrix.Translation(Vector((0, y_loc, 0))) @ Matrix.Rotation(math.radians(90), 4, 'X') @ Matrix.Scale(-1, 4, Vector((0, 0, 1)))
        else:
            mat_place = Matrix.Translation(Vector((0, y_loc, 0))) @ Matrix.Rotation(math.radians(90), 4, 'X') @ Matrix.Scale(-1, 4, Vector((1, 0, 0)))
        mat_combined = mat_place @ mat_wall_offset @ cent
        _add_mesh_polar_bend_with_matrix(bm, src_mesh, mat_combined, uv_layer, final_materials_list, r, theta, Nr, ts, z_lifted, scale_angular=True, cuts=cuts, reverse_faces=False)


def _add_mesh_polar_trapezoid_with_matrix(bm, src_mesh, mat_combined, uv_layer, final_materials_list, r, theta, Nr, ts, z_off, scale_angular=True, reverse_faces=True):
    """Stretch a custom tile mesh into a polar wedge using bilinear interpolation between the four corner coordinates."""
    material_map = []
    if final_materials_list is not None and src_mesh:
        for mat in src_mesh.materials:
            if mat:
                if mat not in final_materials_list:
                    final_materials_list.append(mat)
                material_map.append(final_materials_list.index(mat))
            else:
                material_map.append(0)

    # 1. Create temp BMesh, load, transform, warp, flip normals, map materials
    cache = _get_bmesh_cache()
    if cache is not None and src_mesh in cache:
        temp_bm = cache[src_mesh].copy()
    else:
        temp_bm = bmesh.new()
        temp_bm.from_mesh(src_mesh)
        if cache is not None:
            cache[src_mesh] = temp_bm.copy()

    bmesh.ops.transform(temp_bm, matrix=mat_combined, verts=temp_bm.verts)

    r_mid = r * ts
    alpha_r = 2 * math.pi / Nr
    theta_a = theta * alpha_r
    theta_b = (theta + 1) * alpha_r
    theta_mid = (theta + 0.5) * alpha_r
    r_in = r_mid - ts / 2
    r_out = r_mid + ts / 2

    c_in_ccw_x = r_in * math.cos(theta_a)
    c_in_ccw_y = r_in * math.sin(theta_a)
    c_in_cw_x = r_in * math.cos(theta_b)
    c_in_cw_y = r_in * math.sin(theta_b)
    c_out_ccw_x = r_out * math.cos(theta_a)
    c_out_ccw_y = r_out * math.sin(theta_a)
    c_out_cw_x = r_out * math.cos(theta_b)
    c_out_cw_y = r_out * math.sin(theta_b)

    inv_ts = 1.0 / ts
    for v in temp_bm.verts:
        co = v.co
        if r_mid > 0 and scale_angular:
            # Bilinear interpolation across the four real polar corners of the
            # wedge so adjacent tiles meet exactly on shared edges.
            u = co.x * inv_ts + 0.5
            vv = co.y * inv_ts + 0.5
            omu = 1.0 - u
            omv = 1.0 - vv
            w_in_ccw = omu * omv
            w_in_cw = u * omv
            w_out_ccw = omu * vv
            w_out_cw = u * vv
            v.co.x = (w_in_ccw * c_in_ccw_x + w_in_cw * c_in_cw_x
                      + w_out_ccw * c_out_ccw_x + w_out_cw * c_out_cw_x)
            v.co.y = (w_in_ccw * c_in_ccw_y + w_in_cw * c_in_cw_y
                      + w_out_ccw * c_out_ccw_y + w_out_cw * c_out_cw_y)
        else:
            # Fallback: plain rotate+translate with no angular scaling.
            x_rel = co.x
            y_rel = co.y
            cos_t = math.cos(theta_mid)
            sin_t = math.sin(theta_mid)
            v.co.x = (r_mid + y_rel) * cos_t - x_rel * sin_t
            v.co.y = (r_mid + y_rel) * sin_t + x_rel * cos_t
        v.co.z = co.z + z_off

    if reverse_faces:
        bmesh.ops.reverse_faces(temp_bm, faces=list(temp_bm.faces))

    if final_materials_list is not None and material_map:
        for f in temp_bm.faces:
            if f.material_index < len(material_map):
                f.material_index = material_map[f.material_index]
            else:
                f.material_index = 0

    _merge_bmesh_geometries(temp_bm, bm)
    temp_bm.free()


def _add_mesh_polar_trapezoid(bm, src_mesh, mat_offset, uv_layer, final_materials_list, r, theta, Nr, ts, z_off, centered, reverse_faces=True):
    """Wrap _add_mesh_polar_trapezoid_with_matrix with a centering offset applied to the combined matrix."""
    cent = Matrix.Translation(Vector((-ts / 2, -ts / 2, 0))) if not centered else Matrix.Identity(4)
    mat_combined = mat_offset @ cent
    _add_mesh_polar_trapezoid_with_matrix(bm, src_mesh, mat_combined, uv_layer, final_materials_list, r, theta, Nr, ts, z_off, scale_angular=True, reverse_faces=reverse_faces)


def _add_wall_polar_trapezoid(bm, src_mesh, mat_wall_offset, uv_layer, final_materials_list, wall_type, r, theta, Nr, ts, z_off, centered, flip_out=False, reverse_faces=True, thin_wall_offset=0.0):
    """Place a custom wall mesh on a polar-grid boundary using trapezoidal (scaling) alignment."""
    cent = Matrix.Translation(Vector((-ts / 2, -ts / 2, 0))) if not centered else Matrix.Identity(4)
    alpha_r = 2 * math.pi / Nr
    theta_mid = (theta + 0.5) * alpha_r
    r_mid = r * ts

    # Lift the wall by ts/2 to correct the Z-sinking caused by rotation
    z_lifted = z_off + ts / 2

    if wall_type in ('CW', 'CCW'):
        phi = theta_mid + (alpha_r / 2) if wall_type == 'CW' else theta_mid - (alpha_r / 2)
        shift_val = thin_wall_offset if wall_type == 'CW' else -thin_wall_offset
        mat_base = Matrix.Translation(Vector((r_mid * math.cos(phi), r_mid * math.sin(phi), z_lifted))) @ Matrix.Rotation(phi, 4, 'Z') @ Matrix.Translation(Vector((0, shift_val, 0)))
        if wall_type == 'CW':
            mat_local = Matrix.Rotation(math.radians(90), 4, 'X')
        else:  # CCW
            mat_local = Matrix.Rotation(math.radians(180), 4, 'Z') @ Matrix.Rotation(math.radians(90), 4, 'X')
        mat_combined = mat_base @ mat_wall_offset @ mat_local @ cent
        _add_mesh_at(bm, src_mesh, mat_combined, uv_layer, final_materials_list)
    else:
        y_loc = -ts/2 + thin_wall_offset if wall_type == 'IN' else ts/2 + thin_wall_offset
        if flip_out:
            mat_place = Matrix.Translation(Vector((0, y_loc, 0))) @ Matrix.Rotation(math.radians(90), 4, 'X') @ Matrix.Scale(-1, 4, Vector((0, 0, 1)))
        else:
            mat_place = Matrix.Translation(Vector((0, y_loc, 0))) @ Matrix.Rotation(math.radians(90), 4, 'X') @ Matrix.Scale(-1, 4, Vector((1, 0, 0)))
        mat_combined = mat_place @ mat_wall_offset @ cent
        _add_mesh_polar_trapezoid_with_matrix(bm, src_mesh, mat_combined, uv_layer, final_materials_list, r, theta, Nr, ts, z_lifted, scale_angular=True, reverse_faces=False)

def _build_polar_floor(ctx, props, maze_data, created_objects, name_suffix):
    # Floor
    bm_floor, uv_floor, floor_materials = _create_bmesh_element("floor", ctx['materials'])
    rings = maze_data.polar_rings
    ring_sectors = maze_data.ring_sectors
    alignment = props.polar_custom_alignment

    for z in ctx['z_range']:
        z_off = z * ctx['wh']
        level_cells = ctx['cells_3d'][z]
        for r in range(rings):
            Nr = ring_sectors[r]
            alpha_r = 2 * math.pi / Nr
            for theta in range(Nr):
                if (z, r, theta) in ctx['stair_top_cells']:
                    continue
                if props.wall_mode == 'cube' and level_cells[r][theta][0]:
                    continue
                if len(level_cells[r][theta]) >= 8:
                    floor_idx = level_cells[r][theta][4]
                else:
                    floor_idx = level_cells[r][theta][4] if len(level_cells[r][theta]) > 4 else -1

                # Default uninitialized index (-1) to 0 when a collection is available so
                # the custom mesh is used from the very first generation (no shift-click needed).
                if floor_idx < 0 and ctx['floor_meshes_list']:
                    floor_idx = 0

                src_floor = None
                if ctx['floor_meshes_list'] and isinstance(floor_idx, int) and 0 <= floor_idx < len(ctx['floor_meshes_list']):
                    src_floor = ctx['floor_meshes_list'][floor_idx]
                elif ctx['custom_floor']:
                    src_floor = ctx['custom_floor']

                if r == 0:
                    if src_floor and alignment != 'procedural':
                        _add_mesh_polar_center(bm_floor, src_floor, ctx['mat_floor_offset'], uv_floor, floor_materials, ctx['ts'], z_off, ctx['centered'], reverse_faces=False)
                    else:
                        _add_polar_center_fan(bm_floor, uv_floor, ctx['ts'], z_off, is_roof=False, flip_normal=False)
                elif src_floor and alignment != 'procedural':
                    mat_flipped = ctx['mat_floor_offset'] @ Matrix.Scale(-1, 4, Vector((1, 0, 0)))
                    if alignment == 'trapezoid':
                        _add_mesh_polar_trapezoid(bm_floor, src_floor, mat_flipped, uv_floor, floor_materials, r, theta, Nr, ctx['ts'], z_off, ctx['centered'], reverse_faces=False)
                    elif alignment == 'bend':
                        N_max = ring_sectors[-1]
                        ratio = N_max // Nr if Nr > 0 else 1
                        cuts = max(1, ratio * 8 - 1)
                        _add_mesh_polar_bend(bm_floor, src_floor, mat_flipped, uv_floor, floor_materials, r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, reverse_faces=False)
                else:
                    _add_polar_floor_wedge(bm_floor, uv_floor, r, theta, Nr, ctx['ts'], z_off, is_roof=False)

    floor_obj = _create_object_from_bm(bm_floor, f"FireMaze_Floor{name_suffix}", ctx['col'], None)
    for mat in floor_materials:
        floor_obj.data.materials.append(mat)
    created_objects.append(floor_obj)


def _build_polar_walls(ctx, props, maze_data, created_objects, name_suffix):
    # Walls
    bm_wall, uv_wall, wall_materials = _create_bmesh_element("wall", ctx['materials'])
    bm_cap, uv_cap, cap_materials = _create_bmesh_element("end_cap", ctx['materials'])
    rings = maze_data.polar_rings
    ring_sectors = maze_data.ring_sectors
    alignment = props.polar_custom_alignment
    wall_rng = _real_random.Random(props.seed if props.seed else None)

    if props.wall_mode == 'cube':
        for z in ctx['z_range']:
            z_off_floor = z * ctx['wh']
            level_cells = ctx['cells_3d'][z]
            for level in range(ctx['tiles_high']):
                z_off = z_off_floor + level * ctx['seg_h']
                
                for r in range(rings):
                    Nr = ring_sectors[r]
                    alpha_r = 2 * math.pi / Nr
                    
                    for theta in range(Nr):
                        is_wall = False if (z, r, theta) in ctx['stair_cells'] else level_cells[r][theta][0]
                        if not is_wall and (z, r, theta) not in ctx['stair_cells']:
                            is_entrance = False
                            if maze_data.entrance:
                                en_r, en_theta, en_side = maze_data.entrance
                                if en_r == r and en_theta == theta:
                                    is_entrance = True
                            is_exit = False
                            if maze_data.exits:
                                for ex_r, ex_theta, ex_side in maze_data.exits:
                                    if ex_r == r and ex_theta == theta:
                                        is_exit = True
                                        break
                            if (is_entrance and z > 0) or (is_exit and z < (ctx['floors'] - 1)):
                                is_wall = True
                        
                        if is_wall:
                            if props.cube_mode_pillar and (ctx['wall_meshes_list'] or ctx['custom_wall']):
                                src_mesh = None
                                wall_idx = level_cells[r][theta][2] if len(level_cells[r][theta]) > 2 else -1
                                if ctx['wall_meshes_list']:
                                    if isinstance(wall_idx, int) and 0 <= wall_idx < len(ctx['wall_meshes_list']):
                                        src_mesh = ctx['wall_meshes_list'][wall_idx]
                                    else:
                                        src_mesh = wall_rng.choice(ctx['wall_meshes_list'])
                                else:
                                    src_mesh = ctx['custom_wall']
                                    
                                if src_mesh:
                                    if alignment == 'trapezoid':
                                        _add_mesh_polar_trapezoid(bm_wall, src_mesh, ctx['mat_wall_offset'], uv_wall, wall_materials, r, theta, Nr, ctx['ts'], z_off, ctx['centered'])
                                    elif alignment == 'bend':
                                        N_max = ring_sectors[-1]
                                        ratio = N_max // Nr if Nr > 0 else 1
                                        cuts = max(1, ratio * 8 - 1)
                                        _add_mesh_polar_bend(bm_wall, src_mesh, ctx['mat_wall_offset'], uv_wall, wall_materials, r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts)
                            elif r == rings - 1:
                                src_out_wall = None
                                if len(level_cells[r][theta]) >= 8:
                                    out_idx = level_cells[r][theta][6]
                                else:
                                    out_idx = level_cells[r][theta][3] if len(level_cells[r][theta]) > 3 else -1
                                
                                if ctx['wall_meshes_list'] and isinstance(out_idx, int) and 0 <= out_idx < len(ctx['wall_meshes_list']):
                                    src_out_wall = ctx['wall_meshes_list'][out_idx]
                                elif ctx['custom_wall']:
                                    src_out_wall = ctx['custom_wall']
                                    
                                if src_out_wall and alignment != 'procedural':
                                    if alignment == 'trapezoid':
                                        _add_wall_polar_trapezoid(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=True)
                                    elif alignment == 'bend':
                                        N_max = ring_sectors[-1]
                                        ratio = N_max // Nr if Nr > 0 else 1
                                        cuts = max(1, ratio * 8 - 1)
                                        _add_wall_polar_bend(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=True)
                                else:
                                    radius = (r + 0.5) * ctx['ts']
                                    phi_start = theta * alpha_r
                                    phi_end = (theta + 1) * alpha_r
                                    _add_circular_wall_flat(bm_wall, uv_wall, radius, phi_start, phi_end, ctx['ts'], ctx['seg_h'], z_off, facing_outward=True)
                            continue
                        
                        if props.cube_mode_pillar and (ctx['wall_meshes_list'] or ctx['custom_wall']) and not props.is_editing:
                            continue
                        
                        if len(level_cells[r][theta]) >= 8:
                            cw_idx = level_cells[r][theta][2]
                            ccw_idx = level_cells[r][theta][3]
                            in_idx = level_cells[r][theta][7] if len(level_cells[r][theta]) > 7 else -1
                            out_idx = level_cells[r][theta][8] if len(level_cells[r][theta]) > 8 else -1
                        else:
                            cw_idx = level_cells[r][theta][2] if len(level_cells[r][theta]) > 2 else -1
                            ccw_idx = cw_idx
                            in_idx = level_cells[r][theta][3] if len(level_cells[r][theta]) > 3 else -1
                            out_idx = in_idx
                        
                        src_cw_wall = None
                        if ctx['wall_meshes_list'] and isinstance(cw_idx, int) and 0 <= cw_idx < len(ctx['wall_meshes_list']):
                            src_cw_wall = ctx['wall_meshes_list'][cw_idx]
                        elif ctx['custom_wall']:
                            src_cw_wall = ctx['custom_wall']
     
                        src_ccw_wall = None
                        if ctx['wall_meshes_list'] and isinstance(ccw_idx, int) and 0 <= ccw_idx < len(ctx['wall_meshes_list']):
                            src_ccw_wall = ctx['wall_meshes_list'][ccw_idx]
                        elif ctx['custom_wall']:
                            src_ccw_wall = ctx['custom_wall']
                            
                        src_in_wall = None
                        if ctx['wall_meshes_list'] and isinstance(in_idx, int) and 0 <= in_idx < len(ctx['wall_meshes_list']):
                            src_in_wall = ctx['wall_meshes_list'][in_idx]
                        elif ctx['custom_wall']:
                            src_in_wall = ctx['custom_wall']
     
                        src_out_wall = None
                        if ctx['wall_meshes_list'] and isinstance(out_idx, int) and 0 <= out_idx < len(ctx['wall_meshes_list']):
                            src_out_wall = ctx['wall_meshes_list'][out_idx]
                        elif ctx['custom_wall']:
                            src_out_wall = ctx['custom_wall']
 
                        # 1. Clockwise boundary
                        cw_neighbor_is_wall = level_cells[r][(theta + 1) % Nr][0]
                        if cw_neighbor_is_wall:
                            if src_cw_wall and alignment != 'procedural':
                                if alignment == 'trapezoid':
                                    _add_wall_polar_trapezoid(bm_wall, src_cw_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'CW', r, theta, Nr, ctx['ts'], z_off, ctx['centered'])
                                elif alignment == 'bend':
                                    N_max = ring_sectors[-1]
                                    ratio = N_max // Nr if Nr > 0 else 1
                                    cuts = max(1, ratio * 8 - 1)
                                    _add_wall_polar_bend(bm_wall, src_cw_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'CW', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts)
                            else:
                                phi = (theta + 1) * alpha_r
                                r_in = (r - 0.5) * ctx['ts']
                                r_out = (r + 0.5) * ctx['ts']
                                _add_radial_wall_flat(bm_wall, uv_wall, phi, r_in, r_out, ctx['ts'], ctx['seg_h'], z_off, facing_clockwise=True)
 
                        # 2. Counter-clockwise boundary
                        ccw_neighbor_is_wall = level_cells[r][(theta - 1) % Nr][0]
                        if ccw_neighbor_is_wall:
                            if src_ccw_wall and alignment != 'procedural':
                                if alignment == 'trapezoid':
                                    _add_wall_polar_trapezoid(bm_wall, src_ccw_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'CCW', r, theta, Nr, ctx['ts'], z_off, ctx['centered'])
                                elif alignment == 'bend':
                                    N_max = ring_sectors[-1]
                                    ratio = N_max // Nr if Nr > 0 else 1
                                    cuts = max(1, ratio * 8 - 1)
                                    _add_wall_polar_bend(bm_wall, src_ccw_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'CCW', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts)
                            else:
                                phi = theta * alpha_r
                                r_in = (r - 0.5) * ctx['ts']
                                r_out = (r + 0.5) * ctx['ts']
                                _add_radial_wall_flat(bm_wall, uv_wall, phi, r_in, r_out, ctx['ts'], ctx['seg_h'], z_off, facing_clockwise=False)
 
                        # 3. Inward boundary
                        if r > 0:
                            N_in = ring_sectors[r - 1]
                            theta_in = 0 if N_in == 1 else (theta if N_in == Nr else theta // 2)
                            in_neighbor_is_wall = level_cells[r - 1][theta_in][0]
                            if in_neighbor_is_wall:
                                if src_in_wall and alignment != 'procedural':
                                    if alignment == 'trapezoid':
                                        _add_wall_polar_trapezoid(bm_wall, src_in_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'IN', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=True)
                                    elif alignment == 'bend':
                                        N_max = ring_sectors[-1]
                                        ratio = N_max // Nr if Nr > 0 else 1
                                        cuts = max(1, ratio * 8 - 1)
                                        _add_wall_polar_bend(bm_wall, src_in_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'IN', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=True)
                                else:
                                    radius = (r - 0.5) * ctx['ts']
                                    phi_start = theta * alpha_r
                                    phi_end = (theta + 1) * alpha_r
                                    _add_circular_wall_flat(bm_wall, uv_wall, radius, phi_start, phi_end, ctx['ts'], ctx['seg_h'], z_off, facing_outward=True)
 
                        # 4. Outward boundary
                        if r < rings - 1:
                            N_out = ring_sectors[r + 1]
                            if N_out == Nr:
                                out_neighbors = [theta]
                            elif Nr == 1:
                                out_neighbors = list(range(N_out))
                            else:
                                out_neighbors = [2 * theta, 2 * theta + 1]
                                
                            for ot in out_neighbors:
                                out_neighbor_is_wall = level_cells[r + 1][ot][0]
                                if out_neighbor_is_wall:
                                    phi_start_ot = ot * (2 * math.pi / N_out)
                                    phi_end_ot = (ot + 1) * (2 * math.pi / N_out)
                                    if src_out_wall and alignment != 'procedural':
                                        w_type = 'IN' if r == 0 else 'OUT'
                                        r_val = 1 if r == 0 else r
                                        if alignment == 'trapezoid':
                                            _add_wall_polar_trapezoid(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, w_type, r_val, ot, N_out, ctx['ts'], z_off, ctx['centered'], flip_out=False)
                                        elif alignment == 'bend':
                                            N_max = ring_sectors[-1]
                                            ratio = N_max // N_out if N_out > 0 else 1
                                            cuts = max(1, ratio * 8 - 1)
                                            _add_wall_polar_bend(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, w_type, r_val, ot, N_out, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=False)
                                    else:
                                        radius = (r + 0.5) * ctx['ts']
                                        _add_circular_wall_flat(bm_wall, uv_wall, radius, phi_start_ot, phi_end_ot, ctx['ts'], ctx['seg_h'], z_off, facing_outward=False)
                        else:
                            is_entrance = False
                            if z == 0 and maze_data.entrance:
                                en_r, en_theta, en_side = maze_data.entrance
                                if en_r == r and en_theta == theta and en_side == 'OUT':
                                    is_entrance = True
                            is_exit = False
                            if z == (ctx['floors'] - 1) and maze_data.exits:
                                for ex_r, ex_theta, ex_side in maze_data.exits:
                                    if ex_r == r and ex_theta == theta and ex_side == 'OUT':
                                        is_exit = True
                                        break
                                        
                            is_stair = (z, r, theta) in ctx['stair_cells']
                            if not is_entrance and not is_exit and not is_stair:
                                if src_out_wall and alignment != 'procedural':
                                    if alignment == 'trapezoid':
                                        _add_wall_polar_trapezoid(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=True)
                                    elif alignment == 'bend':
                                        N_max = ring_sectors[-1]
                                        ratio = N_max // Nr if Nr > 0 else 1
                                        cuts = max(1, ratio * 8 - 1)
                                        _add_wall_polar_bend(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=True)
                                else:
                                    radius = (r + 0.5) * ctx['ts']
                                    phi_start = theta * alpha_r
                                    phi_end = (theta + 1) * alpha_r
                                    _add_circular_wall_flat(bm_wall, uv_wall, radius, phi_start, phi_end, ctx['ts'], ctx['seg_h'], z_off, facing_outward=True)

    else:
        for z in ctx['z_range']:
            z_off_floor = z * ctx['wh']
            level_cells = ctx['cells_3d'][z]
            for level in range(ctx['tiles_high']):
                z_off = z_off_floor + level * ctx['seg_h']
                
                for r in range(rings):
                    Nr = ring_sectors[r]
                    alpha_r = 2 * math.pi / Nr
                    
                    for theta in range(Nr):
                        cw_wall = True if name_suffix == "_EditHelper" else level_cells[r][theta][0]
                        in_wall = True if name_suffix == "_EditHelper" else level_cells[r][theta][1]
                        
                        sh_cw = ctx['seg_h']
                        if name_suffix == "_EditHelper" and not level_cells[r][theta][0]:
                            sh_cw = 0.02 * ctx['ts']
                            
                        sh_in = ctx['seg_h']
                        if name_suffix == "_EditHelper" and not level_cells[r][theta][1]:
                            sh_in = 0.02 * ctx['ts']
                        
                        cw_idx = level_cells[r][theta][2] if len(level_cells[r][theta]) > 2 else -1
                        in_idx = level_cells[r][theta][3] if len(level_cells[r][theta]) > 3 else -1
                        out_idx = level_cells[r][theta][6] if len(level_cells[r][theta]) > 6 else in_idx
                        
                        src_cw_wall = None
                        if ctx['wall_meshes_list'] and isinstance(cw_idx, int) and 0 <= cw_idx < len(ctx['wall_meshes_list']):
                            src_cw_wall = ctx['wall_meshes_list'][cw_idx]
                        elif ctx['custom_wall']:
                            src_cw_wall = ctx['custom_wall']
                        
                        src_in_wall = None
                        if ctx['wall_meshes_list'] and isinstance(in_idx, int) and 0 <= in_idx < len(ctx['wall_meshes_list']):
                            src_in_wall = ctx['wall_meshes_list'][in_idx]
                        elif ctx['custom_wall']:
                            src_in_wall = ctx['custom_wall']
     
                        src_out_wall = None
                        if ctx['wall_meshes_list'] and isinstance(out_idx, int) and 0 <= out_idx < len(ctx['wall_meshes_list']):
                            src_out_wall = ctx['wall_meshes_list'][out_idx]
                        elif ctx['custom_wall']:
                            src_out_wall = ctx['custom_wall']
                        
                        if cw_wall and r >= 1:
                            if src_cw_wall and alignment != 'procedural':
                                if props.thin_wall_double_sided:
                                    if alignment == 'trapezoid':
                                        _add_wall_polar_trapezoid(bm_wall, src_cw_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'CW', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], thin_wall_offset=-ctx['wt']/2)
                                        _add_wall_polar_trapezoid(bm_wall, src_cw_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'CCW', r, (theta + 1) % Nr, Nr, ctx['ts'], z_off, ctx['centered'], thin_wall_offset=-ctx['wt']/2)
                                    elif alignment == 'bend':
                                        N_max = ring_sectors[-1]
                                        ratio = N_max // Nr if Nr > 0 else 1
                                        cuts = max(1, ratio * 8 - 1)
                                        _add_wall_polar_bend(bm_wall, src_cw_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'CW', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, thin_wall_offset=-ctx['wt']/2)
                                        _add_wall_polar_bend(bm_wall, src_cw_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'CCW', r, (theta + 1) % Nr, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, thin_wall_offset=-ctx['wt']/2)
                                    # End Caps for Radial Wall (CW)
                                    phi = (theta + 1) * alpha_r
                                    r_in = (r - 0.5) * ctx['ts']
                                    r_out = (r + 0.5) * ctx['ts']
                                    _add_radial_wall_caps(bm_cap, uv_cap, phi, r_in, r_out, ctx['wt'], sh_cw, z_off)
                                else:
                                    if alignment == 'trapezoid':
                                        _add_wall_polar_trapezoid(bm_wall, src_cw_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'CW', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], thin_wall_offset=0.0)
                                    elif alignment == 'bend':
                                        N_max = ring_sectors[-1]
                                        ratio = N_max // Nr if Nr > 0 else 1
                                        cuts = max(1, ratio * 8 - 1)
                                        _add_wall_polar_bend(bm_wall, src_cw_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'CW', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, thin_wall_offset=0.0)
                            else:
                                phi = (theta + 1) * alpha_r
                                r_in = (r - 0.5) * ctx['ts']
                                r_out = (r + 0.5) * ctx['ts']
                                _add_radial_wall(bm_wall, uv_wall, phi, r_in, r_out, ctx['ts'], sh_cw, ctx['wt'], z_off)
 
                        if in_wall and r >= 1:
                            if src_in_wall and alignment != 'procedural':
                                N_in = ring_sectors[r - 1]
                                theta_in = 0 if N_in == 1 else (theta if N_in == Nr else theta // 2)
                                if props.thin_wall_double_sided:
                                    if alignment == 'trapezoid':
                                        # Wall 1: inward face — keep inward-facing (-Y, toward inner ring)
                                        _add_wall_polar_trapezoid(bm_wall, src_in_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'IN', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=False, thin_wall_offset=-ctx['wt']/2)
                                        # Wall 2: outward face — flip so blue faces outward (+Y, toward ring r)
                                        _add_wall_polar_trapezoid(bm_wall, src_in_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'IN', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=True, thin_wall_offset=ctx['wt']/2)
                                    elif alignment == 'bend':
                                        N_max = ring_sectors[-1]
                                        ratio = N_max // Nr if Nr > 0 else 1
                                        cuts = max(1, ratio * 8 - 1)
                                        _add_wall_polar_bend(bm_wall, src_in_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'IN', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=False, thin_wall_offset=-ctx['wt']/2)
                                        _add_wall_polar_bend(bm_wall, src_in_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'IN', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=True, thin_wall_offset=ctx['wt']/2)
                                    # End Caps for Circular Wall (IN)
                                    radius = (r - 0.5) * ctx['ts']
                                    phi_start = theta * alpha_r
                                    phi_end = (theta + 1) * alpha_r
                                    _add_circular_wall_caps(bm_cap, uv_cap, radius, phi_start, phi_end, ctx['wt'], sh_in, z_off)
                                else:
                                    if alignment == 'trapezoid':
                                        _add_wall_polar_trapezoid(bm_wall, src_in_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'IN', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=True, thin_wall_offset=0.0)
                                    elif alignment == 'bend':
                                        N_max = ring_sectors[-1]
                                        ratio = N_max // Nr if Nr > 0 else 1
                                        cuts = max(1, ratio * 8 - 1)
                                        _add_wall_polar_bend(bm_wall, src_in_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'IN', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=True, thin_wall_offset=0.0)
                            else:
                                radius = (r - 0.5) * ctx['ts']
                                phi_start = theta * alpha_r
                                phi_end = (theta + 1) * alpha_r
                                _add_circular_wall(bm_wall, uv_wall, radius, phi_start, phi_end, ctx['ts'], sh_in, ctx['wt'], z_off)
       
                        if r == rings - 1:
                            is_entrance_actual = False
                            if z == 0 and maze_data.entrance:
                                en_r, en_theta, en_side = maze_data.entrance
                                if en_r == r and en_theta == theta and en_side == 'OUT':
                                    is_entrance_actual = True
                            is_exit_actual = False
                            if z == (ctx['floors'] - 1) and maze_data.exits:
                                for ex_r, ex_theta, ex_side in maze_data.exits:
                                    if ex_r == r and ex_theta == theta and ex_side == 'OUT':
                                        is_exit_actual = True
                                        break
                            
                            is_entrance = is_entrance_actual if name_suffix != "_EditHelper" else False
                            is_exit = is_exit_actual if name_suffix != "_EditHelper" else False
                            is_stair = (z, r, theta) in ctx['stair_cells']
                            
                            sh_out = ctx['seg_h']
                            if name_suffix == "_EditHelper" and (is_entrance_actual or is_exit_actual or is_stair):
                                sh_out = 0.02 * ctx['ts']
                                
                            if not is_entrance and not is_exit and not is_stair:
                                if src_out_wall and alignment != 'procedural':
                                    if props.thin_wall_double_sided:
                                        if alignment == 'trapezoid':
                                            # Inner face of perimeter wall — faces inward (visible to players inside)
                                            _add_wall_polar_trapezoid(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=False, thin_wall_offset=-ctx['wt']/2)
                                            # Outer face of perimeter wall — faces outward
                                            _add_wall_polar_trapezoid(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=True, thin_wall_offset=ctx['wt']/2)
                                        elif alignment == 'bend':
                                            N_max = ring_sectors[-1]
                                            ratio = N_max // Nr if Nr > 0 else 1
                                            cuts = max(1, ratio * 8 - 1)
                                            _add_wall_polar_bend(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=False, thin_wall_offset=-ctx['wt']/2)
                                            _add_wall_polar_bend(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=True, thin_wall_offset=ctx['wt']/2)
                                        # End Caps for Outer Circular Wall
                                        radius = (r + 0.5) * ctx['ts']
                                        phi_start = theta * alpha_r
                                        phi_end = (theta + 1) * alpha_r
                                        _add_circular_wall_caps(bm_cap, uv_cap, radius, phi_start, phi_end, ctx['wt'], sh_out, z_off)
                                    else:
                                        # Single-sided: face inward so players see the front face
                                        if alignment == 'trapezoid':
                                            _add_wall_polar_trapezoid(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=False, thin_wall_offset=0.0)
                                        elif alignment == 'bend':
                                            N_max = ring_sectors[-1]
                                            ratio = N_max // Nr if Nr > 0 else 1
                                            cuts = max(1, ratio * 8 - 1)
                                            _add_wall_polar_bend(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=False, thin_wall_offset=0.0)
                                else:
                                    radius = (r + 0.5) * ctx['ts']
                                    phi_start = theta * alpha_r
                                    phi_end = (theta + 1) * alpha_r
                                    _add_circular_wall(bm_wall, uv_wall, radius, phi_start, phi_end, ctx['ts'], sh_out, ctx['wt'], z_off)
 
    if bm_wall.verts:
        _safe_remove_doubles(bm_wall, dist=0.001)
    if bm_cap.verts:
        _safe_remove_doubles(bm_cap, dist=0.001)
        
    # Handle single wall object merge of caps
    if props.single_wall_object and bm_cap.verts:
        vert_map = {}
        for v in bm_cap.verts:
            new_v = bm_wall.verts.new(v.co)
            vert_map[v] = new_v
        if ctx['materials']["end_cap"] not in wall_materials:
            wall_materials.append(ctx['materials']["end_cap"])
        cap_mat_idx = wall_materials.index(ctx['materials']["end_cap"])
        
        for f in bm_cap.faces:
            new_verts = [vert_map[v] for v in f.verts]
            new_f = bm_wall.faces.new(new_verts)
            new_f.material_index = cap_mat_idx
            uv_cap_layer = bm_cap.loops.layers.uv.active
            uv_wall_layer = bm_wall.loops.layers.uv.active
            if uv_cap_layer and uv_wall_layer:
                for l_cap, l_wall in zip(f.loops, new_f.loops):
                    l_wall[uv_wall_layer].uv = l_cap[uv_cap_layer].uv
        bm_cap.free()
        wall_obj = _create_object_from_bm(bm_wall, f"FireMaze_Walls{name_suffix}", ctx['col'], None)
        for mat in wall_materials:
            wall_obj.data.materials.append(mat)
        created_objects.append(wall_obj)
    else:
        wall_obj = _create_object_from_bm(bm_wall, f"FireMaze_Walls{name_suffix}", ctx['col'], None)
        for mat in wall_materials:
            wall_obj.data.materials.append(mat)
        created_objects.append(wall_obj)
        if bm_cap.verts:
            cap_obj = _create_object_from_bm(bm_cap, f"FireMaze_WallEndCaps{name_suffix}", ctx['col'], None)
            for mat in cap_materials:
                cap_obj.data.materials.append(mat)
            created_objects.append(cap_obj)
        else:
            bm_cap.free()


def _build_polar_roof(ctx, props, maze_data, created_objects, name_suffix):
    # Roof
    if not (props.is_editing and props.wall_mode == 'thin' and name_suffix != "_Collider" and not props.edit_roof):
        bm_roof, uv_roof, roof_materials = _create_bmesh_element("roof", ctx['materials'])
        rings = maze_data.polar_rings
        ring_sectors = maze_data.ring_sectors
        alignment = props.polar_custom_alignment

        for z in ctx['z_range']:
            z_off = z * ctx['wh']
            z_off_roof = z * ctx['wh'] + ctx['wh']
            level_cells = ctx['cells_3d'][z]
            for r in range(rings):
                Nr = ring_sectors[r]
                alpha_r = 2 * math.pi / Nr
                for theta in range(Nr):
                    if (z, r, theta) in ctx['stair_bottom_cells']:
                        continue
                    is_wall = level_cells[r][theta][0] if props.wall_mode == 'cube' else False
                    if props.wall_mode == 'cube':
                        if not is_wall:
                            continue
                        if props.cube_mode_pillar and (ctx['wall_meshes_list'] or ctx['custom_wall']):
                            continue
                            
                    if len(level_cells[r][theta]) >= 8:
                        roof_idx = level_cells[r][theta][5]
                    else:
                        roof_idx = level_cells[r][theta][5] if len(level_cells[r][theta]) > 5 else -1
                    
                    # Default uninitialized index (-1) to 0 when a collection is available so
                    # the custom mesh is used from the very first generation (no shift-click needed).
                    if roof_idx < 0 and ctx['roof_meshes_list']:
                        roof_idx = 0

                    src_roof = None
                    if ctx['roof_meshes_list'] and isinstance(roof_idx, int) and 0 <= roof_idx < len(ctx['roof_meshes_list']):
                        src_roof = ctx['roof_meshes_list'][roof_idx]
                    elif custom_roof := ctx['custom_roof']:
                        src_roof = custom_roof

                    if r == 0:
                        if src_roof and alignment != 'procedural':
                            rev_val = (props.wall_mode == 'thin')
                            _add_mesh_polar_center(bm_roof, src_roof, ctx['mat_roof_offset'], uv_roof, roof_materials, ctx['ts'], z_off_roof, ctx['centered'], reverse_faces=rev_val)
                        else:
                            _add_polar_center_fan(bm_roof, uv_roof, ctx['ts'], z_off_roof, is_roof=True, flip_normal=(props.wall_mode == 'thin'))
                    elif src_roof and alignment != 'procedural':
                        mat_flipped = ctx['mat_roof_offset'] @ Matrix.Scale(-1, 4, Vector((1, 0, 0)))
                        rev_val = (props.wall_mode == 'thin')
                        if alignment == 'trapezoid':
                            _add_mesh_polar_trapezoid(bm_roof, src_roof, mat_flipped, uv_roof, roof_materials, r, theta, Nr, ctx['ts'], z_off_roof, ctx['centered'], reverse_faces=rev_val)
                        elif alignment == 'bend':
                            N_max = ring_sectors[-1]
                            ratio = N_max // Nr if Nr > 0 else 1
                            cuts = max(1, ratio * 8 - 1)
                            _add_mesh_polar_bend(bm_roof, src_roof, mat_flipped, uv_roof, roof_materials, r, theta, Nr, ctx['ts'], z_off_roof, ctx['centered'], cuts=cuts, reverse_faces=rev_val)

                    else:
                        flip = props.wall_mode == 'thin'
                        _add_polar_floor_wedge(bm_roof, uv_roof, r, theta, Nr, ctx['ts'], z_off_roof, is_roof=True, flip_normal=flip)

        bmesh.ops.remove_doubles(bm_roof, verts=bm_roof.verts, dist=0.001)
        roof_obj = _create_object_from_bm(bm_roof, f"FireMaze_Roof{name_suffix}", ctx['col'], None)
        for mat in roof_materials:
            roof_obj.data.materials.append(mat)
        created_objects.append(roof_obj)


def _build_polar_stairs(ctx, props, maze_data, created_objects, name_suffix):
    # Stairs (polar mode)
    if maze_data.stairs:
        bm_stairs, uv_stairs, stair_materials = _create_bmesh_element("wall", ctx['materials'])
        ring_sectors = maze_data.ring_sectors
        for zstair in ctx['z_range']:
            z_offset = zstair * ctx['wh']
            for s in maze_data.stairs:
                if s.get('z') != zstair:
                    continue
                sx, sy = s.get('x', 0), s.get('y', 0)
                stheta, sr = sx, sy
                if sy >= maze_data.polar_rings and sx < maze_data.polar_rings:
                    stheta, sr = sy, sx
                sx, sy = stheta, sr
                style = s.get('type', 'stair')
                
                # Polar coordinates translation and rotation alignment
                Nr = ring_sectors[sy]
                alpha_r = 2 * math.pi / Nr
                theta_mid = (sx + 0.5) * alpha_r
                r_mid = sy * ctx['ts']
                cx = r_mid * math.cos(theta_mid)
                cy = r_mid * math.sin(theta_mid)
                
                orient = s.get('orientation', 'IN')
                extra_angle = 0.0
                if orient == 'IN':
                    extra_angle = math.pi / 2
                elif orient == 'OUT':
                    extra_angle = -math.pi / 2
                elif orient == 'CW':
                    extra_angle = math.pi
                elif orient == 'CCW':
                    extra_angle = 0.0
                
                combined_rot = Matrix.Rotation(theta_mid + extra_angle, 4, 'Z')
                mat = Matrix.Translation(Vector((cx, cy, z_offset))) @ combined_rot @ ctx['mat_floor_offset']
                
                if style == 'ramp' and ctx['custom_ramp_mesh'] and is_valid_ref(ctx['custom_ramp_mesh']):
                    _add_mesh_at(bm_stairs, ctx['custom_ramp_mesh'].data if ctx['custom_ramp_mesh'].type == 'MESH' else ctx['custom_ramp_mesh'], mat, uv_stairs, final_materials_list=stair_materials)
                elif style == 'stair' and ctx['custom_stair_mesh'] and is_valid_ref(ctx['custom_stair_mesh']):
                    _add_mesh_at(bm_stairs, ctx['custom_stair_mesh'].data if ctx['custom_stair_mesh'].type == 'MESH' else ctx['custom_stair_mesh'], mat, uv_stairs, final_materials_list=stair_materials)
                elif style == 'ramp':
                    _build_ramp_1x1(bm_stairs, uv_stairs, cx, cy, ctx['ts'], ctx['wh'], z_offset, combined_rot @ ctx['mat_floor_offset'])
                else:
                    _build_spiral_stair_1x1(bm_stairs, uv_stairs, cx, cy, ctx['ts'], ctx['wh'], z_offset, combined_rot @ ctx['mat_floor_offset'])
        if bm_stairs.verts:
            bmesh.ops.remove_doubles(bm_stairs, verts=bm_stairs.verts, dist=0.001)
            stair_obj = _create_object_from_bm(bm_stairs, f"FireMaze_Stairs{name_suffix}", ctx['col'], None)
            for mat in stair_materials:
                stair_obj.data.materials.append(mat)
            created_objects.append(stair_obj)
        else:
            bm_stairs.free()


def _build_polar_maze_objects_impl(props, maze_data, context, collection=None, force_simple=False, name_suffix=""):
    """Build all polar maze meshes (floor, walls, roof, stairs, guide path, colliders) into a collection."""

    ctx = _prepare_maze_building_context(props, maze_data, context, collection, force_simple)
    created_objects = []

    _build_polar_floor(ctx, props, maze_data, created_objects, name_suffix)
    _build_polar_walls(ctx, props, maze_data, created_objects, name_suffix)
    _build_polar_roof(ctx, props, maze_data, created_objects, name_suffix)
    _build_polar_stairs(ctx, props, maze_data, created_objects, name_suffix)

    # Build guide path if requested
    if not force_simple:
        guide_obj = _build_guide_path(props, maze_data, ctx['col'], ctx['materials'])
        if guide_obj:
            created_objects.append(guide_obj)

    if name_suffix == "_Collider":
        for obj in created_objects:
            obj.hide_render = True
            obj.display_type = 'WIRE'
    elif name_suffix == "_EditHelper":
        for obj in created_objects:
            obj.hide_render = True

    if props.remove_doubles:
        for obj in created_objects:
            _remove_doubles_on_obj(obj)

    if name_suffix == "_Collider":
        if props.merge_colliders:
            meshes_to_merge = [obj for obj in created_objects if obj.type == 'MESH']
            merged_obj = _merge_maze_objects(meshes_to_merge, context, name="FireMaze_Collider")
            if props.optimize_colliders_coplanar and merged_obj:
                _optimize_coplanar_on_obj(merged_obj)
        else:
            if props.optimize_colliders_coplanar:
                for obj in created_objects:
                    if obj.type == 'MESH':
                        _optimize_coplanar_on_obj(obj)
    elif name_suffix == "_EditHelper":
        meshes_to_merge = [obj for obj in created_objects if obj.type == 'MESH']
        merged_obj = _merge_maze_objects(meshes_to_merge, context, name="_FireMaze_Edit_Helper")
        if merged_obj:
            merged_obj.hide_viewport = True
            merged_obj.hide_render = True
    else:
        if props.merge_objects:
            meshes_to_merge = [obj for obj in created_objects if obj.type == 'MESH']
            _merge_maze_objects(meshes_to_merge, context, name="FireMaze_Merged")

    # Post-process
    if not name_suffix and not props.is_editing:
        visual_meshes = [obj for obj in ctx['col'].objects if obj.type == 'MESH']
        if props.optimize_coplanar:
            for obj in visual_meshes:
                _optimize_coplanar_on_obj(obj)
        if props.vertex_paint_enable:
            for obj in visual_meshes:
                _apply_vertex_painting_on_obj(obj, props, maze_data)
        if props.generate_lightmap:
            for obj in visual_meshes:
                _generate_lightmap_on_obj(obj, context, method=props.lightmap_method)
        _spawn_decorations(props, maze_data, context, ctx['col'])

    return ctx['col']



def _build_spiral_stair_1x1(bm, uv_layer, cx, cy, ts, wh, z_offset, mat_offset):
    """Procedural 1x1 spiral staircase: central post + 12 wedge steps, full 360° rotation, rising wh."""
    import math
    T_base = Matrix.Translation(Vector((cx, cy, 0))) @ mat_offset
    steps = 12
    rise_per_step = wh / steps
    angle_per_step = 2 * math.pi / steps
    post_r = max(0.02, ts * 0.08)
    step_span = ts * 0.48
    thickness = 0.02 * ts

    # Top exit landing platform on the +X side (exit side)
    # Extends from x = 0 to x = ts/2, and y = -ts/2 to y = ts/2 at height z_offset + wh
    p_plat = [
        T_base @ Vector((0, -ts/2, z_offset + wh - thickness)),
        T_base @ Vector((ts/2, -ts/2, z_offset + wh - thickness)),
        T_base @ Vector((ts/2, ts/2, z_offset + wh - thickness)),
        T_base @ Vector((0, ts/2, z_offset + wh - thickness)),
        T_base @ Vector((0, -ts/2, z_offset + wh)),
        T_base @ Vector((ts/2, -ts/2, z_offset + wh)),
        T_base @ Vector((ts/2, ts/2, z_offset + wh)),
        T_base @ Vector((0, ts/2, z_offset + wh)),
    ]

    # Platform Top Face
    v_top_plat = [bm.verts.new(p_plat[4]), bm.verts.new(p_plat[5]), bm.verts.new(p_plat[6]), bm.verts.new(p_plat[7])]
    f_top_plat = bm.faces.new(v_top_plat)
    for loop in f_top_plat.loops:
        loop[uv_layer].uv = (0.0, 0.0)

    # Platform Bottom Face
    v_bot_plat = [bm.verts.new(p_plat[3]), bm.verts.new(p_plat[2]), bm.verts.new(p_plat[1]), bm.verts.new(p_plat[0])]
    f_bot_plat = bm.faces.new(v_bot_plat)
    for loop in f_bot_plat.loops:
        loop[uv_layer].uv = (0.0, 0.0)

    # Platform Side Faces (South, East, North, West)
    sides = [
        [p_plat[0], p_plat[1], p_plat[5], p_plat[4]], # South
        [p_plat[1], p_plat[2], p_plat[6], p_plat[5]], # East
        [p_plat[2], p_plat[3], p_plat[7], p_plat[6]], # North
        [p_plat[3], p_plat[0], p_plat[4], p_plat[7]], # West
    ]
    for side_verts in sides:
        v_side = [bm.verts.new(pt) for pt in side_verts]
        f_side = bm.faces.new(v_side)
        for loop in f_side.loops:
            loop[uv_layer].uv = (0.0, 0.0)

    # Central post (starts at z_offset, extends to z_offset + wh)
    posts_verts = []
    segs = 8
    for i in range(segs):
        a = 2 * math.pi * i / segs
        posts_verts.append(T_base @ Vector((post_r * math.cos(a), post_r * math.sin(a), z_offset)))
        posts_verts.append(T_base @ Vector((post_r * math.cos(a), post_r * math.sin(a), z_offset + wh)))
    for i in range(segs):
        i0 = i * 2
        i1 = ((i + 1) % segs) * 2
        v = [bm.verts.new(posts_verts[i0]), bm.verts.new(posts_verts[i1]),
             bm.verts.new(posts_verts[i1 + 1]), bm.verts.new(posts_verts[i0 + 1])]
        f = bm.faces.new(v)
        for loop in f.loops:
            loop[uv_layer].uv = (0.5, 0.5)

    # Steps
    for i in range(steps):
        a_start = i * angle_per_step - math.pi / 2
        a_end = (i + 1) * angle_per_step - math.pi / 2
        z_step = z_offset + i * rise_per_step
        h_step = rise_per_step

        cos_s, sin_s = math.cos(a_start), math.sin(a_start)
        cos_e, sin_e = math.cos(a_end), math.sin(a_end)
        r_in = post_r
        r_out = step_span

        p0 = T_base @ Vector((r_in * cos_s, r_in * sin_s, z_step))
        p1 = T_base @ Vector((r_out * cos_s, r_out * sin_s, z_step))
        p2 = T_base @ Vector((r_out * cos_e, r_out * sin_e, z_step))
        p3 = T_base @ Vector((r_in * cos_e, r_in * sin_e, z_step))
        p4 = T_base @ Vector((r_in * cos_s, r_in * sin_s, z_step + h_step))
        p5 = T_base @ Vector((r_out * cos_s, r_out * sin_s, z_step + h_step))
        p6 = T_base @ Vector((r_out * cos_e, r_out * sin_e, z_step + h_step))
        p7 = T_base @ Vector((r_in * cos_e, r_in * sin_e, z_step + h_step))

        # Top face (Winding corrected to point UP)
        v_top = [bm.verts.new(p4), bm.verts.new(p5), bm.verts.new(p6), bm.verts.new(p7)]
        f_top = bm.faces.new(v_top)
        for loop in f_top.loops:
            loop[uv_layer].uv = ((i % 2), 0.5)

        # Bottom face (normal points DOWN)
        v_bot = [bm.verts.new(p0), bm.verts.new(p3), bm.verts.new(p2), bm.verts.new(p1)]
        f_bot = bm.faces.new(v_bot)
        for loop in f_bot.loops:
            loop[uv_layer].uv = (0, 0)

        # Outer riser
        v_outer = [bm.verts.new(p1), bm.verts.new(p2), bm.verts.new(p6), bm.verts.new(p5)]
        f_outer = bm.faces.new(v_outer)
        for loop in f_outer.loops:
            loop[uv_layer].uv = (0, 0)

        # Inner riser
        v_inner = [bm.verts.new(p3), bm.verts.new(p0), bm.verts.new(p4), bm.verts.new(p7)]
        f_inner = bm.faces.new(v_inner)
        for loop in f_inner.loops:
            loop[uv_layer].uv = (0, 0)

        # CW side (Start-angle face / back riser)
        v_cw = [bm.verts.new(p0), bm.verts.new(p1), bm.verts.new(p5), bm.verts.new(p4)]
        f_cw = bm.faces.new(v_cw)
        for loop in f_cw.loops:
            loop[uv_layer].uv = (0, 0)

        # CCW side (End-angle face / front riser)
        v_ccw = [bm.verts.new(p2), bm.verts.new(p3), bm.verts.new(p7), bm.verts.new(p6)]
        f_ccw = bm.faces.new(v_ccw)
        for loop in f_ccw.loops:
            loop[uv_layer].uv = (0, 0)


def _build_ramp_1x1(bm, uv_layer, cx, cy, ts, wh, z_offset, mat_offset):
    """Procedural 1x1 ramp: sloped quadrilateral with solid side panels."""
    T_base = Matrix.Translation(Vector((cx, cy, 0))) @ mat_offset
    t2 = ts / 2
    hw = wh

    # Ramp top surface: rises from z_offset to z_offset + wh (runs in +Y direction)
    p0 = T_base @ Vector((-t2, -t2, z_offset))
    p1 = T_base @ Vector((t2, -t2, z_offset))
    p2 = T_base @ Vector((t2, t2, z_offset + hw))
    p3 = T_base @ Vector((-t2, t2, z_offset + hw))

    # Bottom (hidden, faces down)
    p4 = T_base @ Vector((-t2, -t2, z_offset))
    p5 = T_base @ Vector((t2, -t2, z_offset))
    p6 = T_base @ Vector((t2, t2, z_offset))
    p7 = T_base @ Vector((-t2, t2, z_offset))

    # Ramp top face
    v = [bm.verts.new(p0), bm.verts.new(p1), bm.verts.new(p2), bm.verts.new(p3)]
    f = bm.faces.new(v)
    for loop in f.loops:
        loop[uv_layer].uv = (0, 0)

    # Bottom face
    v = [bm.verts.new(p7), bm.verts.new(p6), bm.verts.new(p5), bm.verts.new(p4)]
    f = bm.faces.new(v)
    for loop in f.loops:
        loop[uv_layer].uv = (0, 0)

    # Left side panel (solid wedge at x = -t2)
    p_back_bottom_L = T_base @ Vector((-t2, -t2, z_offset))
    p_front_bottom_L = T_base @ Vector((-t2, t2, z_offset))
    p_front_top_L = T_base @ Vector((-t2, t2, z_offset + hw))
    v = [bm.verts.new(p_back_bottom_L), bm.verts.new(p_front_top_L), bm.verts.new(p_front_bottom_L)]
    f = bm.faces.new(v)
    for loop in f.loops:
        loop[uv_layer].uv = (0, 0)

    # Right side panel (solid wedge at x = t2)
    p_back_bottom_R = T_base @ Vector((t2, -t2, z_offset))
    p_front_bottom_R = T_base @ Vector((t2, t2, z_offset))
    p_front_top_R = T_base @ Vector((t2, t2, z_offset + hw))
    v = [bm.verts.new(p_back_bottom_R), bm.verts.new(p_front_bottom_R), bm.verts.new(p_front_top_R)]
    f = bm.faces.new(v)
    for loop in f.loops:
        loop[uv_layer].uv = (0, 0)

    # Back face (vertical wall at y = t2)
    v_back = [bm.verts.new(p_front_bottom_R), bm.verts.new(p_front_bottom_L), bm.verts.new(p_front_top_L), bm.verts.new(p_front_top_R)]
    f_back = bm.faces.new(v_back)
    for loop in f_back.loops:
        loop[uv_layer].uv = (0, 0)




def build_maze_objects(props, maze_data, context, collection=None, force_simple=False, name_suffix=""):
    """Top-level entry point for maze mesh construction.

    Manages the BMesh cache lifecycle and dispatches to the appropriate
    polar or rectangular builder.
    """
    with _bmesh_cache_context():
        return build_maze_objects_impl(props, maze_data, context, collection, force_simple, name_suffix)



def _build_rect_cube_floor(ctx, maze_data, created_objects, name_suffix):
    # Floor
    bm_floor, uv_floor, floor_materials = _create_bmesh_element("floor", ctx['materials'])
    for z in ctx['z_range']:
        z_off = z * ctx['wh']
        level_cells = ctx['cells_3d'][z]
        for y in range(maze_data.depth):
            for x in range(maze_data.width):
                if (z, y, x) in ctx['stair_top_cells']:
                    continue
                is_wall = level_cells[y][x][0]
                if not is_wall:
                    floor_idx = level_cells[y][x][5] if len(level_cells[y][x]) > 5 else -1
                    if ctx['floor_meshes_list'] and isinstance(floor_idx, int) and 0 <= floor_idx < len(ctx['floor_meshes_list']):
                        off = ctx['ts'] / 2 if ctx['centered'] else 0
                        mat_base = Matrix.Translation(Vector((x * ctx['ts'] + off, y * ctx['ts'] + off, z_off)))
                        mat = mat_base @ ctx['mat_floor_offset']
                        _add_mesh_at(bm_floor, ctx['floor_meshes_list'][floor_idx], mat, uv_floor, final_materials_list=floor_materials)
                    elif ctx['custom_floor']:
                        off = ctx['ts'] / 2 if ctx['centered'] else 0
                        mat_base = Matrix.Translation(Vector((x * ctx['ts'] + off, y * ctx['ts'] + off, z_off)))
                        mat = mat_base @ ctx['mat_floor_offset']
                        _add_mesh_at(bm_floor, ctx['custom_floor'], mat, uv_floor, final_materials_list=floor_materials)
                    else:
                        _add_floor_tile_transformed(bm_floor, uv_floor, x, y, ctx['ts'], ctx['mat_floor_offset'], z_offset=z_off)
    floor_obj = _create_object_from_bm(bm_floor, f"FireMaze_Floor{name_suffix}", ctx['col'], None)
    for mat in floor_materials:
        floor_obj.data.materials.append(mat)
    created_objects.append(floor_obj)


def _build_rect_cube_walls(ctx, props, maze_data, created_objects, name_suffix):
    # Walls
    bm_wall, uv_wall, wall_materials = _create_bmesh_element("wall", ctx['materials'])
    cent = Matrix.Translation(Vector((-ctx['ts'] / 2, -ctx['ts'] / 2, 0))) if not ctx['centered'] else Matrix.Identity(4)
    wall_rng = _real_random.Random(props.seed if props.seed else None)
    for z in ctx['z_range']:
        z_off_floor = z * ctx['wh']
        level_cells = ctx['cells_3d'][z]
        for level in range(ctx['tiles_high']):
            z_off = z_off_floor + level * ctx['seg_h']
            hw = z_off + ctx['seg_h'] / 2
            for y in range(maze_data.depth):
                for x in range(maze_data.width):
                    is_wall = level_cells[y][x][0]
                    if is_wall:
                        cx, cy = x * ctx['ts'], y * ctx['ts']
                        wall_idx = level_cells[y][x][1] if len(level_cells[y][x]) > 1 else -1
                        
                        if props.cube_mode_pillar and ctx['wall_meshes_list']:
                            # Instanced Pillar Mode
                            src_mesh = None
                            if isinstance(wall_idx, int) and 0 <= wall_idx < len(ctx['wall_meshes_list']):
                                src_mesh = ctx['wall_meshes_list'][wall_idx]
                            else:
                                src_mesh = wall_rng.choice(ctx['wall_meshes_list'])
                            
                            mat_base = Matrix.Translation(Vector((cx + ctx['ts'] / 2, cy + ctx['ts'] / 2, z_off))) @ cent
                            mat = mat_base @ ctx['mat_wall_offset']
                            _add_mesh_at(bm_wall, src_mesh, mat, uv_wall, final_materials_list=wall_materials)
                        else:
                            # Face Assembled Mode
                            def place_wall_face(direction, offset_rot, custom_mesh_fallback, f_idx):
                                """Place a single wall face using a custom mesh or procedurally generated geometry."""
                                if ctx['wall_meshes_list']:
                                    src_mesh = None
                                    if isinstance(f_idx, int) and 0 <= f_idx < len(ctx['wall_meshes_list']):
                                        src_mesh = ctx['wall_meshes_list'][f_idx]
                                    else:
                                        src_mesh = wall_rng.choice(ctx['wall_meshes_list'])
                                    mat_base = Matrix.Translation(Vector((cx + ctx['ts'] / 2, cy + ctx['ts'] / 2, hw))) @ offset_rot @ cent
                                    mat = mat_base @ ctx['mat_wall_offset']
                                    _add_mesh_at(bm_wall, src_mesh, mat, uv_wall, final_materials_list=wall_materials)
                                elif custom_mesh_fallback:
                                    mat_base = Matrix.Translation(Vector((cx + ctx['ts'] / 2, cy + ctx['ts'] / 2, hw))) @ offset_rot @ cent
                                    mat = mat_base @ ctx['mat_wall_offset']
                                    _add_mesh_at(bm_wall, custom_mesh_fallback, mat, uv_wall, final_materials_list=wall_materials)
                                else:
                                    _add_wall_face_transformed(bm_wall, uv_wall, cx, cy, ctx['ts'], ctx['seg_h'], direction, ctx['mat_wall_offset'], z_base=z_off)

                            # +Y (north)
                            if y + 1 >= maze_data.depth or not level_cells[y + 1][x][0]:
                                f_idx = level_cells[y][x][1] if len(level_cells[y][x]) > 1 else -1
                                place_wall_face('+Y', Matrix.Translation(Vector((0, ctx['ts']/2, 0))) @ Matrix.Rotation(math.radians(-90), 4, 'X') @ Matrix.Rotation(math.radians(180), 4, 'Z'), ctx['custom_wall'], f_idx)
                            # -Y (south)
                            if y - 1 < 0 or not level_cells[y - 1][x][0]:
                                f_idx = level_cells[y][x][2] if len(level_cells[y][x]) > 2 else -1
                                place_wall_face('-Y', Matrix.Translation(Vector((0, -ctx['ts']/2, 0))) @ Matrix.Rotation(math.radians(90), 4, 'X'), ctx['custom_wall'], f_idx)
                            # +X (east)
                            if x + 1 >= maze_data.width or not level_cells[y][x + 1][0]:
                                f_idx = level_cells[y][x][3] if len(level_cells[y][x]) > 3 else -1
                                place_wall_face('+X', Matrix.Translation(Vector((ctx['ts']/2, 0, 0))) @ Matrix.Rotation(math.radians(-90), 4, 'Z') @ Matrix.Rotation(math.radians(-90), 4, 'X') @ Matrix.Rotation(math.radians(180), 4, 'Z'), ctx['custom_wall'], f_idx)
                            # -X (west)
                            if x - 1 < 0 or not level_cells[y][x - 1][0]:
                                f_idx = level_cells[y][x][4] if len(level_cells[y][x]) > 4 else -1
                                place_wall_face('-X', Matrix.Translation(Vector((-ctx['ts']/2, 0, 0))) @ Matrix.Rotation(math.radians(90), 4, 'Z') @ Matrix.Rotation(math.radians(-90), 4, 'X') @ Matrix.Rotation(math.radians(180), 4, 'Z'), ctx['custom_wall'], f_idx)

    _safe_remove_doubles(bm_wall, dist=0.001)
    wall_obj = _create_object_from_bm(bm_wall, f"FireMaze_Walls{name_suffix}", ctx['col'], None)
    for mat in wall_materials:
        wall_obj.data.materials.append(mat)
    created_objects.append(wall_obj)


def _build_rect_cube_roof(ctx, props, maze_data, created_objects, name_suffix):
    # Roof
    if not props.cube_mode_pillar or name_suffix in {"_EditHelper", "_Collider"}:
        bm_roof, uv_roof, roof_materials = _create_bmesh_element("roof", ctx['materials'])
        for z in ctx['z_range']:
            z_off = z * ctx['wh']
            level_cells = ctx['cells_3d'][z]
            for y in range(maze_data.depth):
                for x in range(maze_data.width):
                    if (z, y, x) in ctx['stair_bottom_cells']:
                        continue
                    is_wall = level_cells[y][x][0]
                    if is_wall:
                        roof_idx = level_cells[y][x][6] if len(level_cells[y][x]) > 6 else -1
                        if ctx['roof_meshes_list'] and isinstance(roof_idx, int) and 0 <= roof_idx < len(ctx['roof_meshes_list']):
                            off = ctx['ts'] / 2 if ctx['centered'] else 0
                            mat_base = Matrix.Translation(Vector((x * ctx['ts'] + off, y * ctx['ts'] + off, z_off + ctx['wh'])))
                            mat = mat_base @ ctx['mat_roof_offset']
                            _add_mesh_at(bm_roof, ctx['roof_meshes_list'][roof_idx], mat, uv_roof, final_materials_list=roof_materials)
                        elif ctx['custom_roof']:
                            off = ctx['ts'] / 2 if ctx['centered'] else 0
                            mat_base = Matrix.Translation(Vector((x * ctx['ts'] + off, y * ctx['ts'] + off, z_off + ctx['wh'])))
                            mat = mat_base @ ctx['mat_roof_offset']
                            _add_mesh_at(bm_roof, ctx['custom_roof'], mat, uv_roof, final_materials_list=roof_materials)
                        else:
                            _add_cube_roof_face_transformed(bm_roof, uv_roof, x * ctx['ts'] + ctx['ts'] / 2, y * ctx['ts'] + ctx['ts'] / 2, ctx['ts'], ctx['ts'], z_off + ctx['wh'], ctx['mat_roof_offset'])

        if not ctx['custom_roof'] and not ctx['roof_meshes_list']:
            bmesh.ops.remove_doubles(bm_roof, verts=bm_roof.verts, dist=0.001)
        roof_obj = _create_object_from_bm(bm_roof, f"FireMaze_Roof{name_suffix}", ctx['col'], None)
        for mat in roof_materials:
            roof_obj.data.materials.append(mat)
        created_objects.append(roof_obj)


def _build_rect_stairs(ctx, props, maze_data, created_objects, name_suffix):
    # Stairs (cube & thin modes share identical building structure)
    if maze_data.stairs:
        bm_stairs, uv_stairs, stair_materials = _create_bmesh_element("wall", ctx['materials'])
        for zstair in ctx['z_range']:
            z_offset = zstair * ctx['wh']
            for s in maze_data.stairs:
                if s.get('z') != zstair:
                    continue
                sx, sy = s.get('x', 0), s.get('y', 0)
                style = s.get('type', 'stair')
                cx = sx * ctx['ts'] + ctx['ts'] / 2
                cy = sy * ctx['ts'] + ctx['ts'] / 2
                orient = s.get('orientation', 'N')
                rot_angle = 0.0
                if orient == 'E':
                    rot_angle = -math.pi / 2
                elif orient == 'S':
                    rot_angle = math.pi
                elif orient == 'W':
                    rot_angle = math.pi / 2
                rot_mat = Matrix.Rotation(rot_angle, 4, 'Z')

                if style == 'ramp' and ctx['custom_ramp_mesh'] and is_valid_ref(ctx['custom_ramp_mesh']):
                    off = ctx['ts'] / 2 if ctx['centered'] else 0
                    mat = Matrix.Translation(Vector((sx * ctx['ts'] + off, sy * ctx['ts'] + off, z_offset))) @ rot_mat @ ctx['mat_floor_offset']
                    _add_mesh_at(bm_stairs, ctx['custom_ramp_mesh'].data if ctx['custom_ramp_mesh'].type == 'MESH' else ctx['custom_ramp_mesh'], mat, uv_stairs, final_materials_list=stair_materials)
                elif style == 'stair' and ctx['custom_stair_mesh'] and is_valid_ref(ctx['custom_stair_mesh']):
                    off = ctx['ts'] / 2 if ctx['centered'] else 0
                    mat = Matrix.Translation(Vector((sx * ctx['ts'] + off, sy * ctx['ts'] + off, z_offset))) @ rot_mat @ ctx['mat_floor_offset']
                    _add_mesh_at(bm_stairs, ctx['custom_stair_mesh'].data if ctx['custom_stair_mesh'].type == 'MESH' else ctx['custom_stair_mesh'], mat, uv_stairs, final_materials_list=stair_materials)
                elif style == 'ramp':
                    _build_ramp_1x1(bm_stairs, uv_stairs, cx, cy, ctx['ts'], ctx['wh'], z_offset, rot_mat @ ctx['mat_floor_offset'])
                else:
                    _build_spiral_stair_1x1(bm_stairs, uv_stairs, cx, cy, ctx['ts'], ctx['wh'], z_offset, rot_mat @ ctx['mat_floor_offset'])
        if bm_stairs.verts:
            bmesh.ops.remove_doubles(bm_stairs, verts=bm_stairs.verts, dist=0.001)
            stair_obj = _create_object_from_bm(bm_stairs, f"FireMaze_Stairs{name_suffix}", ctx['col'], None)
            for mat in stair_materials:
                stair_obj.data.materials.append(mat)
            created_objects.append(stair_obj)
        else:
            bm_stairs.free()


def _build_rect_thin_floor(ctx, maze_data, created_objects, name_suffix):
    # Floor (all levels combined into one BMesh)
    bm_floor, uv_floor, floor_materials = _create_bmesh_element("floor", ctx['materials'])
    off = ctx['ts'] / 2 if ctx['centered'] else 0
    for z in ctx['z_range']:
        stair_top_cells_z = {(yy, xx) for (zz, yy, xx) in ctx['stair_top_cells'] if zz == z}
        level_cells = ctx['cells_3d'][z]
        for y in range(maze_data.depth):
            for x in range(maze_data.width):
                if (y, x) in stair_top_cells_z:
                    continue
                if len(level_cells[y][x]) > 8:
                    floor_idx = level_cells[y][x][8]
                else:
                    floor_idx = -1
                if floor_idx == -2:
                    continue
                if ctx['floor_meshes_list'] and isinstance(floor_idx, int) and 0 <= floor_idx < len(ctx['floor_meshes_list']):
                    mat_base = Matrix.Translation(Vector((x * ctx['ts'] + off, y * ctx['ts'] + off, z * ctx['wh'])))
                    mat = mat_base @ ctx['mat_floor_offset']
                    _add_mesh_at(bm_floor, ctx['floor_meshes_list'][floor_idx], mat, uv_floor, final_materials_list=floor_materials)
                elif ctx['custom_floor']:
                    mat_base = Matrix.Translation(Vector((x * ctx['ts'] + off, y * ctx['ts'] + off, z * ctx['wh'])))
                    mat = mat_base @ ctx['mat_floor_offset']
                    _add_mesh_at(bm_floor, ctx['custom_floor'], mat, uv_floor, final_materials_list=floor_materials)
                else:
                    _add_floor_tile_transformed(bm_floor, uv_floor, x, y, ctx['ts'], ctx['mat_floor_offset'], z_offset=z * ctx['wh'])
    floor_obj = _create_object_from_bm(bm_floor, f"FireMaze_Floor{name_suffix}", ctx['col'], None)
    for mat in floor_materials:
        floor_obj.data.materials.append(mat)
    created_objects.append(floor_obj)


def _build_rect_thin_walls(ctx, props, maze_data, created_objects, name_suffix):
    # Walls and caps
    bm_wall, uv_wall, wall_materials = _create_bmesh_element("wall", ctx['materials'])
    bm_cap, uv_cap, cap_materials = _create_bmesh_element("end_cap", ctx['materials'])

    has_any_wall_custom = (ctx['custom_wall'] or ctx['wall_meshes_list'])
    cent = Matrix.Translation(Vector((-ctx['ts'] / 2, -ctx['ts'] / 2, 0))) if not ctx['centered'] else Matrix.Identity(4)
    tw = ctx['wt'] / 2
    wall_rng = _real_random.Random(props.seed if props.seed else None)

    for z in ctx['z_range']:
        level_cells = ctx['cells_3d'][z]
        actual_segments = _get_wall_segments(maze_data, level_cells)
        if name_suffix == "_EditHelper":
            segments = []
            for y in range(maze_data.depth + 1):
                for x in range(maze_data.width):
                    segments.append(('H', x, y))
            for y in range(maze_data.depth):
                for x in range(maze_data.width + 1):
                    segments.append(('V', x, y))
        else:
            segments = list(actual_segments)
        h_positions = set()
        v_positions = set()
        h_endpoints = set()
        for seg_type, a, b in segments:
            if seg_type == 'H':
                h_positions.add((a, b))
                h_endpoints.add((a, b))
                h_endpoints.add((a + 1, b))
            else:
                v_positions.add((a, b))

        for level in range(ctx['tiles_high']):
            z_off = z * ctx['wh'] + level * ctx['seg_h']

            for seg_type, a, b in segments:
                is_active_wall = (seg_type, a, b) in actual_segments
                sh = ctx['seg_h']
                if name_suffix == "_EditHelper" and not is_active_wall:
                    sh = 0.02 * ctx['ts']
                hw = z_off + sh / 2
                wall_idx = -1
                if seg_type == 'H':
                    if len(level_cells[0][0]) > 8:
                        if b < maze_data.depth:
                            wall_idx = level_cells[b][a][5] if len(level_cells[b][a]) > 5 else -1
                        else:
                            wall_idx = level_cells[b - 1][a][4] if len(level_cells[b - 1][a]) > 4 else -1
                    else:
                        if b < maze_data.depth and a < maze_data.width:
                            wall_idx = level_cells[b][a][4] if len(level_cells[b][a]) > 4 else -1
                        else:
                            # Boundary wall fallback for 8-item layout
                            wall_idx = a % len(ctx['wall_meshes_list']) if len(ctx['wall_meshes_list']) > 0 else -1
                else:
                    if len(level_cells[0][0]) > 8:
                        if a < maze_data.width:
                            wall_idx = level_cells[b][a][7] if len(level_cells[b][a]) > 7 else -1
                        else:
                            wall_idx = level_cells[b][a - 1][6] if len(level_cells[b][a - 1]) > 6 else -1
                    else:
                        if a < maze_data.width and b < maze_data.depth:
                            wall_idx = level_cells[b][a][5] if len(level_cells[b][a]) > 5 else -1
                        else:
                            # Boundary wall fallback for 8-item layout
                            wall_idx = b % len(ctx['wall_meshes_list']) if len(ctx['wall_meshes_list']) > 0 else -1

                if seg_type == 'H':
                    x0, x1 = a * ctx['ts'], (a + 1) * ctx['ts']
                    yc = b * ctx['ts']
                    
                    def add_horizontal_face(direction, offset_rot, custom_mesh_fallback, y_offset, uvs_standard, local_pts):
                        """Place a horizontal wall face (along Y) using a custom mesh or procedural geometry."""
                        if ctx['wall_meshes_list']:
                            src_mesh = None
                            if isinstance(wall_idx, int) and 0 <= wall_idx < len(ctx['wall_meshes_list']):
                                src_mesh = ctx['wall_meshes_list'][wall_idx]
                            else:
                                src_mesh = wall_rng.choice(ctx['wall_meshes_list'])
                            mat_base = Matrix.Translation(Vector((x0 + ctx['ts'] / 2, yc + y_offset, hw))) @ offset_rot @ cent
                            mat = mat_base @ ctx['mat_wall_offset']
                            _add_mesh_at(bm_wall, src_mesh, mat, uv_wall, final_materials_list=wall_materials)
                        elif custom_mesh_fallback:
                            mat_base = Matrix.Translation(Vector((x0 + ctx['ts'] / 2, yc + y_offset, hw))) @ offset_rot @ cent
                            mat = mat_base @ ctx['mat_wall_offset']
                            _add_mesh_at(bm_wall, custom_mesh_fallback, mat, uv_wall, final_materials_list=wall_materials)
                        else:
                            T = Matrix.Translation(Vector((x0 + ctx['ts']/2, yc, hw))) @ ctx['mat_wall_offset']
                            verts = [bm_wall.verts.new(T @ Vector(p)) for p in local_pts]
                            f = bm_wall.faces.new(verts)
                            for loop, uv in zip(f.loops, uvs_standard):
                                loop[uv_wall].uv = uv

                    if not has_any_wall_custom:
                        # North face (+Y)
                        add_horizontal_face('+Y', Matrix.Rotation(math.radians(-90), 4, 'X'), None, tw, 
                                            [(0,0),(1,0),(1,1),(0,1)],
                                            [(ctx['ts']/2, tw, -sh/2), (-ctx['ts']/2, tw, -sh/2), (-ctx['ts']/2, tw, sh/2), (ctx['ts']/2, tw, sh/2)])
                        # South face (-Y)
                        add_horizontal_face('-Y', Matrix.Rotation(math.radians(90), 4, 'X'), None, -tw, 
                                            [(0,0),(1,0),(1,1),(0,1)],
                                            [(-ctx['ts']/2, -tw, -sh/2), (ctx['ts']/2, -tw, -sh/2), (ctx['ts']/2, -tw, sh/2), (-ctx['ts']/2, -tw, sh/2)])
                        
                        # West end-cap
                        if (a - 1, b) not in h_positions:
                            T = Matrix.Translation(Vector((x0 + ctx['ts']/2, yc, hw))) @ ctx['mat_wall_offset']
                            v_pts = [T @ Vector(p) for p in [(-ctx['ts']/2, tw, -sh/2), (-ctx['ts']/2, -tw, -sh/2), (-ctx['ts']/2, -tw, sh/2), (-ctx['ts']/2, tw, sh/2)]]
                            f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                            for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                loop[uv_cap].uv = uv
                        # East end-cap
                        if (a + 1, b) not in h_positions:
                            T = Matrix.Translation(Vector((x0 + ctx['ts']/2, yc, hw))) @ ctx['mat_wall_offset']
                            v_pts = [T @ Vector(p) for p in [(ctx['ts']/2, -tw, -sh/2), (ctx['ts']/2, tw, -sh/2), (ctx['ts']/2, tw, sh/2), (ctx['ts']/2, -tw, sh/2)]]
                            f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                            for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                loop[uv_cap].uv = uv
                    else:
                        if props.thin_wall_double_sided:
                            # North face (+Y)
                            add_horizontal_face('+Y', Matrix.Rotation(math.radians(-90), 4, 'X') @ Matrix.Rotation(math.radians(180), 4, 'Z'), ctx['custom_wall'], tw,
                                                [(0,0),(1,0),(1,1),(0,1)],
                                                [(ctx['ts']/2, tw, -sh/2), (-ctx['ts']/2, tw, -sh/2), (-ctx['ts']/2, tw, sh/2), (ctx['ts']/2, tw, sh/2)])
                            # South face (-Y)
                            add_horizontal_face('-Y', Matrix.Rotation(math.radians(90), 4, 'X'), ctx['custom_wall'], -tw, 
                                                [(0,0),(1,0),(1,1),(0,1)],
                                                [(-ctx['ts']/2, -tw, -sh/2), (ctx['ts']/2, -tw, -sh/2), (ctx['ts']/2, -tw, sh/2), (-ctx['ts']/2, -tw, sh/2)])
                            
                            # West end-cap
                            if (a - 1, b) not in h_positions:
                                T = Matrix.Translation(Vector((x0 + ctx['ts']/2, yc, hw))) @ ctx['mat_wall_offset']
                                v_pts = [T @ Vector(p) for p in [(-ctx['ts']/2, tw, -sh/2), (-ctx['ts']/2, -tw, -sh/2), (-ctx['ts']/2, -tw, sh/2), (-ctx['ts']/2, tw, sh/2)]]
                                f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                                for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                    loop[uv_cap].uv = uv
                            # East end-cap
                            if (a + 1, b) not in h_positions:
                                T = Matrix.Translation(Vector((x0 + ctx['ts']/2, yc, hw))) @ ctx['mat_wall_offset']
                                v_pts = [T @ Vector(p) for p in [(ctx['ts']/2, -tw, -sh/2), (ctx['ts']/2, tw, -sh/2), (ctx['ts']/2, tw, sh/2), (ctx['ts']/2, -tw, sh/2)]]
                                f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                                for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                    loop[uv_cap].uv = uv
                        else:
                            # Single centered face at 0.0 offset (and no caps)
                            add_horizontal_face('+Y', Matrix.Rotation(math.radians(-90), 4, 'X') @ Matrix.Rotation(math.radians(180), 4, 'Z'), ctx['custom_wall'], 0.0, 
                                                [(0,0),(1,0),(1,1),(0,1)],
                                                [(ctx['ts']/2, 0.0, -sh/2), (-ctx['ts']/2, 0.0, -sh/2), (-ctx['ts']/2, 0.0, sh/2), (ctx['ts']/2, 0.0, sh/2)])
     
                else:
                    xc = a * ctx['ts']
                    y0, y1 = b * ctx['ts'], (b + 1) * ctx['ts']
                    
                    def add_vertical_face(direction, offset_rot, custom_mesh_fallback, x_offset, uvs_standard, local_pts):
                        """Place a vertical wall face (along X) using a custom mesh or procedural geometry."""
                        if ctx['wall_meshes_list']:
                            src_mesh = None
                            if isinstance(wall_idx, int) and 0 <= wall_idx < len(ctx['wall_meshes_list']):
                                src_mesh = ctx['wall_meshes_list'][wall_idx]
                            else:
                                src_mesh = wall_rng.choice(ctx['wall_meshes_list'])
                            mat_base = Matrix.Translation(Vector((xc + x_offset, y0 + ctx['ts'] / 2, hw))) @ offset_rot @ cent
                            mat = mat_base @ ctx['mat_wall_offset']
                            _add_mesh_at(bm_wall, src_mesh, mat, uv_wall, final_materials_list=wall_materials)
                        elif custom_mesh_fallback:
                            mat_base = Matrix.Translation(Vector((xc + x_offset, y0 + ctx['ts'] / 2, hw))) @ offset_rot @ cent
                            mat = mat_base @ ctx['mat_wall_offset']
                            _add_mesh_at(bm_wall, custom_mesh_fallback, mat, uv_wall, final_materials_list=wall_materials)
                        else:
                            T = Matrix.Translation(Vector((xc, y0 + ctx['ts']/2, hw))) @ ctx['mat_wall_offset']
                            verts = [bm_wall.verts.new(T @ Vector(p)) for p in local_pts]
                            f = bm_wall.faces.new(verts)
                            for loop, uv in zip(f.loops, uvs_standard):
                                loop[uv_wall].uv = uv
     
                    if not has_any_wall_custom:
                        # East face (+X)
                        add_vertical_face('+X', Matrix.Rotation(math.radians(-90), 4, 'Z') @ Matrix.Rotation(math.radians(-90), 4, 'X') @ Matrix.Rotation(math.radians(180), 4, 'Z'), None, tw, 
                                          [(0,0),(1,0),(1,1),(0,1)],
                                          [(tw, -ctx['ts']/2, -sh/2), (tw, ctx['ts']/2, -sh/2), (tw, ctx['ts']/2, sh/2), (tw, -ctx['ts']/2, sh/2)])
                        # West face (-X)
                        add_vertical_face('-X', Matrix.Rotation(math.radians(-90), 4, 'Y'), None, -tw, 
                                          [(1,0),(0,0),(0,1),(1,1)],
                                          [(-tw, ctx['ts']/2, -sh/2), (-tw, -ctx['ts']/2, -sh/2), (-tw, -ctx['ts']/2, sh/2), (-tw, ctx['ts']/2, sh/2)])
     
                        # South end-cap
                        if (a, b - 1) not in v_positions:
                            T = Matrix.Translation(Vector((xc, y0 + ctx['ts']/2, hw))) @ ctx['mat_wall_offset']
                            v_pts = [T @ Vector(p) for p in [(-tw, -ctx['ts']/2, -sh/2), (tw, -ctx['ts']/2, -sh/2), (tw, -ctx['ts']/2, sh/2), (-tw, -ctx['ts']/2, sh/2)]]
                            f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                            for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                loop[uv_cap].uv = uv
                        # North end-cap
                        if (a, b + 1) not in v_positions:
                            T = Matrix.Translation(Vector((xc, y0 + ctx['ts']/2, hw))) @ ctx['mat_wall_offset']
                            v_pts = [T @ Vector(p) for p in [(tw, ctx['ts']/2, -sh/2), (-tw, ctx['ts']/2, -sh/2), (-tw, ctx['ts']/2, sh/2), (tw, ctx['ts']/2, sh/2)]]
                            f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                            for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                loop[uv_cap].uv = uv
                    else:
                        if props.thin_wall_double_sided:
                            # East face (+X)
                            add_vertical_face('+X', Matrix.Rotation(math.radians(90), 4, 'X') @ Matrix.Rotation(math.radians(90), 4, 'Y'), ctx['custom_wall'], tw,
                                              [(0,0),(1,0),(1,1),(0,1)],
                                              [(tw, -ctx['ts']/2, -sh/2), (tw, ctx['ts']/2, -sh/2), (tw, ctx['ts']/2, sh/2), (tw, -ctx['ts']/2, sh/2)])
                            # West face (-X)
                            add_vertical_face('-X', Matrix.Rotation(math.radians(90), 4, 'X') @ Matrix.Rotation(math.radians(-90), 4, 'Y'), ctx['custom_wall'], -tw,
                                              [(1,0),(0,0),(0,1),(1,1)],
                                              [(-tw, ctx['ts']/2, -sh/2), (-tw, -ctx['ts']/2, -sh/2), (-tw, -ctx['ts']/2, sh/2), (-tw, ctx['ts']/2, sh/2)])
                            
                            # South end-cap
                            if (a, b - 1) not in v_positions:
                                T = Matrix.Translation(Vector((xc, y0 + ctx['ts']/2, hw))) @ ctx['mat_wall_offset']
                                v_pts = [T @ Vector(p) for p in [(-tw, -ctx['ts']/2, -sh/2), (tw, -ctx['ts']/2, -sh/2), (tw, -ctx['ts']/2, sh/2), (-tw, -ctx['ts']/2, sh/2)]]
                                f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                                for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                    loop[uv_cap].uv = uv
                            # North end-cap
                            if (a, b + 1) not in v_positions:
                                T = Matrix.Translation(Vector((xc, y0 + ctx['ts']/2, hw))) @ ctx['mat_wall_offset']
                                v_pts = [T @ Vector(p) for p in [(tw, ctx['ts']/2, -sh/2), (-tw, ctx['ts']/2, -sh/2), (-tw, ctx['ts']/2, sh/2), (tw, ctx['ts']/2, sh/2)]]
                                f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                                for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                    loop[uv_cap].uv = uv
                        else:
                            # Single centered face at 0.0 offset (and no caps)
                            add_vertical_face('+X', Matrix.Rotation(math.radians(-90), 4, 'Z') @ Matrix.Rotation(math.radians(-90), 4, 'X') @ Matrix.Rotation(math.radians(180), 4, 'Z'), ctx['custom_wall'], 0.0,
                                              [(0,0),(1,0),(1,1),(0,1)],
                                              [(0.0, -ctx['ts']/2, -sh/2), (0.0, ctx['ts']/2, -sh/2), (0.0, ctx['ts']/2, sh/2), (0.0, -ctx['ts']/2, sh/2)])


    # Handle single wall object merge of caps
    if props.single_wall_object and bm_cap.verts:
        vert_map = {}
        for v in bm_cap.verts:
            new_v = bm_wall.verts.new(v.co)
            vert_map[v] = new_v
        if ctx['materials']["end_cap"] not in wall_materials:
            wall_materials.append(ctx['materials']["end_cap"])
        cap_mat_idx = wall_materials.index(ctx['materials']["end_cap"])
        
        for f in bm_cap.faces:
            new_verts = [vert_map[v] for v in f.verts]
            new_f = bm_wall.faces.new(new_verts)
            new_f.material_index = cap_mat_idx
            uv_cap_layer = bm_cap.loops.layers.uv.active
            uv_wall_layer = bm_wall.loops.layers.uv.active
            if uv_cap_layer and uv_wall_layer:
                for l_cap, l_wall in zip(f.loops, new_f.loops):
                    l_wall[uv_wall_layer].uv = l_cap[uv_cap_layer].uv
        bm_cap.free()
        wall_obj = _create_object_from_bm(bm_wall, f"FireMaze_Walls{name_suffix}", ctx['col'], None)
        for mat in wall_materials:
            wall_obj.data.materials.append(mat)
        created_objects.append(wall_obj)
    else:
        wall_obj = _create_object_from_bm(bm_wall, f"FireMaze_Walls{name_suffix}", ctx['col'], None)
        for mat in wall_materials:
            wall_obj.data.materials.append(mat)
        created_objects.append(wall_obj)
        if bm_cap.verts:
            cap_obj = _create_object_from_bm(bm_cap, f"FireMaze_WallEndCaps{name_suffix}", ctx['col'], None)
            for mat in cap_materials:
                cap_obj.data.materials.append(mat)
            created_objects.append(cap_obj)
        else:
            bm_cap.free()


def _build_rect_thin_roof(ctx, maze_data, created_objects, name_suffix):
    # Roof
    if name_suffix != "_EditHelper":
        bm_roof, uv_roof, roof_materials = _create_bmesh_element("roof", ctx['materials'])
        for z in ctx['z_range']:
            level_cells = ctx['cells_3d'][z]
            if name_suffix == "_EditHelper":
                segments = []
                for y in range(maze_data.depth + 1):
                    for x in range(maze_data.width):
                        segments.append(('H', x, y))
                for y in range(maze_data.depth):
                    for x in range(maze_data.width + 1):
                        segments.append(('V', x, y))
            else:
                segments = list(_get_wall_segments(maze_data, level_cells))
            h_positions = set()
            v_positions = set()
            h_endpoints = set()
            for seg_type, a, b in segments:
                if seg_type == 'H':
                    h_positions.add((a, b))
                    h_endpoints.add((a, b))
                    h_endpoints.add((a + 1, b))
                else:
                    v_positions.add((a, b))
            sz = z * ctx['wh'] + ctx['wh']
            if ctx['custom_roof'] or ctx['roof_meshes_list']:
                for seg_type, a, b in segments:
                    if seg_type == 'H':
                        cx, cy = a * ctx['ts'] + ctx['ts'] / 2, b * ctx['ts']
                        if len(level_cells[0][0]) > 8:
                            target_y = min(b, maze_data.depth - 1)
                            roof_idx = level_cells[target_y][a][9] if len(level_cells[target_y][a]) > 9 else -1
                        else:
                            roof_idx = -1
                    else:
                        cx, cy = a * ctx['ts'], b * ctx['ts'] + ctx['ts'] / 2
                        if len(level_cells[0][0]) > 8:
                            target_x = min(a, maze_data.width - 1)
                            roof_idx = level_cells[b][target_x][9] if len(level_cells[b][target_x]) > 9 else -1
                        else:
                            roof_idx = -1
                    
                    if ctx['roof_meshes_list'] and isinstance(roof_idx, int) and 0 <= roof_idx < len(ctx['roof_meshes_list']):
                        mat_base = Matrix.Translation(Vector((cx, cy, sz)))
                        mat = mat_base @ ctx['mat_roof_offset']
                        _add_mesh_at(bm_roof, ctx['roof_meshes_list'][roof_idx], mat, uv_roof, final_materials_list=roof_materials)
                    elif ctx['custom_roof']:
                        mat_base = Matrix.Translation(Vector((cx, cy, sz)))
                        mat = mat_base @ ctx['mat_roof_offset']
                        _add_mesh_at(bm_roof, ctx['custom_roof'], mat, uv_roof, final_materials_list=roof_materials)
            else:
                filled = set()
                for seg_type, a, b in segments:
                    if seg_type == 'H':
                        _add_horizontal_roof_face_transformed(bm_roof, uv_roof, a, b, ctx['ts'], sz, ctx['wt'], ctx['mat_roof_offset'])
                    else:
                        tsouth = (a, b) in h_endpoints
                        tnorth = (a, b + 1) in h_endpoints
                        _add_vertical_roof_face_transformed(bm_roof, uv_roof, a, b, ctx['ts'], sz, ctx['wt'], ctx['mat_roof_offset'], trim_south=tsouth, trim_north=tnorth)
                        tw = ctx['wt'] / 2
                        xc = a * ctx['ts']
                        if tsouth:
                            yc = b * ctx['ts']
                            y_lo = yc if (a, b - 1) not in v_positions else yc - tw
                            y_hi = yc + tw
                            for gx, gy, side in [(a - 1, b, 'l'), (a, b, 'r')]:
                                key = (gx, gy, side)
                                if (gx, gy) not in h_positions and key not in filled:
                                    filled.add(key)
                                    if side == 'l':
                                        hx0_rel, hx1_rel = -tw, 0
                                    else:
                                        hx0_rel, hx1_rel = 0, tw
                                    _add_vertical_roof_filler_transformed(bm_roof, uv_roof, xc, yc, sz, tw, y_lo - yc, y_hi - yc, hx0_rel, hx1_rel, ctx['mat_roof_offset'])
                        if tnorth:
                            yc = (b + 1) * ctx['ts']
                            y_lo = yc - tw
                            y_hi = yc if (a, b + 1) not in v_positions else yc + tw
                            for gx, gy, side in [(a - 1, b + 1, 'l'), (a, b + 1, 'r')]:
                                key = (gx, gy, side)
                                if (gx, gy) not in h_positions and key not in filled:
                                    filled.add(key)
                                    if side == 'l':
                                        hx0_rel, hx1_rel = -tw, 0
                                    else:
                                        hx0_rel, hx1_rel = 0, tw
                                    _add_vertical_roof_filler_transformed(bm_roof, uv_roof, xc, yc, sz, tw, y_lo - yc, y_hi - yc, hx0_rel, hx1_rel, ctx['mat_roof_offset'])

        if not ctx['custom_roof']:
            bmesh.ops.remove_doubles(bm_roof, verts=bm_roof.verts, dist=0.001)
        roof_obj = _create_object_from_bm(bm_roof, f"FireMaze_Roof{name_suffix}", ctx['col'], None)
        for mat in roof_materials:
            roof_obj.data.materials.append(mat)
        created_objects.append(roof_obj)


def build_maze_objects_impl(props, maze_data, context, collection=None, force_simple=False, name_suffix=""):
    """Build all rectangular maze meshes (floor, walls, roof, stairs, guide path, colliders) into a collection."""
    if maze_data.grid_type == 'polar':
        return _build_polar_maze_objects_impl(props, maze_data, context, collection, force_simple, name_suffix)

    ctx = _prepare_maze_building_context(props, maze_data, context, collection, force_simple)
    created_objects = []

    if props.wall_mode == 'cube':
        _build_rect_cube_floor(ctx, maze_data, created_objects, name_suffix)
        _build_rect_cube_walls(ctx, props, maze_data, created_objects, name_suffix)
        _build_rect_cube_roof(ctx, props, maze_data, created_objects, name_suffix)
        _build_rect_stairs(ctx, props, maze_data, created_objects, name_suffix)
    else:
        _build_rect_thin_floor(ctx, maze_data, created_objects, name_suffix)
        _build_rect_thin_walls(ctx, props, maze_data, created_objects, name_suffix)
        _build_rect_thin_roof(ctx, maze_data, created_objects, name_suffix)
        _build_rect_stairs(ctx, props, maze_data, created_objects, name_suffix)

    # Build guide path if requested
    if not force_simple:
        guide_obj = _build_guide_path(props, maze_data, ctx['col'], ctx['materials'])
        if guide_obj:
            created_objects.append(guide_obj)

    # Set collider / helper specific flags
    if name_suffix == "_Collider":
        for obj in created_objects:
            obj.hide_render = True
            obj.display_type = 'WIRE'
    elif name_suffix == "_EditHelper":
        for obj in created_objects:
            obj.hide_render = True
            # Keep display_type as SOLID so raycasting hits faces, not wires!

    # Perform merges and cleanups
    if props.remove_doubles:
        for obj in created_objects:
            _remove_doubles_on_obj(obj)

    # Merging logic
    if name_suffix == "_Collider":
        if props.merge_colliders:
            meshes_to_merge = [obj for obj in created_objects if obj.type == 'MESH']
            merged_obj = _merge_maze_objects(meshes_to_merge, context, name="FireMaze_Collider")
            if props.optimize_colliders_coplanar and merged_obj:
                _optimize_coplanar_on_obj(merged_obj)
        else:
            if props.optimize_colliders_coplanar:
                for obj in created_objects:
                    if obj.type == 'MESH':
                        _optimize_coplanar_on_obj(obj)
    elif name_suffix == "_EditHelper":
        meshes_to_merge = [obj for obj in created_objects if obj.type == 'MESH']
        merged_obj = _merge_maze_objects(meshes_to_merge, context, name="_FireMaze_Edit_Helper")
        if merged_obj:
            merged_obj.hide_viewport = True
            merged_obj.hide_render = True
    else:
        if props.merge_objects:
            # Filter meshes for joining (Curves like guide path cannot be merged directly with meshes in join unless converted)
            meshes_to_merge = [obj for obj in created_objects if obj.type == 'MESH']
            merged_obj = _merge_maze_objects(meshes_to_merge, context, name="FireMaze_Merged")
        # Remaining non-mesh objects are kept separate but stay in the collection

    # Post-process visual mesh objects (optimize coplanar, vertex paint, lightmap)
    if not name_suffix and not props.is_editing:
        visual_meshes = [obj for obj in ctx['col'].objects if obj.type == 'MESH']
        
        # 1. Optimize coplanar faces
        if props.optimize_coplanar:
            for obj in visual_meshes:
                _optimize_coplanar_on_obj(obj)
                
        # 2. Procedural vertex painting
        if props.vertex_paint_enable:
            for obj in visual_meshes:
                _apply_vertex_painting_on_obj(obj, props, maze_data)
                
        # 3. Lightmap UV generation
        if props.generate_lightmap:
            for obj in visual_meshes:
                _generate_lightmap_on_obj(obj, context, method=props.lightmap_method)

        # 4. Spawn decorations and props
        _spawn_decorations(props, maze_data, context, ctx['col'])

    return ctx['col']


def _ensure_materials():
    """Create or retrieve FireMaze materials (floor, wall, roof, end_cap, guide) with Principled BSDF defaults."""
    mats = {}
    for key, label, color in [
        ("floor", "FireMaze_Floor", (0.15, 0.15, 0.15, 1.0)),
        ("wall", "FireMaze_Walls", (0.35, 0.35, 0.35, 1.0)),
        ("roof", "FireMaze_Roof", (0.25, 0.25, 0.25, 1.0)),
        ("end_cap", "FireMaze_WallEndCaps", (0.6, 0.3, 0.3, 1.0)),
        ("guide", "FireMaze_Guide", (0.0, 1.0, 0.2, 1.0)),
    ]:
        mat = bpy.data.materials.get(label)
        if not mat:
            mat = bpy.data.materials.new(label)
            mat.use_nodes = True
            bsdf = mat.node_tree.nodes.get("Principled BSDF")
            if bsdf:
                # Blender 4.0+ color input
                if "Base Color" in bsdf.inputs:
                    bsdf.inputs["Base Color"].default_value = color
                if key == "guide":
                    # Emission
                    if "Emission Color" in bsdf.inputs:
                        bsdf.inputs["Emission Color"].default_value = color
                    elif "Emission" in bsdf.inputs:
                        bsdf.inputs["Emission"].default_value = color
                    if "Emission Strength" in bsdf.inputs:
                        bsdf.inputs["Emission Strength"].default_value = 2.5
        mats[key] = mat
    return mats
