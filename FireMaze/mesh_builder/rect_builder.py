"""Rectangular grid builders and main mesh builder dispatcher for FireMaze."""

import math
import bpy
import bmesh
from mathutils import Matrix, Vector
from ..utils import get_rng, get_cell_id
from ..maze_data import MazeData
from .bmesh_utils import (
    _create_bmesh_element,
    _add_mesh_at,
    _add_floor_tile_transformed,
    _create_object_from_bm,
    _add_wall_face_transformed,
    _safe_remove_doubles,
    _add_cube_roof_face_transformed,
    _prepare_maze_building_context,
    _get_wall_segments,
    _add_horizontal_roof_face_transformed,
    _add_vertical_roof_face_transformed,
    _add_vertical_roof_filler_transformed,
    _build_guide_path,
    _remove_doubles_on_obj,
)
from .stair_builder import _build_ramp_1x1, _build_spiral_stair_1x1
from .post_processor import (
    _optimize_coplanar_on_obj,
    _merge_maze_objects,
    _apply_vertex_painting_on_obj,
    _generate_lightmap_on_obj,
    _spawn_decorations,
)
from .polar_builder import _build_polar_maze_objects_impl

def _build_rect_cube_floor(ctx, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, dirty_cells=None):
    # Floor
    if bm is None:
        bm_floor, uv_floor, floor_materials = _create_bmesh_element("floor", ctx['materials'])
        is_external_bm = False
    else:
        bm_floor = bm
        uv_floor = uv_layer
        floor_materials = materials
        is_external_bm = True

    cell_layer = bm_floor.faces.layers.int.get("cell_id")
    if cell_layer is None:
        cell_layer = bm_floor.faces.layers.int.new("cell_id")
    off = ctx['ts'] / 2 if ctx['centered'] else 0
    for z in ctx['z_range']:
        z_off = z * ctx['wh']
        level_cells = ctx['cells_3d'][z]
        for y in range(maze_data.depth):
            for x in range(maze_data.width):
                if dirty_cells is not None and (z, y, x) not in dirty_cells:
                    continue
                if (z, y, x) in ctx['stair_top_cells']:
                    continue
                is_wall = level_cells[y][x][0]
                if not is_wall:
                    if dirty_cells is None:
                        start_idx = len(bm_floor.faces)
                    else:
                        existing_faces = set(bm_floor.faces)

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

                    cell_id = get_cell_id(z, y, x)
                    if dirty_cells is None:
                        bm_floor.faces.ensure_lookup_table()
                        for i in range(start_idx, len(bm_floor.faces)):
                            bm_floor.faces[i][cell_layer] = cell_id
                    else:
                        for f in bm_floor.faces:
                            if f not in existing_faces:
                                f[cell_layer] = cell_id

    if not is_external_bm:
        floor_obj = _create_object_from_bm(bm_floor, f"FireMaze_Floor{name_suffix}", ctx['col'], None)
        for mat in floor_materials:
            floor_obj.data.materials.append(mat)
        created_objects.append(floor_obj)



def _build_rect_cube_walls(ctx, props, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, dirty_cells=None):
    # Walls
    if bm is None:
        bm_wall, uv_wall, wall_materials = _create_bmesh_element("wall", ctx['materials'])
        is_external_bm = False
    else:
        bm_wall = bm
        uv_wall = uv_layer
        wall_materials = materials
        is_external_bm = True

    cell_layer = bm_wall.faces.layers.int.get("cell_id")
    if cell_layer is None:
        cell_layer = bm_wall.faces.layers.int.new("cell_id")
    cent = Matrix.Translation(Vector((-ctx['ts'] / 2, -ctx['ts'] / 2, 0))) if not ctx['centered'] else Matrix.Identity(4)
    wall_rng = get_rng()
    for z in ctx['z_range']:
        z_off_floor = z * ctx['wh']
        level_cells = ctx['cells_3d'][z]
        for level in range(ctx['tiles_high']):
            z_off = z_off_floor + level * ctx['seg_h']
            hw = z_off + ctx['seg_h'] / 2
            for y in range(maze_data.depth):
                for x in range(maze_data.width):
                    if dirty_cells is not None and (z, y, x) not in dirty_cells:
                        continue
                    is_wall = level_cells[y][x][0]
                    if is_wall:
                        if dirty_cells is None:
                            start_idx = len(bm_wall.faces)
                        else:
                            existing_faces = set(bm_wall.faces)
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
                            def place_wall_face(direction, offset_trans_rot, fallback_mesh, f_idx):
                                if ctx['wall_meshes_list'] and isinstance(f_idx, int) and 0 <= f_idx < len(ctx['wall_meshes_list']):
                                    m = ctx['wall_meshes_list'][f_idx]
                                    mat_base = Matrix.Translation(Vector((cx + ctx['ts']/2, cy + ctx['ts']/2, hw))) @ offset_trans_rot @ cent
                                    mat = mat_base @ ctx['mat_wall_offset']
                                    _add_mesh_at(bm_wall, m, mat, uv_wall, final_materials_list=wall_materials)
                                elif fallback_mesh:
                                    mat_base = Matrix.Translation(Vector((cx + ctx['ts']/2, cy + ctx['ts']/2, hw))) @ offset_trans_rot @ cent
                                    mat = mat_base @ ctx['mat_wall_offset']
                                    _add_mesh_at(bm_wall, fallback_mesh, mat, uv_wall, final_materials_list=wall_materials)
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

                        cell_id = get_cell_id(z, y, x)
                        if dirty_cells is None:
                            bm_wall.faces.ensure_lookup_table()
                            for i in range(start_idx, len(bm_wall.faces)):
                                bm_wall.faces[i][cell_layer] = cell_id
                        else:
                            for f in bm_wall.faces:
                                if f not in existing_faces:
                                    f[cell_layer] = cell_id

    if not is_external_bm:
        _safe_remove_doubles(bm_wall, dist=0.001)
        wall_obj = _create_object_from_bm(bm_wall, f"FireMaze_Walls{name_suffix}", ctx['col'], None)
        for mat in wall_materials:
            wall_obj.data.materials.append(mat)
        created_objects.append(wall_obj)



def _build_rect_cube_roof(ctx, props, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, dirty_cells=None):
    # Roof
    if not props.cube_mode_pillar or name_suffix in {"_EditHelper", "_Collider"}:
        if bm is None:
            bm_roof, uv_roof, roof_materials = _create_bmesh_element("roof", ctx['materials'])
            is_external_bm = False
        else:
            bm_roof = bm
            uv_roof = uv_layer
            roof_materials = materials
            is_external_bm = True

        cell_layer = bm_roof.faces.layers.int.get("cell_id")
        if cell_layer is None:
            cell_layer = bm_roof.faces.layers.int.new("cell_id")

        for z in ctx['z_range']:
            z_off = z * ctx['wh']
            level_cells = ctx['cells_3d'][z]
            for y in range(maze_data.depth):
                for x in range(maze_data.width):
                    if dirty_cells is not None and (z, y, x) not in dirty_cells:
                        continue
                    if (z, y, x) in ctx['stair_bottom_cells']:
                        continue
                    is_wall = level_cells[y][x][0]
                    if is_wall:
                        if dirty_cells is None:
                            start_idx = len(bm_roof.faces)
                        else:
                            existing_faces = set(bm_roof.faces)

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

                        cell_id = get_cell_id(z, y, x)
                        if dirty_cells is None:
                            bm_roof.faces.ensure_lookup_table()
                            for i in range(start_idx, len(bm_roof.faces)):
                                bm_roof.faces[i][cell_layer] = cell_id
                        else:
                            for f in bm_roof.faces:
                                if f not in existing_faces:
                                    f[cell_layer] = cell_id

        if not is_external_bm:
            if not ctx['custom_roof'] and not ctx['roof_meshes_list']:
                bmesh.ops.remove_doubles(bm_roof, verts=bm_roof.verts, dist=0.001)
            roof_obj = _create_object_from_bm(bm_roof, f"FireMaze_Roof{name_suffix}", ctx['col'], None)
            for mat in roof_materials:
                roof_obj.data.materials.append(mat)
            created_objects.append(roof_obj)



def _build_rect_stairs(ctx, props, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, dirty_cells=None):
    # Stairs (cube & thin modes share identical building structure)
    if maze_data.stairs:
        if bm is None:
            bm_stairs, uv_stairs, stair_materials = _create_bmesh_element("wall", ctx['materials'])
            is_external_bm = False
        else:
            bm_stairs = bm
            uv_stairs = uv_layer
            stair_materials = materials
            is_external_bm = True

        cell_layer = bm_stairs.faces.layers.int.get("cell_id")
        if cell_layer is None:
            cell_layer = bm_stairs.faces.layers.int.new("cell_id")

        for zstair in ctx['z_range']:
            z_offset = zstair * ctx['wh']
            for s in maze_data.stairs:
                if s.get('z') != zstair:
                    continue
                sx, sy = s.get('x', 0), s.get('y', 0)
                if dirty_cells is not None and (zstair, sy, sx) not in dirty_cells:
                    continue

                if dirty_cells is None:
                    start_idx = len(bm_stairs.faces)
                else:
                    existing_faces = set(bm_stairs.faces)

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

                if style == 'ramp' and ctx['custom_ramp_mesh']:
                    off = ctx['ts'] / 2 if ctx['centered'] else 0
                    mat = Matrix.Translation(Vector((sx * ctx['ts'] + off, sy * ctx['ts'] + off, z_offset))) @ rot_mat @ ctx['mat_floor_offset']
                    _add_mesh_at(bm_stairs, ctx['custom_ramp_mesh'].data if ctx['custom_ramp_mesh'].type == 'MESH' else ctx['custom_ramp_mesh'], mat, uv_stairs, final_materials_list=stair_materials)
                elif style == 'stair' and ctx['custom_stair_mesh']:
                    off = ctx['ts'] / 2 if ctx['centered'] else 0
                    mat = Matrix.Translation(Vector((sx * ctx['ts'] + off, sy * ctx['ts'] + off, z_offset))) @ rot_mat @ ctx['mat_floor_offset']
                    _add_mesh_at(bm_stairs, ctx['custom_stair_mesh'].data if ctx['custom_stair_mesh'].type == 'MESH' else ctx['custom_stair_mesh'], mat, uv_stairs, final_materials_list=stair_materials)
                elif style == 'ramp':
                    _build_ramp_1x1(bm_stairs, uv_stairs, cx, cy, ctx['ts'], ctx['wh'], z_offset, rot_mat @ ctx['mat_floor_offset'])
                else:
                    _build_spiral_stair_1x1(bm_stairs, uv_stairs, cx, cy, ctx['ts'], ctx['wh'], z_offset, rot_mat @ ctx['mat_floor_offset'])

                cell_id = get_cell_id(zstair, sy, sx)
                if dirty_cells is None:
                    bm_stairs.faces.ensure_lookup_table()
                    for i in range(start_idx, len(bm_stairs.faces)):
                        bm_stairs.faces[i][cell_layer] = cell_id
                else:
                    for f in bm_stairs.faces:
                        if f not in existing_faces:
                            f[cell_layer] = cell_id

        if not is_external_bm:
            if bm_stairs.verts:
                bmesh.ops.remove_doubles(bm_stairs, verts=bm_stairs.verts, dist=0.001)
                stair_obj = _create_object_from_bm(bm_stairs, f"FireMaze_Stairs{name_suffix}", ctx['col'], None)
                for mat in stair_materials:
                    stair_obj.data.materials.append(mat)
                created_objects.append(stair_obj)
            else:
                bm_stairs.free()



def _build_rect_thin_floor(ctx, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, dirty_cells=None):
    # Floor (all levels combined into one BMesh)
    if bm is None:
        bm_floor, uv_floor, floor_materials = _create_bmesh_element("floor", ctx['materials'])
        is_external_bm = False
    else:
        bm_floor = bm
        uv_floor = uv_layer
        floor_materials = materials
        is_external_bm = True

    cell_layer = bm_floor.faces.layers.int.get("cell_id") or bm_floor.faces.layers.int.new("cell_id")
    off = ctx['ts'] / 2 if ctx['centered'] else 0
    for z in ctx['z_range']:
        stair_top_cells_z = {(yy, xx) for (zz, yy, xx) in ctx['stair_top_cells'] if zz == z}
        level_cells = ctx['cells_3d'][z]
        for y in range(maze_data.depth):
            for x in range(maze_data.width):
                if dirty_cells is not None and (z, y, x) not in dirty_cells:
                    continue
                if (y, x) in stair_top_cells_z:
                    continue
                if dirty_cells is None:
                    start_idx = len(bm_floor.faces)
                else:
                    existing_faces = set(bm_floor.faces)

                floor_idx = level_cells[y][x][8] if len(level_cells[y][x]) > 8 else -1
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

                cell_id = get_cell_id(z, y, x)
                if dirty_cells is None:
                    bm_floor.faces.ensure_lookup_table()
                    for i in range(start_idx, len(bm_floor.faces)):
                        bm_floor.faces[i][cell_layer] = cell_id
                else:
                    for f in bm_floor.faces:
                        if f not in existing_faces:
                            f[cell_layer] = cell_id

    if not is_external_bm:
        floor_obj = _create_object_from_bm(bm_floor, f"FireMaze_Floor{name_suffix}", ctx['col'], None)
        for mat in floor_materials:
            floor_obj.data.materials.append(mat)
        created_objects.append(floor_obj)



def _compute_corner_offsets(a, b, seg_type, h_positions, v_positions, clean_corners, tw):
    """Compute the left/south and right/north end corner extensions/offsets for thin walls."""
    if seg_type == 'H':
        perp_left = ((a, b) in v_positions) + ((a, b - 1) in v_positions)
        perp_right = ((a + 1, b) in v_positions) + ((a + 1, b - 1) in v_positions)
        continues_left = (a - 1, b) in h_positions
        continues_right = (a + 1, b) in h_positions
        
        offset_left = tw if (clean_corners and perp_left == 1 and not continues_left) else 0.0
        offset_right = tw if (clean_corners and perp_right == 1 and not continues_right) else 0.0
        return offset_left, offset_right, perp_left, perp_right, continues_left, continues_right
    else:
        perp_south = ((a, b) in h_positions) + ((a - 1, b) in h_positions)
        perp_north = ((a, b + 1) in h_positions) + ((a - 1, b + 1) in h_positions)
        continues_south = (a, b - 1) in v_positions
        continues_north = (a, b + 1) in v_positions
        
        offset_south = tw if (clean_corners and perp_south == 1 and not continues_south) else 0.0
        offset_north = tw if (clean_corners and perp_north == 1 and not continues_north) else 0.0
        return offset_south, offset_north, perp_south, perp_north, continues_south, continues_north


def _build_rect_thin_walls(ctx, props, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, bm_cap=None, uv_layer_cap=None, materials_cap=None, dirty_cells=None):
    # Walls and caps
    if bm is None:
        bm_wall, uv_wall, wall_materials = _create_bmesh_element("wall", ctx['materials'])
        is_external_bm = False
    else:
        bm_wall = bm
        uv_wall = uv_layer
        wall_materials = materials
        is_external_bm = True

    if bm_cap is None:
        bm_cap, uv_cap, cap_materials = _create_bmesh_element("end_cap", ctx['materials'])
        is_external_cap = False
    else:
        bm_cap = bm_cap
        uv_cap = uv_layer_cap
        cap_materials = materials_cap
        is_external_cap = True

    cell_layer = bm_wall.faces.layers.int.get("cell_id")
    if cell_layer is None:
        cell_layer = bm_wall.faces.layers.int.new("cell_id")
    cell_layer_cap = bm_cap.faces.layers.int.get("cell_id")
    if cell_layer_cap is None:
        cell_layer_cap = bm_cap.faces.layers.int.new("cell_id")

    has_any_wall_custom = (ctx['custom_wall'] or ctx['wall_meshes_list'])
    cent = Matrix.Translation(Vector((-ctx['ts'] / 2, -ctx['ts'] / 2, 0))) if not ctx['centered'] else Matrix.Identity(4)
    tw = ctx['wt'] / 2
    wall_rng = get_rng()

    clean_corners = ctx['clean_wall_corners']
    index = {'N': 0, 'S': 1, 'E': 2, 'W': 3}
    opposites = {'N': 'S', 'S': 'N', 'E': 'W', 'W': 'E'}

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
                if seg_type == 'H':
                    owner_cell = (z, b, a) if b < maze_data.depth else (z, b - 1, a)
                else:
                    owner_cell = (z, b, a) if a < maze_data.width else (z, b, a - 1)

                if dirty_cells is not None and owner_cell not in dirty_cells:
                    continue

                if seg_type == 'V' and a == 3 and b == 2:
                    print("DEBUG rect_builder: building segment ('V', 3, 2), owner_cell =", owner_cell)

                if dirty_cells is None:
                    start_idx = len(bm_wall.faces)
                    start_cap_idx = len(bm_cap.faces)
                else:
                    existing_faces = set(bm_wall.faces)
                    existing_cap_faces = set(bm_cap.faces)

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
                            wall_idx = b % len(ctx['wall_meshes_list']) if len(ctx['wall_meshes_list']) > 0 else -1

                resolved_mesh = None
                if ctx['wall_meshes_list']:
                    if isinstance(wall_idx, int) and 0 <= wall_idx < len(ctx['wall_meshes_list']):
                        resolved_mesh = ctx['wall_meshes_list'][wall_idx]
                    else:
                        resolved_mesh = wall_rng.choice(ctx['wall_meshes_list'])

                if seg_type == 'H':
                    x0, x1 = a * ctx['ts'], (a + 1) * ctx['ts']
                    yc = b * ctx['ts']
                    
                    dx_left, dx_right, perp_left, perp_right, continues_left, continues_right = _compute_corner_offsets(
                        a, b, 'H', h_positions, v_positions, clean_corners, tw
                    )
                    
                    def add_horizontal_face(direction, offset_rot, custom_mesh_fallback, y_offset, uvs_standard, local_pts):
                        """Place a horizontal wall face (along Y) using a custom mesh or procedural geometry."""
                        if ctx['wall_meshes_list']:
                            src_mesh = resolved_mesh
                            if clean_corners and (dx_left > 0.0 or dx_right > 0.0):
                                scale_x = (ctx['ts'] + dx_left + dx_right) / ctx['ts']
                                shift_x = (dx_right - dx_left) / 2
                                mat_scale_shift = Matrix.Translation(Vector((shift_x, 0, 0))) @ Matrix.Scale(scale_x, 4, Vector((1, 0, 0)))
                                mat_base = Matrix.Translation(Vector((x0 + ctx['ts'] / 2, yc + y_offset, hw))) @ mat_scale_shift @ offset_rot @ cent
                            else:
                                mat_base = Matrix.Translation(Vector((x0 + ctx['ts'] / 2, yc + y_offset, hw))) @ offset_rot @ cent
                            mat = mat_base @ ctx['mat_wall_offset']
                            _add_mesh_at(bm_wall, src_mesh, mat, uv_wall, final_materials_list=wall_materials)
                        elif custom_mesh_fallback:
                            if clean_corners and (dx_left > 0.0 or dx_right > 0.0):
                                scale_x = (ctx['ts'] + dx_left + dx_right) / ctx['ts']
                                shift_x = (dx_right - dx_left) / 2
                                mat_scale_shift = Matrix.Translation(Vector((shift_x, 0, 0))) @ Matrix.Scale(scale_x, 4, Vector((1, 0, 0)))
                                mat_base = Matrix.Translation(Vector((x0 + ctx['ts'] / 2, yc + y_offset, hw))) @ mat_scale_shift @ offset_rot @ cent
                            else:
                                mat_base = Matrix.Translation(Vector((x0 + ctx['ts'] / 2, yc + y_offset, hw))) @ offset_rot @ cent
                            mat = mat_base @ ctx['mat_wall_offset']
                            _add_mesh_at(bm_wall, custom_mesh_fallback, mat, uv_wall, final_materials_list=wall_materials)
                        else:
                            T = Matrix.Translation(Vector((x0 + ctx['ts']/2, yc, hw))) @ ctx['mat_wall_offset']
                            verts = [bm_wall.verts.new(T @ Vector(p)) for p in local_pts]
                            f = bm_wall.faces.new(verts)
                            for loop, uv in zip(f.loops, uvs_standard):
                                loop[uv_wall].uv = uv

                    if props.thin_wall_double_sided:
                        if not has_any_wall_custom:
                            u_left = -dx_left / ctx['ts']
                            u_right = 1.0 + dx_right / ctx['ts']
                            
                            # North face (+Y)
                            add_horizontal_face('+Y', Matrix.Rotation(math.radians(-90), 4, 'X'), None, tw, 
                                                [(u_right,0),(u_left,0),(u_left,1),(u_right,1)],
                                                [(ctx['ts']/2 + dx_right, tw, -sh/2), (-ctx['ts']/2 - dx_left, tw, -sh/2), (-ctx['ts']/2 - dx_left, tw, sh/2), (ctx['ts']/2 + dx_right, tw, sh/2)])
                            # South face (-Y)
                            add_horizontal_face('-Y', Matrix.Rotation(math.radians(90), 4, 'X'), None, -tw, 
                                                [(u_left,0),(u_right,0),(u_right,1),(u_left,1)],
                                                [(-ctx['ts']/2 - dx_left, -tw, -sh/2), (ctx['ts']/2 + dx_right, -tw, -sh/2), (ctx['ts']/2 + dx_right, -tw, sh/2), (-ctx['ts']/2 - dx_left, -tw, sh/2)])
                        else:
                            # North face (+Y)
                            add_horizontal_face('+Y', Matrix.Rotation(math.radians(-90), 4, 'X') @ Matrix.Rotation(math.radians(180), 4, 'Z'), ctx['custom_wall'], tw,
                                                [(0,0),(1,0),(1,1),(0,1)],
                                                [(ctx['ts']/2, tw, -sh/2), (-ctx['ts']/2, tw, -sh/2), (-ctx['ts']/2, tw, sh/2), (ctx['ts']/2, tw, sh/2)])
                            # South face (-Y)
                            add_horizontal_face('-Y', Matrix.Rotation(math.radians(90), 4, 'X'), ctx['custom_wall'], -tw, 
                                                [(0,0),(1,0),(1,1),(0,1)],
                                                [(-ctx['ts']/2, -tw, -sh/2), (ctx['ts']/2, -tw, -sh/2), (ctx['ts']/2, -tw, sh/2), (-ctx['ts']/2, -tw, sh/2)])
                        
                        # West end-cap
                        if not continues_left and perp_left <= 1:
                            T = Matrix.Translation(Vector((x0 + ctx['ts']/2, yc, hw))) @ ctx['mat_wall_offset']
                            v_pts = [T @ Vector(p) for p in [(-ctx['ts']/2, tw, -sh/2), (-ctx['ts']/2, -tw, -sh/2), (-ctx['ts']/2, -tw, sh/2), (-ctx['ts']/2, tw, sh/2)]]
                            f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                            for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                loop[uv_cap].uv = uv
                        # East end-cap
                        if not continues_right and perp_right <= 1:
                            T = Matrix.Translation(Vector((x0 + ctx['ts']/2, yc, hw))) @ ctx['mat_wall_offset']
                            v_pts = [T @ Vector(p) for p in [(ctx['ts']/2, -tw, -sh/2), (ctx['ts']/2, tw, -sh/2), (ctx['ts']/2, tw, sh/2), (ctx['ts']/2, -tw, sh/2)]]
                            f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                            for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                loop[uv_cap].uv = uv
                    else:
                        if not has_any_wall_custom:
                            # Single centered face at 0.0 offset (and no caps)
                            T = Matrix.Translation(Vector((x0 + ctx['ts']/2, yc, hw))) @ ctx['mat_wall_offset']
                            verts = [bm_wall.verts.new(T @ Vector(p)) for p in [(ctx['ts']/2, 0.0, -sh/2), (-ctx['ts']/2, 0.0, -sh/2), (-ctx['ts']/2, 0.0, sh/2), (ctx['ts']/2, 0.0, sh/2)]]
                            f = bm_wall.faces.new(verts)
                            for loop, uv in zip(f.loops, [(1,0),(0,0),(0,1),(1,1)]):
                                loop[uv_wall].uv = uv
                        else:
                            # Single centered face at 0.0 offset (and no caps)
                            add_horizontal_face('+Y', Matrix.Rotation(math.radians(-90), 4, 'X') @ Matrix.Rotation(math.radians(180), 4, 'Z'), ctx['custom_wall'], 0.0, 
                                                [(0,0),(1,0),(1,1),(0,1)],
                                                [(ctx['ts']/2, 0.0, -sh/2), (-ctx['ts']/2, 0.0, -sh/2), (-ctx['ts']/2, 0.0, sh/2), (ctx['ts']/2, 0.0, sh/2)])
     
                else:
                    xc = a * ctx['ts']
                    y0, y1 = b * ctx['ts'], (b + 1) * ctx['ts']
                    
                    dy_south, dy_north, perp_south, perp_north, continues_south, continues_north = _compute_corner_offsets(
                        a, b, 'V', h_positions, v_positions, clean_corners, tw
                    )
                    
                    def add_vertical_face(direction, offset_rot, custom_mesh_fallback, x_offset, uvs_standard, local_pts):
                        """Place a vertical wall face (along X) using a custom mesh or procedural geometry."""
                        if ctx['wall_meshes_list']:
                            src_mesh = resolved_mesh
                            if clean_corners and (dy_south > 0.0 or dy_north > 0.0):
                                scale_y = (ctx['ts'] + dy_south + dy_north) / ctx['ts']
                                shift_y = (dy_north - dy_south) / 2
                                mat_scale_shift = Matrix.Translation(Vector((0, shift_y, 0))) @ Matrix.Scale(scale_y, 4, Vector((0, 1, 0)))
                                mat_base = Matrix.Translation(Vector((xc + x_offset, y0 + ctx['ts'] / 2, hw))) @ mat_scale_shift @ offset_rot @ cent
                            else:
                                mat_base = Matrix.Translation(Vector((xc + x_offset, y0 + ctx['ts'] / 2, hw))) @ offset_rot @ cent
                            mat = mat_base @ ctx['mat_wall_offset']
                            _add_mesh_at(bm_wall, src_mesh, mat, uv_wall, final_materials_list=wall_materials)
                        elif custom_mesh_fallback:
                            if clean_corners and (dy_south > 0.0 or dy_north > 0.0):
                                scale_y = (ctx['ts'] + dy_south + dy_north) / ctx['ts']
                                shift_y = (dy_north - dy_south) / 2
                                mat_scale_shift = Matrix.Translation(Vector((0, shift_y, 0))) @ Matrix.Scale(scale_y, 4, Vector((0, 1, 0)))
                                mat_base = Matrix.Translation(Vector((xc + x_offset, y0 + ctx['ts'] / 2, hw))) @ mat_scale_shift @ offset_rot @ cent
                            else:
                                mat_base = Matrix.Translation(Vector((xc + x_offset, y0 + ctx['ts'] / 2, hw))) @ offset_rot @ cent
                            mat = mat_base @ ctx['mat_wall_offset']
                            _add_mesh_at(bm_wall, custom_mesh_fallback, mat, uv_wall, final_materials_list=wall_materials)
                        else:
                            T = Matrix.Translation(Vector((xc, y0 + ctx['ts']/2, hw))) @ ctx['mat_wall_offset']
                            verts = [bm_wall.verts.new(T @ Vector(p)) for p in local_pts]
                            f = bm_wall.faces.new(verts)
                            for loop, uv in zip(f.loops, uvs_standard):
                                loop[uv_wall].uv = uv

                    if props.thin_wall_double_sided:
                        if not has_any_wall_custom:
                            u_south = -dy_south / ctx['ts']
                            u_north = 1.0 + dy_north / ctx['ts']

                            # East face (+X)
                            add_vertical_face('+X', Matrix.Rotation(math.radians(-90), 4, 'Z') @ Matrix.Rotation(math.radians(-90), 4, 'X') @ Matrix.Rotation(math.radians(180), 4, 'Z'), None, tw, 
                                              [(u_south,0),(u_north,0),(u_north,1),(u_south,1)],
                                              [(tw, -ctx['ts']/2 - dy_south, -sh/2), (tw, ctx['ts']/2 + dy_north, -sh/2), (tw, ctx['ts']/2 + dy_north, sh/2), (tw, -ctx['ts']/2 - dy_south, sh/2)])
                            # West face (-X)
                            add_vertical_face('-X', Matrix.Rotation(math.radians(-90), 4, 'Y'), None, -tw, 
                                              [(u_north,0),(u_south,0),(u_south,1),(u_north,1)],
                                              [(-tw, ctx['ts']/2 + dy_north, -sh/2), (-tw, -ctx['ts']/2 - dy_south, -sh/2), (-tw, -ctx['ts']/2 - dy_south, sh/2), (-tw, ctx['ts']/2 + dy_north, sh/2)])
                        else:
                            # East face (+X)
                            add_vertical_face('+X', Matrix.Rotation(math.radians(90), 4, 'X') @ Matrix.Rotation(math.radians(90), 4, 'Y'), ctx['custom_wall'], tw,
                                              [(0,0),(1,0),(1,1),(0,1)],
                                              [(tw, -ctx['ts']/2, -sh/2), (tw, ctx['ts']/2, -sh/2), (tw, ctx['ts']/2, sh/2), (tw, -ctx['ts']/2, sh/2)])
                            # West face (-X)
                            add_vertical_face('-X', Matrix.Rotation(math.radians(90), 4, 'X') @ Matrix.Rotation(math.radians(-90), 4, 'Y'), ctx['custom_wall'], -tw,
                                              [(1,0),(0,0),(0,1),(1,1)],
                                              [(-tw, ctx['ts']/2, -sh/2), (-tw, -ctx['ts']/2, -sh/2), (-tw, -ctx['ts']/2, sh/2), (-tw, ctx['ts']/2, sh/2)])
                        
                        # South end-cap
                        if not continues_south and perp_south <= 1:
                            T = Matrix.Translation(Vector((xc, y0 + ctx['ts']/2, hw))) @ ctx['mat_wall_offset']
                            v_pts = [T @ Vector(p) for p in [(-tw, -ctx['ts']/2, -sh/2), (tw, -ctx['ts']/2, -sh/2), (tw, -ctx['ts']/2, sh/2), (-tw, -ctx['ts']/2, sh/2)]]
                            f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                            for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                loop[uv_cap].uv = uv
                        # North end-cap
                        if not continues_north and perp_north <= 1:
                            T = Matrix.Translation(Vector((xc, y0 + ctx['ts']/2, hw))) @ ctx['mat_wall_offset']
                            v_pts = [T @ Vector(p) for p in [(tw, ctx['ts']/2, -sh/2), (-tw, ctx['ts']/2, -sh/2), (-tw, ctx['ts']/2, sh/2), (tw, ctx['ts']/2, sh/2)]]
                            f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                            for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                loop[uv_cap].uv = uv
                    else:
                        if not has_any_wall_custom:
                            # Single centered face at 0.0 offset (and no caps)
                            T = Matrix.Translation(Vector((xc, y0 + ctx['ts']/2, hw))) @ ctx['mat_wall_offset']
                            verts = [bm_wall.verts.new(T @ Vector(p)) for p in [(0.0, -ctx['ts']/2, -sh/2), (0.0, ctx['ts']/2, -sh/2), (0.0, ctx['ts']/2, sh/2), (0.0, -ctx['ts']/2, sh/2)]]
                            f = bm_wall.faces.new(verts)
                            for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                loop[uv_wall].uv = uv
                        else:
                            # Single centered face at 0.0 offset (and no caps)
                            add_vertical_face('+X', Matrix.Rotation(math.radians(-90), 4, 'Z') @ Matrix.Rotation(math.radians(-90), 4, 'X') @ Matrix.Rotation(math.radians(180), 4, 'Z'), ctx['custom_wall'], 0.0,
                                              [(0,0),(1,0),(1,1),(0,1)],
                                              [(0.0, -ctx['ts']/2, -sh/2), (0.0, ctx['ts']/2, -sh/2), (0.0, ctx['ts']/2, sh/2), (0.0, -ctx['ts']/2, sh/2)])

                cell_id = get_cell_id(owner_cell[0], owner_cell[1], owner_cell[2])
                if dirty_cells is None:
                    bm_wall.faces.ensure_lookup_table()
                    for i in range(start_idx, len(bm_wall.faces)):
                        bm_wall.faces[i][cell_layer] = cell_id
                    bm_cap.faces.ensure_lookup_table()
                    for i in range(start_cap_idx, len(bm_cap.faces)):
                        bm_cap.faces[i][cell_layer_cap] = cell_id
                else:
                    for f in bm_wall.faces:
                        if f not in existing_faces:
                            f[cell_layer] = cell_id
                    for f in bm_cap.faces:
                        if f not in existing_cap_faces:
                            f[cell_layer_cap] = cell_id

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
            if cell_layer and cell_layer_cap:
                new_f[cell_layer] = f[cell_layer_cap]

    print("DEBUG rect_builder: end of _build_rect_thin_walls, faces =", len(bm_wall.faces))
    cell_layer_dbg = bm_wall.faces.layers.int.get("cell_id")
    if cell_layer_dbg is not None:
        cids = {f[cell_layer_dbg] for f in bm_wall.faces}
        print("DEBUG rect_builder: end of _build_rect_thin_walls cids =", sorted(list(cids)))

    if not is_external_bm:
        if props.single_wall_object:
            if bm_cap.verts:
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
    else:
        if props.single_wall_object and bm_cap.verts:
            bm_cap.free()
        elif not props.single_wall_object and bm_cap.verts:
            cap_obj = _create_object_from_bm(bm_cap, f"FireMaze_WallEndCaps{name_suffix}", ctx['col'], None)
            for mat in cap_materials:
                cap_obj.data.materials.append(mat)
            created_objects.append(cap_obj)
        elif not bm_cap.verts:
            bm_cap.free()



def _build_rect_thin_roof(ctx, props, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, dirty_cells=None):
    # Roof
    if name_suffix != "_EditHelper":
        if bm is None:
            bm_roof, uv_roof, roof_materials = _create_bmesh_element("roof", ctx['materials'])
            is_external_bm = False
        else:
            bm_roof = bm
            uv_roof = uv_layer
            roof_materials = materials
            is_external_bm = True

        cell_layer = bm_roof.faces.layers.int.get("cell_id")
        if cell_layer is None:
            cell_layer = bm_roof.faces.layers.int.new("cell_id")
        tw = ctx['wt'] / 2
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
            clean_corners = ctx['clean_wall_corners']
            if ctx['custom_roof'] or ctx['roof_meshes_list']:
                for seg_type, a, b in segments:
                    if seg_type == 'H':
                        owner_cell = (z, b, a) if b < maze_data.depth else (z, b - 1, a)
                    else:
                        owner_cell = (z, b, a) if a < maze_data.width else (z, b, a - 1)

                    if dirty_cells is not None and owner_cell not in dirty_cells:
                        continue

                    if dirty_cells is None:
                        start_idx = len(bm_roof.faces)
                    else:
                        existing_faces = set(bm_roof.faces)

                    if seg_type == 'H':
                        cx, cy = a * ctx['ts'] + ctx['ts'] / 2, b * ctx['ts']
                        if len(level_cells[0][0]) > 8:
                            target_y = min(b, maze_data.depth - 1)
                            roof_idx = level_cells[target_y][a][9] if len(level_cells[target_y][a]) > 9 else -1
                        else:
                            roof_idx = -1
                        
                        perp_left = ((a, b) in v_positions) + ((a, b - 1) in v_positions)
                        perp_right = ((a + 1, b) in v_positions) + ((a + 1, b - 1) in v_positions)
                        continues_left = (a - 1, b) in h_positions
                        continues_right = (a + 1, b) in h_positions
                        
                        dx_left = tw if (clean_corners and perp_left == 1 and not continues_left) else 0.0
                        dx_right = tw if (clean_corners and perp_right == 1 and not continues_right) else 0.0
                        
                        if clean_corners and (dx_left > 0.0 or dx_right > 0.0):
                            scale_x = (ctx['ts'] + dx_left + dx_right) / ctx['ts']
                            shift_x = (dx_right - dx_left) / 2
                            mat_scale_shift = Matrix.Translation(Vector((shift_x, 0, 0))) @ Matrix.Scale(scale_x, 4, Vector((1, 0, 0)))
                            mat_base = Matrix.Translation(Vector((cx, cy, sz))) @ mat_scale_shift
                        else:
                            mat_base = Matrix.Translation(Vector((cx, cy, sz)))
                    else:
                        cx, cy = a * ctx['ts'], b * ctx['ts'] + ctx['ts'] / 2
                        if len(level_cells[0][0]) > 8:
                            target_x = min(a, maze_data.width - 1)
                            roof_idx = level_cells[b][target_x][9] if len(level_cells[b][target_x]) > 9 else -1
                        else:
                            roof_idx = -1
                        
                        perp_south = ((a, b) in h_positions) + ((a - 1, b) in h_positions)
                        perp_north = ((a, b + 1) in h_positions) + ((a - 1, b + 1) in h_positions)
                        continues_south = (a, b - 1) in v_positions
                        continues_north = (a, b + 1) in v_positions
                        
                        dy_south = tw if (clean_corners and perp_south == 1 and not continues_south) else 0.0
                        dy_north = tw if (clean_corners and perp_north == 1 and not continues_north) else 0.0
                        
                        if clean_corners and (dy_south > 0.0 or dy_north > 0.0):
                            scale_y = (ctx['ts'] + dy_south + dy_north) / ctx['ts']
                            shift_y = (dy_north - dy_south) / 2
                            mat_scale_shift = Matrix.Translation(Vector((0, shift_y, 0))) @ Matrix.Scale(scale_y, 4, Vector((0, 1, 0)))
                            mat_base = Matrix.Translation(Vector((cx, cy, sz))) @ mat_scale_shift
                        else:
                            mat_base = Matrix.Translation(Vector((cx, cy, sz)))
                    
                    if ctx['roof_meshes_list'] and isinstance(roof_idx, int) and 0 <= roof_idx < len(ctx['roof_meshes_list']):
                        mat = mat_base @ ctx['mat_roof_offset']
                        _add_mesh_at(bm_roof, ctx['roof_meshes_list'][roof_idx], mat, uv_roof, final_materials_list=roof_materials)
                    elif ctx['custom_roof']:
                        mat = mat_base @ ctx['mat_roof_offset']
                        _add_mesh_at(bm_roof, ctx['custom_roof'], mat, uv_roof, final_materials_list=roof_materials)

                    cell_id = get_cell_id(z, owner_cell[1], owner_cell[2])
                    if dirty_cells is None:
                        bm_roof.faces.ensure_lookup_table()
                        for i in range(start_idx, len(bm_roof.faces)):
                            bm_roof.faces[i][cell_layer] = cell_id
                    else:
                        for f in bm_roof.faces:
                            if f not in existing_faces:
                                f[cell_layer] = cell_id
            else:
                filled = set()
                for seg_type, a, b in segments:
                    if seg_type == 'H':
                        owner_cell = (z, b, a) if b < maze_data.depth else (z, b - 1, a)
                    else:
                        owner_cell = (z, b, a) if a < maze_data.width else (z, b, a - 1)

                    if dirty_cells is not None and owner_cell not in dirty_cells:
                        continue

                    if dirty_cells is None:
                        start_idx = len(bm_roof.faces)
                    else:
                        existing_faces = set(bm_roof.faces)

                    if seg_type == 'H':
                        if clean_corners:
                            perp_left = ((a, b) in v_positions) + ((a, b - 1) in v_positions)
                            perp_right = ((a + 1, b) in v_positions) + ((a + 1, b - 1) in v_positions)
                            continues_left = (a - 1, b) in h_positions
                            continues_right = (a + 1, b) in h_positions
                            
                            dx_left = tw if (perp_left == 1 and not continues_left) else 0.0
                            dx_right = tw if (perp_right == 1 and not continues_right) else 0.0
                        else:
                            dx_left = 0.0
                            dx_right = 0.0
                            
                        # Horizontal roof face
                        _add_horizontal_roof_face_transformed(bm_roof, uv_roof, a, b, ctx['ts'], sz, ctx['wt'], ctx['mat_roof_offset'], extend_left=(dx_left > 0.0), extend_right=(dx_right > 0.0))
                        
                        # Add end cap fillers for thin walls
                        tleft = ((a, b) in v_positions) or ((a, b - 1) in v_positions)
                        tright = ((a + 1, b) in v_positions) or ((a + 1, b - 1) in v_positions)
                        xc = a * ctx['ts']
                        if tleft:
                            yc = b * ctx['ts']
                            y_lo = yc if (a, b - 1) not in v_positions else yc - tw
                            y_hi = yc if (a, b) not in v_positions else yc + tw
                            for gx, gy, side in [(a - 1, b - 1, 'r'), (a - 1, b, 'r')]:
                                key = (gx, gy, side)
                                if (gx, gy) not in h_positions and key not in filled:
                                    filled.add(key)
                                    _add_vertical_roof_filler_transformed(bm_roof, uv_roof, xc, yc, sz, tw, y_lo - yc, y_hi - yc, -tw, 0.0, ctx['mat_roof_offset'])
                        xc = (a + 1) * ctx['ts']
                        if tright:
                            yc = b * ctx['ts']
                            y_lo = yc if (a + 1, b - 1) not in v_positions else yc - tw
                            y_hi = yc if (a + 1, b) not in v_positions else yc + tw
                            for gx, gy, side in [(a + 1, b - 1, 'l'), (a + 1, b, 'l')]:
                                key = (gx, gy, side)
                                if (gx, gy) not in h_positions and key not in filled:
                                    filled.add(key)
                                    _add_vertical_roof_filler_transformed(bm_roof, uv_roof, xc, yc, sz, tw, y_lo - yc, y_hi - yc, 0.0, tw, ctx['mat_roof_offset'])
                    else:
                        if clean_corners:
                            perp_south = ((a, b) in h_positions) + ((a - 1, b) in h_positions)
                            perp_north = ((a, b + 1) in h_positions) + ((a - 1, b + 1) in h_positions)
                            continues_south = (a, b - 1) in v_positions
                            continues_north = (a, b + 1) in v_positions
                            
                            dy_south = tw if (perp_south == 1 and not continues_south) else 0.0
                            dy_north = tw if (perp_north == 1 and not continues_north) else 0.0
                        else:
                            dy_south = 0.0
                            dy_north = 0.0
                            
                        # Vertical roof face
                        _add_vertical_roof_face_transformed(bm_roof, uv_roof, a, b, ctx['ts'], sz, ctx['wt'], ctx['mat_roof_offset'], trim_south=(dy_south > 0.0), trim_north=(dy_north > 0.0))
                        
                        tsouth = ((a, b) in h_positions) or ((a - 1, b) in h_positions)
                        tnorth = ((a, b + 1) in h_positions) or ((a - 1, b + 1) in h_positions)
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
                            for gx, gy, side in [(a - 1, b + 1, 'l'), (a + 1, b + 1, 'r')]:
                                key = (gx, gy, side)
                                if (gx, gy) not in h_positions and key not in filled:
                                    filled.add(key)
                                    if side == 'l':
                                        hx0_rel, hx1_rel = -tw, 0
                                    else:
                                        hx0_rel, hx1_rel = 0, tw
                                    _add_vertical_roof_filler_transformed(bm_roof, uv_roof, xc, yc, sz, tw, y_lo - yc, y_hi - yc, hx0_rel, hx1_rel, ctx['mat_roof_offset'])

                    cell_id = get_cell_id(z, owner_cell[1], owner_cell[2])
                    if dirty_cells is None:
                        bm_roof.faces.ensure_lookup_table()
                        for i in range(start_idx, len(bm_roof.faces)):
                            bm_roof.faces[i][cell_layer] = cell_id
                    else:
                        for f in bm_roof.faces:
                            if f not in existing_faces:
                                f[cell_layer] = cell_id

        if not is_external_bm:
            if not ctx['custom_roof']:
                bmesh.ops.remove_doubles(bm_roof, verts=bm_roof.verts, dist=0.001)
            roof_obj = _create_object_from_bm(bm_roof, f"FireMaze_Roof{name_suffix}", ctx['col'], None)
            for mat in roof_materials:
                roof_obj.data.materials.append(mat)
            created_objects.append(roof_obj)



def build_maze_objects_impl(
    props: bpy.types.PropertyGroup,
    maze_data: MazeData,
    context: bpy.types.Context,
    collection: bpy.types.Collection = None,
    force_simple: bool = False,
    name_suffix: str = "",
    dirty_cells: set = None,
) -> bpy.types.Collection:
    """Build all rectangular maze meshes (floor, walls, roof, stairs, guide path, colliders) into a collection."""
    if maze_data.grid_type == 'polar':
        return _build_polar_maze_objects_impl(props, maze_data, context, collection, force_simple, name_suffix, dirty_cells=dirty_cells)

    ctx = _prepare_maze_building_context(props, maze_data, context, collection, force_simple)
    created_objects = []

    if props.wall_mode == 'cube':
        _build_rect_cube_floor(ctx, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
        _build_rect_cube_walls(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
        _build_rect_cube_roof(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
        _build_rect_stairs(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
    else:
        _build_rect_thin_floor(ctx, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
        _build_rect_thin_walls(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
        _build_rect_thin_roof(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
        _build_rect_stairs(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)


    # Build guide path if requested
    if not force_simple and name_suffix == "":
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

    # Perform merges and cleanups
    if props.remove_doubles:
        for obj in created_objects:
            _remove_doubles_on_obj(obj)

    # Merging logic
    if name_suffix == "_Collider":
        if props.merge_colliders:
            if props.optimize_colliders_coplanar:
                for obj in created_objects:
                    if obj.type == 'MESH':
                        _optimize_coplanar_on_obj(obj)
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
            merged_obj = _merge_maze_objects(meshes_to_merge, context, name="FireMaze_Merged")

    # Post-process visual mesh objects (optimize coplanar, vertex paint, lightmap)
    if not name_suffix and not props.is_editing:
        if props.merge_objects:
            visual_meshes = [merged_obj] if (merged_obj and merged_obj.type == 'MESH') else []
        else:
            visual_meshes = [obj for obj in created_objects if obj.type == 'MESH']
        
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
