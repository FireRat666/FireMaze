"""Rectangular grid builders and main mesh builder dispatcher for FireMaze."""

import math
import bpy
import bmesh
from mathutils import Matrix, Vector
from ..utils import is_valid_ref, get_rng
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
    wall_rng = get_rng()
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

                if seg_type == 'H':
                    x0, x1 = a * ctx['ts'], (a + 1) * ctx['ts']
                    yc = b * ctx['ts']
                    
                    perp_left = ((a, b) in v_positions) + ((a, b - 1) in v_positions)
                    perp_right = ((a + 1, b) in v_positions) + ((a + 1, b - 1) in v_positions)
                    continues_left = (a - 1, b) in h_positions
                    continues_right = (a + 1, b) in h_positions
                    
                    dx_left = tw if (clean_corners and perp_left == 1 and not continues_left) else 0.0
                    dx_right = tw if (clean_corners and perp_right == 1 and not continues_right) else 0.0
                    
                    def add_horizontal_face(direction, offset_rot, custom_mesh_fallback, y_offset, uvs_standard, local_pts):
                        """Place a horizontal wall face (along Y) using a custom mesh or procedural geometry."""
                        if ctx['wall_meshes_list']:
                            src_mesh = None
                            if isinstance(wall_idx, int) and 0 <= wall_idx < len(ctx['wall_meshes_list']):
                                src_mesh = ctx['wall_meshes_list'][wall_idx]
                            else:
                                src_mesh = wall_rng.choice(ctx['wall_meshes_list'])
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
                        
                        # West end-cap: dead end (no perp walls, doesn't continue)
                        if perp_left == 0 and not continues_left:
                            T = Matrix.Translation(Vector((x0 + ctx['ts']/2, yc, hw))) @ ctx['mat_wall_offset']
                            v_pts = [T @ Vector(p) for p in [(-ctx['ts']/2, tw, -sh/2), (-ctx['ts']/2, -tw, -sh/2), (-ctx['ts']/2, -tw, sh/2), (-ctx['ts']/2, tw, sh/2)]]
                            f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                            for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                loop[uv_cap].uv = uv
                        # East end-cap: dead end (no perp walls, doesn't continue)
                        if perp_right == 0 and not continues_right:
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
                            
                            # West end-cap: dead end (no perp walls, doesn't continue)
                            if perp_left == 0 and not continues_left:
                                T = Matrix.Translation(Vector((x0 + ctx['ts']/2, yc, hw))) @ ctx['mat_wall_offset']
                                v_pts = [T @ Vector(p) for p in [(-ctx['ts']/2, tw, -sh/2), (-ctx['ts']/2, -tw, -sh/2), (-ctx['ts']/2, -tw, sh/2), (-ctx['ts']/2, tw, sh/2)]]
                                f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                                for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                    loop[uv_cap].uv = uv
                            # East end-cap: dead end (no perp walls, doesn't continue)
                            if perp_right == 0 and not continues_right:
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
                    
                    perp_south = ((a, b) in h_positions) + ((a - 1, b) in h_positions)
                    perp_north = ((a, b + 1) in h_positions) + ((a - 1, b + 1) in h_positions)
                    continues_south = (a, b - 1) in v_positions
                    continues_north = (a, b + 1) in v_positions
                    
                    dy_south = tw if (clean_corners and perp_south == 1 and not continues_south) else 0.0
                    dy_north = tw if (clean_corners and perp_north == 1 and not continues_north) else 0.0
                    
                    def add_vertical_face(direction, offset_rot, custom_mesh_fallback, x_offset, uvs_standard, local_pts):
                        """Place a vertical wall face (along X) using a custom mesh or procedural geometry."""
                        if ctx['wall_meshes_list']:
                            src_mesh = None
                            if isinstance(wall_idx, int) and 0 <= wall_idx < len(ctx['wall_meshes_list']):
                                src_mesh = ctx['wall_meshes_list'][wall_idx]
                            else:
                                src_mesh = wall_rng.choice(ctx['wall_meshes_list'])
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
     
                        # South end-cap: dead end (no perp walls, doesn't continue)
                        if perp_south == 0 and not continues_south:
                            T = Matrix.Translation(Vector((xc, y0 + ctx['ts']/2, hw))) @ ctx['mat_wall_offset']
                            v_pts = [T @ Vector(p) for p in [(-tw, -ctx['ts']/2, -sh/2), (tw, -ctx['ts']/2, -sh/2), (tw, -ctx['ts']/2, sh/2), (-tw, -ctx['ts']/2, sh/2)]]
                            f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                            for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                loop[uv_cap].uv = uv
                        # North end-cap: dead end (no perp walls, doesn't continue)
                        if perp_north == 0 and not continues_north:
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
                            
                            # South end-cap: dead end (no perp walls, doesn't continue)
                            if perp_south == 0 and not continues_south:
                                T = Matrix.Translation(Vector((xc, y0 + ctx['ts']/2, hw))) @ ctx['mat_wall_offset']
                                v_pts = [T @ Vector(p) for p in [(-tw, -ctx['ts']/2, -sh/2), (tw, -ctx['ts']/2, -sh/2), (tw, -ctx['ts']/2, sh/2), (-tw, -ctx['ts']/2, sh/2)]]
                                f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                                for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                    loop[uv_cap].uv = uv
                            # North end-cap: dead end (no perp walls, doesn't continue)
                            if perp_north == 0 and not continues_north:
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


def _build_rect_thin_roof(ctx, props, maze_data, created_objects, name_suffix):
    # Roof
    if name_suffix != "_EditHelper":
        bm_roof, uv_roof, roof_materials = _create_bmesh_element("roof", ctx['materials'])
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
            else:
                filled = set()
                for seg_type, a, b in segments:
                    if seg_type == 'H':
                        if clean_corners:
                            perp_left = ((a, b) in v_positions) + ((a, b - 1) in v_positions)
                            perp_right = ((a + 1, b) in v_positions) + ((a + 1, b - 1) in v_positions)
                            continues_left = (a - 1, b) in h_positions
                            continues_right = (a + 1, b) in h_positions
                            
                            extend_left = clean_corners and perp_left == 1 and not continues_left
                            extend_right = clean_corners and perp_right == 1 and not continues_right
                            _add_horizontal_roof_face_transformed(bm_roof, uv_roof, a, b, ctx['ts'], sz, ctx['wt'], ctx['mat_roof_offset'], extend_left=extend_left, extend_right=extend_right)
                        else:
                            _add_horizontal_roof_face_transformed(bm_roof, uv_roof, a, b, ctx['ts'], sz, ctx['wt'], ctx['mat_roof_offset'])
                    else:
                        if clean_corners:
                            perp_south = ((a, b) in h_positions) + ((a - 1, b) in h_positions)
                            perp_north = ((a, b + 1) in h_positions) + ((a - 1, b + 1) in h_positions)
                            continues_south = (a, b - 1) in v_positions
                            continues_north = (a, b + 1) in v_positions
                            
                            extend_south = clean_corners and perp_south == 1 and not continues_south
                            extend_north = clean_corners and perp_north == 1 and not continues_north
                            _add_vertical_roof_face_transformed(bm_roof, uv_roof, a, b, ctx['ts'], sz, ctx['wt'], ctx['mat_roof_offset'], trim_south=False, trim_north=False, extend_south=extend_south, extend_north=extend_north)
                        else:
                            tsouth = (a, b) in h_endpoints
                            tnorth = (a, b + 1) in h_endpoints
                            _add_vertical_roof_face_transformed(bm_roof, uv_roof, a, b, ctx['ts'], sz, ctx['wt'], ctx['mat_roof_offset'], trim_south=tsouth, trim_north=tnorth)
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
        _build_rect_thin_roof(ctx, props, maze_data, created_objects, name_suffix)
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
