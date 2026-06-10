"""Common BMesh helper functions and context managers for FireMaze builders."""

import math
import random as _real_random
import bpy
import bmesh
import logging
import threading
from collections import deque
from contextlib import contextmanager
from mathutils import Matrix, Vector
from ..utils import is_valid_ref, _resolve_cells_3d, _get_stair_footprint_coords

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
        'clean_wall_corners': props.clean_wall_corners,
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


def _add_horizontal_roof_face_transformed(bm, uv_layer, x, y, ts, wh, wt, mat_offset, extend_left=False, extend_right=False):
    """Add a horizontal roof cap quad along a thin wall segment at grid-line (x, y)."""
    xc = x * ts + ts / 2
    yc = y * ts
    T = Matrix.Translation(Vector((xc, yc, wh))) @ mat_offset
    tw = wt / 2
    t2 = ts / 2
    x_lo = -t2 - (tw if extend_left else 0)
    x_hi = t2 + (tw if extend_right else 0)
    v0 = T @ Vector((x_lo, -tw, 0))
    v1 = T @ Vector((x_hi, -tw, 0))
    v2 = T @ Vector((x_hi, tw, 0))
    v3 = T @ Vector((x_lo, tw, 0))
    face = bm.faces.new([bm.verts.new(v0), bm.verts.new(v1), bm.verts.new(v2), bm.verts.new(v3)])

    u_left = -tw if extend_left else 0.0
    u_right = ts + tw if extend_right else ts
    for loop, uv in zip(face.loops, [(u_left, 0), (u_right, 0), (u_right, wt), (u_left, wt)]):
        loop[uv_layer].uv = uv


def _add_vertical_roof_face_transformed(bm, uv_layer, x, y, ts, wh, wt, mat_offset, trim_south=False, trim_north=False, extend_south=False, extend_north=False):
    """Add a vertical roof cap face with optional trimming/extending at junctions."""
    xc = x * ts
    yc = y * ts + ts / 2
    T = Matrix.Translation(Vector((xc, yc, wh))) @ mat_offset
    tw = wt / 2
    t2 = ts / 2
    if extend_south:
        y0 = -t2 - tw
    else:
        y0 = -t2 + (tw if trim_south else 0)
    if extend_north:
        y1 = t2 + tw
    else:
        y1 = t2 - (tw if trim_north else 0)

    v0 = T @ Vector((-tw, y0, 0))
    v1 = T @ Vector((tw, y0, 0))
    v2 = T @ Vector((tw, y1, 0))
    v3 = T @ Vector((-tw, y1, 0))
    face = bm.faces.new([bm.verts.new(v0), bm.verts.new(v1), bm.verts.new(v2), bm.verts.new(v3)])
    
    u_south = y0 + t2
    u_north = y1 + t2
    for loop, uv in zip(face.loops, [(0, u_south), (wt, u_south), (wt, u_north), (0, u_north)]):
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
        else:
            # Cube mode neighbors
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < width and 0 <= ny < depth:
                    if not cells_3d[cz][ny][nx][0]:
                        neighbors.append((cz, ny, nx))
                        
        # Stair transitions
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
