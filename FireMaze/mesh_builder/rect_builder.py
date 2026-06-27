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
    _add_clipped_custom_mesh_at,
    _add_floor_tile_transformed,
    _merge_bmesh_geometries,
    _create_object_from_bm,
    _add_wall_face_transformed,
    _safe_remove_doubles,
    _add_cube_roof_face_transformed,
    _prepare_maze_building_context,
    _get_wall_segments,
    _add_horizontal_roof_face_transformed,
    _add_vertical_roof_face_transformed,
    _add_vertical_roof_filler_transformed,
    _add_horizontal_roof_filler_transformed,
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


def _is_boundary_cell(x, y, w, d, shape_blocked):
    """Return True if cell (x,y) is adjacent to a cell of opposite blocked status (including diagonals)."""
    cell_blocked = shape_blocked[y][x]
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx, ny = x + dx, y + dy
            if nx < 0 or nx >= w or ny < 0 or ny >= d:
                neighbor_blocked = True
            else:
                neighbor_blocked = shape_blocked[ny][nx]
            if neighbor_blocked != cell_blocked:
                return True
    return False


def _add_clipped_floor_tile(bm, uv_layer, x, y, w, d, ts, shape, rotation, z_offset, mat_offset, thickness=0.0, offset=0.0):
    """Clip a floor tile at cell (x, y) to the mathematical shape boundary wall contour using BMesh bisect."""
    from ..shape_boundaries import get_perfect_shape_polygon
    poly = get_perfect_shape_polygon(shape, rotation, w, d, offset)
    if not poly:
        return False

    temp_bm = bmesh.new()
    temp_uv = temp_bm.loops.layers.uv.new(uv_layer.name)

    _add_floor_tile_transformed(
        temp_bm, temp_uv, x, y, ts, mat_offset, z_offset=z_offset, thickness=0.0
    )

    n = len(poly)
    world_poly = [Vector((u * w * ts, v * d * ts, 0.0)) for u, v in poly]

    for i in range(n):
        p0 = world_poly[i]
        p1 = world_poly[(i + 1) % n]
        dx = p1.x - p0.x
        dy = p1.y - p0.y
        plane_co = p0
        plane_no = Vector((dy, -dx, 0.0))
        if plane_no.length > 1e-6:
            plane_no.normalize()
            center = Vector((w * ts / 2, d * ts / 2, 0.0))
            to_center = center - plane_co
            if plane_no.dot(to_center) > 0.0:
                plane_no = -plane_no

            if temp_bm.verts:
                bmesh.ops.bisect_plane(
                    temp_bm,
                    geom=temp_bm.verts[:] + temp_bm.edges[:] + temp_bm.faces[:],
                    plane_co=plane_co,
                    plane_no=plane_no,
                    clear_outer=True
                )

    # Remove degenerate/zero-area faces
    for face in list(temp_bm.faces):
        if face.calc_area() < 1e-5:
            temp_bm.faces.remove(face)

    if not temp_bm.faces:
        temp_bm.free()
        return True

    # Clean up loose/unused geometry
    loose_verts = [v for v in temp_bm.verts if not v.link_faces]
    if loose_verts:
        bmesh.ops.delete(temp_bm, geom=loose_verts, context='VERTS')

    # If thickness > 0, thicken the clipped flat shape into a watertight 3D shell
    if thickness > 0:
        temp_bm.verts.ensure_lookup_table()
        temp_bm.faces.ensure_lookup_table()

        top_face = temp_bm.faces[0]
        top_face.material_index = 0

        loop_verts = list(top_face.verts)
        n_verts = len(loop_verts)

        top_uvs = []
        for loop in top_face.loops:
            top_uvs.append(loop[temp_uv].uv[:])

        bottom_verts = []
        for v in loop_verts:
            bv = temp_bm.verts.new(Vector((v.co.x, v.co.y, v.co.z - thickness)))
            bottom_verts.append(bv)

        bottom_reversed = list(reversed(bottom_verts))
        bottom_face = temp_bm.faces.new(bottom_reversed)
        bottom_face.material_index = 1
        for j in range(n_verts):
            bottom_face.loops[j][temp_uv].uv = top_uvs[n_verts - 1 - j]

        edge_lengths = [0.0]
        total_len = 0.0
        for i in range(n_verts):
            p1 = loop_verts[i].co
            p2 = loop_verts[(i + 1) % n_verts].co
            total_len += (p2 - p1).length
            edge_lengths.append(total_len)

        for i in range(n_verts):
            i2 = (i + 1) % n_verts
            v_top1 = loop_verts[i]
            v_top2 = loop_verts[i2]
            v_bot1 = bottom_verts[i]
            v_bot2 = bottom_verts[i2]

            side = temp_bm.faces.new([v_top1, v_bot1, v_bot2, v_top2])
            side.material_index = 2

            u0 = edge_lengths[i] / total_len if total_len > 0 else 0.0
            u1 = edge_lengths[i + 1] / total_len if total_len > 0 else 1.0
            side.loops[0][temp_uv].uv = (u0, 1.0)
            side.loops[1][temp_uv].uv = (u0, 0.0)
            side.loops[2][temp_uv].uv = (u1, 0.0)
            side.loops[3][temp_uv].uv = (u1, 1.0)

    temp_bm.verts.ensure_lookup_table()
    temp_bm.faces.ensure_lookup_table()
    _merge_bmesh_geometries(temp_bm, bm)
    temp_bm.free()
    return True


def _build_rect_cube_floor(ctx, props, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, dirty_cells=None):
    """Build cube-mode floor tiles for a rectangular grid, optionally updating an existing BMesh."""
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
    pad = 3 if props.smooth_shape_edges and getattr(props, 'smooth_boundary_method', 'filler') == 'clip' else 0
    from ..shape_boundaries import get_cell_clip_status

    for z in ctx['z_range']:
        z_off = z * ctx['level_height']
        level_cells = ctx['cells_3d'][z]
        for y in range(-pad, maze_data.depth + pad):
            for x in range(-pad, maze_data.width + pad):
                # Clamp coordinates for dirty cells check and cell_id encoding
                y_clamp = max(0, min(maze_data.depth - 1, y))
                x_clamp = max(0, min(maze_data.width - 1, x))

                if dirty_cells is not None and (z, y_clamp, x_clamp) not in dirty_cells:
                    continue

                is_in_grid = (0 <= x < maze_data.width and 0 <= y < maze_data.depth)

                if is_in_grid and (z, y, x) in ctx['stair_top_cells']:
                    continue

                is_blocked = not is_in_grid or (ctx.get('shape_blocked') is not None and ctx['shape_blocked'][y][x])
                
                # Determine floor generation and clipping status
                if is_blocked:
                    if not (props.smooth_shape_edges and getattr(props, 'smooth_boundary_method', 'filler') == 'clip'):
                        continue
                    # Check clipping status for this blocked cell
                    status = get_cell_clip_status(x, y, maze_data.width, maze_data.depth, props.maze_shape, props.shape_rotation, props.smooth_boundary_offset)
                    if status == 'none':
                        continue
                    use_clip = (status == 'clip')
                else:
                    # Active floor cell: check if we should clip it (only when smooth edges & clip are active)
                    if props.smooth_shape_edges and getattr(props, 'smooth_boundary_method', 'filler') == 'clip':
                        status = get_cell_clip_status(x, y, maze_data.width, maze_data.depth, props.maze_shape, props.shape_rotation, props.smooth_boundary_offset)
                        use_clip = (status == 'clip')
                        if status == 'none':
                            continue
                    else:
                        use_clip = False

                if dirty_cells is None:
                    start_idx = len(bm_floor.faces)
                else:
                    existing_faces = set(bm_floor.faces)

                floor_idx = -1
                if not is_blocked:
                    floor_idx = level_cells[y][x][5] if len(level_cells[y][x]) > 5 else -1
                if floor_idx < 0 and ctx['floor_meshes_list']:
                    floor_idx = 0

                has_custom = False
                if ctx['floor_meshes_list'] and isinstance(floor_idx, int) and 0 <= floor_idx < len(ctx['floor_meshes_list']):
                    has_custom = True
                    mat_base = Matrix.Translation(Vector((x * ctx['ts'] + off, y * ctx['ts'] + off, z_off - ctx['ft'] / 2)))
                    mat = mat_base @ ctx['mat_floor_offset']
                    if use_clip:
                        _add_clipped_custom_mesh_at(
                            bm_floor, ctx['floor_meshes_list'][floor_idx], mat, uv_floor, floor_materials,
                            x, y, maze_data.width, maze_data.depth, ctx['ts'], props.maze_shape, props.shape_rotation,
                            offset=props.smooth_boundary_offset
                        )
                    else:
                        _add_mesh_at(bm_floor, ctx['floor_meshes_list'][floor_idx], mat, uv_floor, final_materials_list=floor_materials)
                elif ctx['custom_floor']:
                    has_custom = True
                    mat_base = Matrix.Translation(Vector((x * ctx['ts'] + off, y * ctx['ts'] + off, z_off - ctx['ft'] / 2)))
                    mat = mat_base @ ctx['mat_floor_offset']
                    if use_clip:
                        _add_clipped_custom_mesh_at(
                            bm_floor, ctx['custom_floor'], mat, uv_floor, floor_materials,
                            x, y, maze_data.width, maze_data.depth, ctx['ts'], props.maze_shape, props.shape_rotation,
                            offset=props.smooth_boundary_offset
                        )
                    else:
                        _add_mesh_at(bm_floor, ctx['custom_floor'], mat, uv_floor, final_materials_list=floor_materials)

                if not has_custom:
                    if use_clip:
                        clipped = _add_clipped_floor_tile(
                            bm_floor, uv_floor, x, y, maze_data.width, maze_data.depth,
                            ctx['ts'], props.maze_shape, props.shape_rotation,
                            z_offset=z_off, mat_offset=ctx['mat_floor_offset'],
                            thickness=ctx['ft'], offset=props.smooth_boundary_offset)
                        if not clipped:
                            _add_floor_tile_transformed(bm_floor, uv_floor, x, y, ctx['ts'], ctx['mat_floor_offset'], z_offset=z_off, thickness=ctx['ft'])
                    else:
                        _add_floor_tile_transformed(bm_floor, uv_floor, x, y, ctx['ts'], ctx['mat_floor_offset'], z_offset=z_off, thickness=ctx['ft'])

                cell_id = get_cell_id(z, y_clamp, x_clamp)
                if dirty_cells is None:
                    bm_floor.faces.ensure_lookup_table()
                    for i in range(start_idx, len(bm_floor.faces)):
                        bm_floor.faces[i][cell_layer] = cell_id
                else:
                    for f in bm_floor.faces:
                        if f not in existing_faces:
                            f[cell_layer] = cell_id

    if getattr(props, 'smooth_boundary_method', 'filler') != 'clip':
        _build_smooth_floor_triangles(ctx, props, maze_data, bm_floor, uv_floor, cell_layer, dirty_cells)

    if not is_external_bm:
        floor_obj = _create_object_from_bm(bm_floor, f"FireMaze_Floor{name_suffix}", ctx['col'], None)
        for mat in floor_materials:
            floor_obj.data.materials.append(mat)
        created_objects.append(floor_obj)



def _build_rect_cube_walls(ctx, props, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, dirty_cells=None):
    """Build cube-mode wall geometry for a rectangular grid, optionally updating an existing BMesh."""
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
    smooth_edges = props.smooth_shape_edges and ctx.get('shape_blocked') is not None
    for z in ctx['z_range']:
        z_off_floor = z * ctx['level_height']
        level_cells = ctx['cells_3d'][z]
        for level in range(ctx['tiles_high']):
            z_off = z_off_floor + level * ctx['seg_h']
            hw = z_off + ctx['seg_h'] / 2
            for y in range(maze_data.depth):
                for x in range(maze_data.width):
                    if dirty_cells is not None and (z, y, x) not in dirty_cells:
                        continue

                    if ctx.get('shape_blocked') is not None and ctx['shape_blocked'][y][x]:
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
                                """Place a single cube-mode wall face using mesh library, custom mesh, or procedural geometry."""
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
                            sb = ctx.get('shape_blocked')
                            if y + 1 >= maze_data.depth or (sb is not None and sb[y + 1][x]) or not level_cells[y + 1][x][0]:
                                f_idx = level_cells[y][x][1] if len(level_cells[y][x]) > 1 else -1
                                place_wall_face('+Y', Matrix.Translation(Vector((0, ctx['ts']/2, 0))) @ Matrix.Rotation(math.radians(-90), 4, 'X') @ Matrix.Rotation(math.radians(180), 4, 'Z'), ctx['custom_wall'], f_idx)
                            # -Y (south)
                            if y - 1 < 0 or (sb is not None and sb[y - 1][x]) or not level_cells[y - 1][x][0]:
                                f_idx = level_cells[y][x][2] if len(level_cells[y][x]) > 2 else -1
                                place_wall_face('-Y', Matrix.Translation(Vector((0, -ctx['ts']/2, 0))) @ Matrix.Rotation(math.radians(90), 4, 'X'), ctx['custom_wall'], f_idx)
                            # +X (east)
                            if x + 1 >= maze_data.width or (sb is not None and sb[y][x + 1]) or not level_cells[y][x + 1][0]:
                                f_idx = level_cells[y][x][3] if len(level_cells[y][x]) > 3 else -1
                                place_wall_face('+X', Matrix.Translation(Vector((ctx['ts']/2, 0, 0))) @ Matrix.Rotation(math.radians(-90), 4, 'Z') @ Matrix.Rotation(math.radians(-90), 4, 'X') @ Matrix.Rotation(math.radians(180), 4, 'Z'), ctx['custom_wall'], f_idx)
                            # -X (west)
                            if x - 1 < 0 or (sb is not None and sb[y][x - 1]) or not level_cells[y][x - 1][0]:
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

        if smooth_edges:
            from ..shape_boundaries import _ROTATION_ANGLES, _rotate_point
            w = maze_data.width
            d = maze_data.depth
            ts = ctx['ts']
            sb = ctx['shape_blocked']
            angle_rad = _ROTATION_ANGLES.get(props.shape_rotation, 0.0)

            is_clip_mode = (getattr(props, 'smooth_boundary_method', 'filler') == 'clip')
            corner_items = []
            if is_clip_mode:
                # In cube mode, do not generate slanted/smooth mathematical boundary walls.
                pass
            else:
                # Filler mode: collect corners of active grid boundary cells facing outside using original logic
                from ..shape_boundaries import _SHAPE_TESTS
                test_fn_filler = _SHAPE_TESTS.get(props.maze_shape)
                if test_fn_filler is not None:
                    def _pt_outside_filler(u, v):
                        pu, pv = u, v
                        if angle_rad != 0.0:
                            pu, pv = _rotate_point(pu, pv, -angle_rad)
                        return not test_fn_filler(pu, pv)

                    corner_dict = {}
                    for y in range(d):
                        for x in range(w):
                            if sb[y][x]:
                                continue
                            boundary = False
                            for ny, nx in ((y-1,x),(y+1,x),(y,x-1),(y,x+1)):
                                if nx < 0 or nx >= w or ny < 0 or ny >= d or sb[ny][nx]:
                                    boundary = True
                                    break
                            if not boundary:
                                continue
                            for cu, cv in [(x, y), (x+1, y), (x+1, y+1), (x, y+1)]:
                                u, v = cu / w, cv / d
                                if _pt_outside_filler(u, v):
                                    key = (cu * ts, cv * ts)
                                    corner_dict.setdefault(key, set()).add((x, y))

                    if corner_dict:
                        corner_items = [(wx, wy, frozenset(cells)) for (wx, wy), cells in corner_dict.items()]
                        cx_center = w * ts / 2
                        cy_center = d * ts / 2
                        corner_items.sort(key=lambda c: math.atan2(c[1] - cy_center, c[0] - cx_center))

            n = len(corner_items)
            for idx in range(n):
                c1 = corner_items[idx]
                c2 = corner_items[(idx + 1) % n]

                x0, y0 = c1[0], c1[1]
                x1, y1 = c2[0], c2[1]

                level_cells = ctx['cells_3d'][z]

                # Identify all cells associated with this segment (either sharing corners, or close to it).
                associated_cells = set()
                for cell_set in (c1[2], c2[2]):
                    associated_cells.update(cell_set)

                min_cx = max(0, int(min(x0, x1) / ts - 1))
                max_cx = min(w - 1, int(max(x0, x1) / ts + 1))
                min_cy = max(0, int(min(y0, y1) / ts - 1))
                max_cy = min(d - 1, int(max(y0, y1) / ts + 1))

                for cy in range(min_cy, max_cy + 1):
                    for cx in range(min_cx, max_cx + 1):
                        if sb[cy][cx]:
                            continue
                        ccx = cx * ts + ts / 2
                        ccy = cy * ts + ts / 2
                        dx = x1 - x0
                        dy = y1 - y0
                        if dx == 0 and dy == 0:
                            dist = math.hypot(ccx - x0, ccy - y0)
                        else:
                            t = ((ccx - x0) * dx + (ccy - y0) * dy) / (dx * dx + dy * dy)
                            t = max(0.0, min(1.0, t))
                            qx = x0 + t * dx
                            qy = y0 + t * dy
                            dist = math.hypot(ccx - qx, ccy - qy)

                        if dist < 1.25 * ts:
                            associated_cells.add((cx, cy))

                # Skip segment if any cell sharing either corner or close to it is a
                # walkable floor tile (entrance, exit, or open passage).
                skip_seg = False
                for (cx, cy) in associated_cells:
                    if 0 <= cx < w and 0 <= cy < d:
                        if not level_cells[cy][cx][0]:
                            skip_seg = True
                            break
                if skip_seg:
                    continue

                has_dirty = dirty_cells is None
                if not has_dirty:
                    for (cx, cy) in associated_cells:
                        cx_clamp = max(0, min(w - 1, cx))
                        cy_clamp = max(0, min(d - 1, cy))
                        if (z, cy_clamp, cx_clamp) in dirty_cells:
                            has_dirty = True
                            break
                if not has_dirty:
                    continue

                ref_cx, ref_cy = next(iter(c1[2]))
                z_off_floor = z * ctx['level_height']
                cx_w = ref_cx * ts + ts / 2
                cy_w = ref_cy * ts + ts / 2

                for level in range(ctx['tiles_high']):
                    z_off = z_off_floor + level * ctx['seg_h']
                    hw = z_off + ctx['seg_h'] / 2
                    start_idx = len(bm_wall.faces)

                    v0_l = Vector((x0 - cx_w, y0 - cy_w, 0.0))
                    v1_l = Vector((x1 - cx_w, y1 - cy_w, 0.0))

                    pts = [
                        Vector((v0_l.x, v0_l.y, -ctx['seg_h']/2)),
                        Vector((v1_l.x, v1_l.y, -ctx['seg_h']/2)),
                        Vector((v1_l.x, v1_l.y, ctx['seg_h']/2)),
                        Vector((v0_l.x, v0_l.y, ctx['seg_h']/2))
                    ]
                    final_pts = []
                    for p in pts:
                        p_trans = Matrix.Translation(Vector((cx_w, cy_w, hw))) @ ctx['mat_wall_offset'] @ p
                        final_pts.append(p_trans)

                    f_verts = [bm_wall.verts.new(pt) for pt in final_pts]
                    face = bm_wall.faces.new(f_verts)
                    for loop, uv in zip(face.loops, [(0,0),(1,0),(1,1),(0,1)]):
                        loop[uv_wall].uv = uv

                    ref_cx_clamp = max(0, min(w - 1, ref_cx))
                    ref_cy_clamp = max(0, min(d - 1, ref_cy))
                    cell_id = get_cell_id(z, ref_cy_clamp, ref_cx_clamp)
                    bm_wall.faces.ensure_lookup_table()
                    for i_face in range(start_idx, len(bm_wall.faces)):
                        bm_wall.faces[i_face][cell_layer] = cell_id

    if not is_external_bm:
        _safe_remove_doubles(bm_wall, dist=0.001)
        wall_obj = _create_object_from_bm(bm_wall, f"FireMaze_Walls{name_suffix}", ctx['col'], None)
        for mat in wall_materials:
            wall_obj.data.materials.append(mat)
        created_objects.append(wall_obj)



def _build_smooth_roof_triangles(ctx, props, maze_data, bm_roof, uv_roof, cell_layer, dirty_cells=None):
    """Add triangular roof faces at roof height along smooth shape edges."""
    if not props.smooth_shape_edges or ctx.get('shape_blocked') is None:
        return

    from ..shape_boundaries import _SHAPE_TESTS_STRICT, _ROTATION_ANGLES, _rotate_point

    test_fn = _SHAPE_TESTS_STRICT.get(props.maze_shape)
    if test_fn is None:
        return

    angle_rad = _ROTATION_ANGLES.get(props.shape_rotation, 0.0)
    def _pt_outside(u, v):
        """Return True if (u,v) in normalized space falls outside the smooth roof shape boundary (rotation-aware)."""
        pu, pv = u, v
        if angle_rad != 0.0:
            pu, pv = _rotate_point(pu, pv, -angle_rad)
        return not test_fn(pu, pv)

    w = maze_data.width
    d = maze_data.depth
    ts = ctx['ts']
    sb = ctx['shape_blocked']

    corner_dict = {}
    for y in range(d):
        for x in range(w):
            if sb[y][x]:
                continue
            boundary = False
            for ny, nx in ((y-1,x),(y+1,x),(y,x-1),(y,x+1)):
                if nx < 0 or nx >= w or ny < 0 or ny >= d or sb[ny][nx]:
                    boundary = True
                    break
            if not boundary:
                continue
            for cu, cv in [(x, y), (x+1, y), (x+1, y+1), (x, y+1)]:
                u, v = cu / w, cv / d
                if _pt_outside(u, v):
                    key = (cu * ts, cv * ts)
                    corner_dict.setdefault(key, set()).add((x, y))

    if not corner_dict:
        return

    corner_items = [(wx, wy, frozenset(cells)) for (wx, wy), cells in corner_dict.items()]
    cx_center = w * ts / 2
    cy_center = d * ts / 2
    corner_items.sort(key=lambda c: math.atan2(c[1] - cy_center, c[0] - cx_center))

    n = len(corner_items)
    for idx in range(n):
        c1 = corner_items[idx]
        c2 = corner_items[(idx + 1) % n]

        x0, y0 = c1[0], c1[1]
        x1, y1 = c2[0], c2[1]

        for z in ctx['z_range']:
            level_cells = ctx['cells_3d'][z]

            associated_cells = set()
            for cell_set in (c1[2], c2[2]):
                associated_cells.update(cell_set)

            min_cx = max(0, int(min(x0, x1) / ts - 1))
            max_cx = min(w - 1, int(max(x0, x1) / ts + 1))
            min_cy = max(0, int(min(y0, y1) / ts - 1))
            max_cy = min(d - 1, int(max(y0, y1) / ts + 1))

            for cy in range(min_cy, max_cy + 1):
                for cx in range(min_cx, max_cx + 1):
                    if sb[cy][cx]:
                        continue
                    ccx = cx * ts + ts / 2
                    ccy = cy * ts + ts / 2
                    dx = x1 - x0
                    dy = y1 - y0
                    if dx == 0 and dy == 0:
                        dist = math.hypot(ccx - x0, ccy - y0)
                    else:
                        t = ((ccx - x0) * dx + (ccy - y0) * dy) / (dx * dx + dy * dy)
                        t = max(0.0, min(1.0, t))
                        qx = x0 + t * dx
                        qy = y0 + t * dy
                        dist = math.hypot(ccx - qx, ccy - qy)
                    if dist < 1.25 * ts:
                        associated_cells.add((cx, cy))

            skip_seg = False
            for (cx, cy) in associated_cells:
                if not level_cells[cy][cx][0]:
                    skip_seg = True
                    break
            if skip_seg:
                continue

            has_dirty = dirty_cells is None
            if not has_dirty:
                for (cx, cy) in associated_cells:
                    if (z, cy, cx) in dirty_cells:
                        has_dirty = True
                        break
            if not has_dirty:
                continue

            ref_cx, ref_cy = next(iter(c1[2]))
            z_off = z * ctx['level_height']
            cx_w = ref_cx * ts + ts / 2
            cy_w = ref_cy * ts + ts / 2
            roof_z = z_off + ctx['wh']

            gx0 = int(round(x0 / ts))
            gy0 = int(round(y0 / ts))
            gx1 = int(round(x1 / ts))
            gy1 = int(round(y1 / ts))

            kx, ky = None, None
            for cx, cy in [(gx0, gy1), (gx1, gy0)]:
                if (cx, cy) != (gx0, gy0) and (cx, cy) != (gx1, gy1):
                    u, v = cx / w, cy / d
                    if not _pt_outside(u, v):
                        kx, ky = cx, cy
                        break

            if kx is not None:
                kwx, kwy = kx * ts, ky * ts

                v0_w = Vector((x0, y0, roof_z))
                v1_w = Vector((x1, y1, roof_z))
                vk_w = Vector((kwx, kwy, roof_z))

                v0_2d = (x0, y0)
                v1_2d = (x1, y1)
                vk_2d = (kwx, kwy)
                cross_z = (v1_2d[0] - v0_2d[0]) * (vk_2d[1] - v0_2d[1]) - (v1_2d[1] - v0_2d[1]) * (vk_2d[0] - v0_2d[0])

                if cross_z > 0:
                    tri_w = [v0_w, v1_w, vk_w]
                else:
                    tri_w = [v0_w, vk_w, v1_w]

                center = Vector((cx_w, cy_w, roof_z))
                pts = [Matrix.Translation(center) @ ctx['mat_roof_offset'] @ (p - center) for p in tri_w]
                f_verts = [bm_roof.verts.new(pt) for pt in pts]
                face = bm_roof.faces.new(f_verts)

                for loop, p in zip(face.loops, tri_w):
                    loop[uv_roof].uv = ((p.x - ref_cx * ts) / ts, (p.y - ref_cy * ts) / ts)

                face[cell_layer] = get_cell_id(z, ref_cy, ref_cx)


def _build_smooth_floor_triangles(ctx, props, maze_data, bm_floor, uv_floor, cell_layer, dirty_cells=None):
    """Add triangular floor faces at floor height along smooth shape edges to fill gaps."""
    if not props.smooth_shape_edges or ctx.get('shape_blocked') is None:
        return

    from ..shape_boundaries import _SHAPE_TESTS_STRICT, _ROTATION_ANGLES, _rotate_point

    test_fn = _SHAPE_TESTS_STRICT.get(props.maze_shape)
    if test_fn is None:
        return

    angle_rad = _ROTATION_ANGLES.get(props.shape_rotation, 0.0)
    def _pt_outside(u, v):
        """Return True if (u,v) in normalized space falls outside the smooth floor shape boundary (rotation-aware)."""
        pu, pv = u, v
        if angle_rad != 0.0:
            pu, pv = _rotate_point(pu, pv, -angle_rad)
        return not test_fn(pu, pv)

    w = maze_data.width
    d = maze_data.depth
    ts = ctx['ts']
    sb = ctx['shape_blocked']

    corner_dict = {}
    for y in range(d):
        for x in range(w):
            if sb[y][x]:
                continue
            boundary = False
            for ny, nx in ((y-1,x),(y+1,x),(y,x-1),(y,x+1)):
                if nx < 0 or nx >= w or ny < 0 or ny >= d or sb[ny][nx]:
                    boundary = True
                    break
            if not boundary:
                continue
            for cu, cv in [(x, y), (x+1, y), (x+1, y+1), (x, y+1)]:
                u, v = cu / w, cv / d
                if _pt_outside(u, v):
                    key = (cu * ts, cv * ts)
                    corner_dict.setdefault(key, set()).add((x, y))

    if not corner_dict:
        return

    corner_items = [(wx, wy, frozenset(cells)) for (wx, wy), cells in corner_dict.items()]
    cx_center = w * ts / 2
    cy_center = d * ts / 2
    corner_items.sort(key=lambda c: math.atan2(c[1] - cy_center, c[0] - cx_center))

    n = len(corner_items)
    for idx in range(n):
        c1 = corner_items[idx]
        c2 = corner_items[(idx + 1) % n]

        x0, y0 = c1[0], c1[1]
        x1, y1 = c2[0], c2[1]

        for z in ctx['z_range']:
            level_cells = ctx['cells_3d'][z]

            associated_cells = set()
            for cell_set in (c1[2], c2[2]):
                associated_cells.update(cell_set)

            min_cx = max(0, int(min(x0, x1) / ts - 1))
            max_cx = min(w - 1, int(max(x0, x1) / ts + 1))
            min_cy = max(0, int(min(y0, y1) / ts - 1))
            max_cy = min(d - 1, int(max(y0, y1) / ts + 1))

            for cy in range(min_cy, max_cy + 1):
                for cx in range(min_cx, max_cx + 1):
                    if sb[cy][cx]:
                        continue
                    ccx = cx * ts + ts / 2
                    ccy = cy * ts + ts / 2
                    dx = x1 - x0
                    dy = y1 - y0
                    if dx == 0 and dy == 0:
                        dist = math.hypot(ccx - x0, ccy - y0)
                    else:
                        t = ((ccx - x0) * dx + (ccy - y0) * dy) / (dx * dx + dy * dy)
                        t = max(0.0, min(1.0, t))
                        qx = x0 + t * dx
                        qy = y0 + t * dy
                        dist = math.hypot(ccx - qx, ccy - qy)
                    if dist < 1.25 * ts:
                        associated_cells.add((cx, cy))

            has_dirty = dirty_cells is None
            if not has_dirty:
                for (cx, cy) in associated_cells:
                    if (z, cy, cx) in dirty_cells:
                        has_dirty = True
                        break
            if not has_dirty:
                continue

            ref_cx, ref_cy = next(iter(c1[2]))
            z_off = z * ctx['level_height']
            cx_w = ref_cx * ts + ts / 2
            cy_w = ref_cy * ts + ts / 2
            floor_z = z_off

            gx0 = int(round(x0 / ts))
            gy0 = int(round(y0 / ts))
            gx1 = int(round(x1 / ts))
            gy1 = int(round(y1 / ts))

            kx, ky = None, None
            for cx, cy in [(gx0, gy1), (gx1, gy0)]:
                if (cx, cy) != (gx0, gy0) and (cx, cy) != (gx1, gy1):
                    u, v = cx / w, cy / d
                    if not _pt_outside(u, v):
                        kx, ky = cx, cy
                        break

            if kx is not None:
                kwx, kwy = kx * ts, ky * ts

                v0_w = Vector((x0, y0, floor_z))
                v1_w = Vector((x1, y1, floor_z))
                vk_w = Vector((kwx, kwy, floor_z))

                v0_2d = (x0, y0)
                v1_2d = (x1, y1)
                vk_2d = (kwx, kwy)
                cross_z = (v1_2d[0] - v0_2d[0]) * (vk_2d[1] - v0_2d[1]) - (v1_2d[1] - v0_2d[1]) * (vk_2d[0] - v0_2d[0])

                if cross_z > 0:
                    tri_w = [v0_w, v1_w, vk_w]
                else:
                    tri_w = [v0_w, vk_w, v1_w]

                ft = ctx['ft']
                center = Vector((cx_w, cy_w, floor_z))

                pts = [Matrix.Translation(center) @ ctx['mat_floor_offset'] @ (p - center) for p in tri_w]
                f_verts = [bm_floor.verts.new(pt) for pt in pts]
                f_top = bm_floor.faces.new(f_verts)
                f_top.material_index = 0
                for loop, p in zip(f_top.loops, tri_w):
                    loop[uv_floor].uv = ((p.x - ref_cx * ts) / ts, (p.y - ref_cy * ts) / ts)
                f_top[cell_layer] = get_cell_id(z, ref_cy, ref_cx)

                if ft > 0:
                    tri_w_bot = [Vector((p.x, p.y, p.z - ft)) for p in tri_w]
                    pts_bot = [Matrix.Translation(center) @ ctx['mat_floor_offset'] @ (p - center) for p in reversed(tri_w_bot)]
                    f_verts_bot = [bm_floor.verts.new(pt) for pt in pts_bot]
                    f_bot = bm_floor.faces.new(f_verts_bot)
                    f_bot.material_index = 1
                    for loop, p in zip(f_bot.loops, reversed(tri_w_bot)):
                        loop[uv_floor].uv = ((p.x - ref_cx * ts) / ts, (p.y - ref_cy * ts) / ts)
                    f_bot[cell_layer] = get_cell_id(z, ref_cy, ref_cx)

                    edges_idx = [(0, 1), (1, 2), (2, 0)]
                    for i0, i1 in edges_idx:
                        side_pts = [
                            tri_w[i0], tri_w[i1],
                            tri_w_bot[i1], tri_w_bot[i0],
                        ]
                        side_world = [Matrix.Translation(center) @ ctx['mat_floor_offset'] @ (p - center) for p in side_pts]
                        side_verts = [bm_floor.verts.new(p) for p in side_world]
                        f_side = bm_floor.faces.new(side_verts)
                        f_side.material_index = 2
                        f_side.loops[0][uv_floor].uv = (0.0, 1.0)
                        f_side.loops[1][uv_floor].uv = (1.0, 1.0)
                        f_side.loops[2][uv_floor].uv = (1.0, 0.0)
                        f_side.loops[3][uv_floor].uv = (0.0, 0.0)
                        f_side[cell_layer] = get_cell_id(z, ref_cy, ref_cx)



def _build_rect_cube_roof(ctx, props, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, dirty_cells=None):
    """Build cube-mode roof tiles for a rectangular grid, optionally updating an existing BMesh."""
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

        pad = 3 if props.smooth_shape_edges and getattr(props, 'smooth_boundary_method', 'filler') == 'clip' else 0
        off = ctx['ts'] / 2 if ctx['centered'] else 0

        for z in ctx['z_range']:
            z_off = z * ctx['level_height']
            level_cells = ctx['cells_3d'][z]
            for y in range(-pad, maze_data.depth + pad):
                for x in range(-pad, maze_data.width + pad):
                    # Clamp coordinates for dirty cells check and cell_id encoding
                    y_clamp = max(0, min(maze_data.depth - 1, y))
                    x_clamp = max(0, min(maze_data.width - 1, x))

                    if dirty_cells is not None and (z, y_clamp, x_clamp) not in dirty_cells:
                        continue

                    is_in_grid = (0 <= x < maze_data.width and 0 <= y < maze_data.depth)

                    if is_in_grid and (z, y, x) in ctx['stair_bottom_cells']:
                        continue

                    is_blocked = not is_in_grid or (ctx.get('shape_blocked') is not None and ctx['shape_blocked'][y][x])
                    
                    # Determine roof generation and clipping status
                    if is_blocked:
                        continue
                    
                    is_wall = level_cells[y][x][0]
                    if is_wall:
                        if dirty_cells is None:
                            start_idx = len(bm_roof.faces)
                        else:
                            existing_faces = set(bm_roof.faces)

                        roof_idx = level_cells[y][x][6] if len(level_cells[y][x]) > 6 else -1
                        if roof_idx < 0 and ctx['roof_meshes_list']:
                            roof_idx = 0

                        use_clip = False

                        if ctx['roof_meshes_list'] and isinstance(roof_idx, int) and 0 <= roof_idx < len(ctx['roof_meshes_list']):
                            mat_base = Matrix.Translation(Vector((x * ctx['ts'] + off, y * ctx['ts'] + off, z_off + ctx['wh'])))
                            mat = mat_base @ ctx['mat_roof_offset']
                            if use_clip:
                                _add_clipped_custom_mesh_at(
                                    bm_roof, ctx['roof_meshes_list'][roof_idx], mat, uv_roof, roof_materials,
                                    x, y, maze_data.width, maze_data.depth, ctx['ts'], props.maze_shape, props.shape_rotation,
                                    offset=props.smooth_boundary_offset
                                )
                            else:
                                _add_mesh_at(bm_roof, ctx['roof_meshes_list'][roof_idx], mat, uv_roof, final_materials_list=roof_materials)
                        elif ctx['custom_roof']:
                            mat_base = Matrix.Translation(Vector((x * ctx['ts'] + off, y * ctx['ts'] + off, z_off + ctx['wh'])))
                            mat = mat_base @ ctx['mat_roof_offset']
                            if use_clip:
                                _add_clipped_custom_mesh_at(
                                    bm_roof, ctx['custom_roof'], mat, uv_roof, roof_materials,
                                    x, y, maze_data.width, maze_data.depth, ctx['ts'], props.maze_shape, props.shape_rotation,
                                    offset=props.smooth_boundary_offset
                                )
                            else:
                                _add_mesh_at(bm_roof, ctx['custom_roof'], mat, uv_roof, final_materials_list=roof_materials)
                        else:
                            if use_clip:
                                clipped = _add_clipped_floor_tile(
                                    bm_roof, uv_roof, x, y, maze_data.width, maze_data.depth,
                                    ctx['ts'], props.maze_shape, props.shape_rotation,
                                    z_offset=z_off + ctx['wh'], mat_offset=ctx['mat_roof_offset'],
                                    offset=props.smooth_boundary_offset)
                                if not clipped:
                                    _add_cube_roof_face_transformed(bm_roof, uv_roof, x * ctx['ts'] + ctx['ts'] / 2, y * ctx['ts'] + ctx['ts'] / 2, ctx['ts'], ctx['ts'], z_off + ctx['wh'], ctx['mat_roof_offset'])
                            else:
                                _add_cube_roof_face_transformed(bm_roof, uv_roof, x * ctx['ts'] + ctx['ts'] / 2, y * ctx['ts'] + ctx['ts'] / 2, ctx['ts'], ctx['ts'], z_off + ctx['wh'], ctx['mat_roof_offset'])

                        cell_id = get_cell_id(z, y_clamp, x_clamp)
                        if dirty_cells is None:
                            bm_roof.faces.ensure_lookup_table()
                            for i in range(start_idx, len(bm_roof.faces)):
                                bm_roof.faces[i][cell_layer] = cell_id
                        else:
                            for f in bm_roof.faces:
                                if f not in existing_faces:
                                    f[cell_layer] = cell_id
                        continue

                    if is_blocked:
                        if dirty_cells is None:
                            start_idx = len(bm_roof.faces)
                        else:
                            existing_faces = set(bm_roof.faces)

                        has_custom = False
                        if ctx['roof_meshes_list']:
                            roof_idx = 0
                            if isinstance(roof_idx, int) and 0 <= roof_idx < len(ctx['roof_meshes_list']):
                                has_custom = True
                                mat_base = Matrix.Translation(Vector((x * ctx['ts'] + off, y * ctx['ts'] + off, z_off + ctx['wh'])))
                                mat = mat_base @ ctx['mat_roof_offset']
                                _add_clipped_custom_mesh_at(
                                    bm_roof, ctx['roof_meshes_list'][roof_idx], mat, uv_roof, roof_materials,
                                    x, y, maze_data.width, maze_data.depth, ctx['ts'], props.maze_shape, props.shape_rotation,
                                    offset=props.smooth_boundary_offset
                                )
                        elif ctx['custom_roof']:
                            has_custom = True
                            mat_base = Matrix.Translation(Vector((x * ctx['ts'] + off, y * ctx['ts'] + off, z_off + ctx['wh'])))
                            mat = mat_base @ ctx['mat_roof_offset']
                            _add_clipped_custom_mesh_at(
                                bm_roof, ctx['custom_roof'], mat, uv_roof, roof_materials,
                                x, y, maze_data.width, maze_data.depth, ctx['ts'], props.maze_shape, props.shape_rotation,
                                offset=props.smooth_boundary_offset
                            )

                        if not has_custom:
                            _add_clipped_floor_tile(
                                bm_roof, uv_roof, x, y, maze_data.width, maze_data.depth,
                                ctx['ts'], props.maze_shape, props.shape_rotation,
                                z_offset=z_off + ctx['wh'], mat_offset=ctx['mat_roof_offset'],
                                offset=props.smooth_boundary_offset)
                        
                        cell_id = get_cell_id(z, y_clamp, x_clamp)
                        if dirty_cells is None:
                            bm_roof.faces.ensure_lookup_table()
                            for i in range(start_idx, len(bm_roof.faces)):
                                bm_roof.faces[i][cell_layer] = cell_id
                        else:
                            for f in bm_roof.faces:
                                if f not in existing_faces:
                                    f[cell_layer] = cell_id

        if getattr(props, 'smooth_boundary_method', 'filler') != 'clip':
            _build_smooth_roof_triangles(ctx, props, maze_data, bm_roof, uv_roof, cell_layer, dirty_cells)

        if not is_external_bm:
            if not ctx['custom_roof'] and not ctx['roof_meshes_list']:
                bmesh.ops.remove_doubles(bm_roof, verts=bm_roof.verts, dist=0.001)
            roof_obj = _create_object_from_bm(bm_roof, f"FireMaze_Roof{name_suffix}", ctx['col'], None)
            for mat in roof_materials:
                roof_obj.data.materials.append(mat)
            created_objects.append(roof_obj)



def _build_rect_stairs(ctx, props, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, dirty_cells=None):
    """Build stair/ramp geometry for a rectangular grid, shared by cube and thin wall modes."""
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
            z_offset = zstair * ctx['level_height']
            for s in maze_data.stairs:
                if s.get('z') != zstair:
                    continue
                sx, sy = s.get('x', 0), s.get('y', 0)
                if dirty_cells is not None and (zstair, sy, sx) not in dirty_cells:
                    continue

                if ctx.get('shape_blocked') is not None and ctx['shape_blocked'][sy][sx]:
                    continue

                if dirty_cells is None:
                    start_idx = len(bm_stairs.faces)
                else:
                    existing_faces = set(bm_stairs.faces)

                style = s.get('type', 'stair')
                footprint = s.get('footprint', '1x1')
                # Geometry is built only at the base cell (sx,sy). For 1x2
                # footprints, the extra cells are intentionally left empty
                # to provide headroom clearance ΓÇö not for duplicate stairs.
                stair_ts = ctx['ts'] * 2 if footprint == '2x2' else ctx['ts']
                # 2x2 footprints center + scale a single stair to fill the block
                cx = sx * ctx['ts'] + stair_ts / 2
                cy = sy * ctx['ts'] + stair_ts / 2
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
                    off = stair_ts / 2 if ctx['centered'] else 0
                    mat = Matrix.Translation(Vector((sx * ctx['ts'] + off, sy * ctx['ts'] + off, z_offset))) @ rot_mat @ ctx['mat_floor_offset']
                    _add_mesh_at(bm_stairs, ctx['custom_ramp_mesh'].data if ctx['custom_ramp_mesh'].type == 'MESH' else ctx['custom_ramp_mesh'], mat, uv_stairs, final_materials_list=stair_materials)
                elif style == 'stair' and ctx['custom_stair_mesh']:
                    off = stair_ts / 2 if ctx['centered'] else 0
                    mat = Matrix.Translation(Vector((sx * ctx['ts'] + off, sy * ctx['ts'] + off, z_offset))) @ rot_mat @ ctx['mat_floor_offset']
                    _add_mesh_at(bm_stairs, ctx['custom_stair_mesh'].data if ctx['custom_stair_mesh'].type == 'MESH' else ctx['custom_stair_mesh'], mat, uv_stairs, final_materials_list=stair_materials)
                elif style == 'ramp':
                    _build_ramp_1x1(bm_stairs, uv_stairs, cx, cy, stair_ts, ctx['level_height'], z_offset, rot_mat @ ctx['mat_floor_offset'])
                else:
                    _build_spiral_stair_1x1(bm_stairs, uv_stairs, cx, cy, stair_ts, ctx['level_height'], z_offset, rot_mat @ ctx['mat_floor_offset'])

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



def _build_rect_thin_floor(ctx, props, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, dirty_cells=None):
    """Build thin-mode floor tiles for a rectangular grid, optionally updating an existing BMesh."""
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
    pad = 3 if props.smooth_shape_edges and getattr(props, 'smooth_boundary_method', 'filler') == 'clip' else 0
    from ..shape_boundaries import get_cell_clip_status

    for z in ctx['z_range']:
        stair_top_cells_z = {(yy, xx) for (zz, yy, xx) in ctx['stair_top_cells'] if zz == z}
        level_cells = ctx['cells_3d'][z]
        for y in range(-pad, maze_data.depth + pad):
            for x in range(-pad, maze_data.width + pad):
                # Clamp coordinates for dirty cells check and cell_id encoding
                y_clamp = max(0, min(maze_data.depth - 1, y))
                x_clamp = max(0, min(maze_data.width - 1, x))

                if dirty_cells is not None and (z, y_clamp, x_clamp) not in dirty_cells:
                    continue

                is_in_grid = (0 <= x < maze_data.width and 0 <= y < maze_data.depth)

                if is_in_grid and (y, x) in stair_top_cells_z:
                    continue

                is_blocked = not is_in_grid or (ctx.get('shape_blocked') is not None and ctx['shape_blocked'][y][x])
                
                # Determine floor generation and clipping status
                if is_blocked:
                    if not (props.smooth_shape_edges and getattr(props, 'smooth_boundary_method', 'filler') == 'clip'):
                        continue
                    # Check clipping status for this blocked cell
                    status = get_cell_clip_status(x, y, maze_data.width, maze_data.depth, props.maze_shape, props.shape_rotation, props.smooth_boundary_offset)
                    if status == 'none':
                        continue
                    use_clip = (status == 'clip')
                else:
                    # Active floor cell: check if we should clip it (only when smooth edges & clip are active)
                    if props.smooth_shape_edges and getattr(props, 'smooth_boundary_method', 'filler') == 'clip':
                        status = get_cell_clip_status(x, y, maze_data.width, maze_data.depth, props.maze_shape, props.shape_rotation, props.smooth_boundary_offset)
                        use_clip = (status == 'clip')
                        if status == 'none':
                            continue
                    else:
                        use_clip = False

                if dirty_cells is None:
                    start_idx = len(bm_floor.faces)
                else:
                    existing_faces = set(bm_floor.faces)

                floor_idx = -1
                if not is_blocked:
                    floor_idx = level_cells[y][x][8] if len(level_cells[y][x]) > 8 else -1
                if floor_idx < 0 and ctx['floor_meshes_list']:
                    floor_idx = 0

                has_custom = False
                if ctx['floor_meshes_list'] and isinstance(floor_idx, int) and 0 <= floor_idx < len(ctx['floor_meshes_list']):
                    has_custom = True
                    mat_base = Matrix.Translation(Vector((x * ctx['ts'] + off, y * ctx['ts'] + off, z * ctx['level_height'] - ctx['ft'] / 2)))
                    mat = mat_base @ ctx['mat_floor_offset']
                    if use_clip:
                        _add_clipped_custom_mesh_at(
                            bm_floor, ctx['floor_meshes_list'][floor_idx], mat, uv_floor, floor_materials,
                            x, y, maze_data.width, maze_data.depth, ctx['ts'], props.maze_shape, props.shape_rotation,
                            offset=props.smooth_boundary_offset
                        )
                    else:
                        _add_mesh_at(bm_floor, ctx['floor_meshes_list'][floor_idx], mat, uv_floor, final_materials_list=floor_materials)
                elif ctx['custom_floor']:
                    has_custom = True
                    mat_base = Matrix.Translation(Vector((x * ctx['ts'] + off, y * ctx['ts'] + off, z * ctx['level_height'] - ctx['ft'] / 2)))
                    mat = mat_base @ ctx['mat_floor_offset']
                    if use_clip:
                        _add_clipped_custom_mesh_at(
                            bm_floor, ctx['custom_floor'], mat, uv_floor, floor_materials,
                            x, y, maze_data.width, maze_data.depth, ctx['ts'], props.maze_shape, props.shape_rotation,
                            offset=props.smooth_boundary_offset
                        )
                    else:
                        _add_mesh_at(bm_floor, ctx['custom_floor'], mat, uv_floor, final_materials_list=floor_materials)

                if not has_custom:
                    if use_clip:
                        clipped = _add_clipped_floor_tile(
                            bm_floor, uv_floor, x, y, maze_data.width, maze_data.depth,
                            ctx['ts'], props.maze_shape, props.shape_rotation,
                            z_offset=z * ctx['level_height'], mat_offset=ctx['mat_floor_offset'],
                            thickness=ctx['ft'], offset=props.smooth_boundary_offset)
                        if not clipped:
                            _add_floor_tile_transformed(bm_floor, uv_floor, x, y, ctx['ts'], ctx['mat_floor_offset'], z_offset=z * ctx['level_height'], thickness=ctx['ft'])
                    else:
                        _add_floor_tile_transformed(bm_floor, uv_floor, x, y, ctx['ts'], ctx['mat_floor_offset'], z_offset=z * ctx['level_height'], thickness=ctx['ft'])

                cell_id = get_cell_id(z, y_clamp, x_clamp)
                if dirty_cells is None:
                    bm_floor.faces.ensure_lookup_table()
                    for i in range(start_idx, len(bm_floor.faces)):
                        bm_floor.faces[i][cell_layer] = cell_id
                else:
                    for f in bm_floor.faces:
                        if f not in existing_faces:
                            f[cell_layer] = cell_id

    if getattr(props, 'smooth_boundary_method', 'filler') != 'clip':
        _build_smooth_floor_triangles(ctx, props, maze_data, bm_floor, uv_floor, cell_layer, dirty_cells)

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
    """Build thin-mode wall and end-cap geometry for a rectangular grid, optionally updating an existing BMesh."""
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
    smooth_edges = props.smooth_shape_edges and (ctx.get('shape_blocked') is not None)
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

        if ctx.get('shape_blocked') is not None:
            from .bmesh_utils import _filter_wall_segments
            segments = list(_filter_wall_segments(segments, maze_data.width, maze_data.depth, ctx['shape_blocked']))
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

        open_segments = set()
        if z == 0 and maze_data.entrance:
            ex, ey, ed = maze_data.entrance
            if ed == 'N':
                open_segments.add(('H', ex, ey + 1))
            elif ed == 'S':
                open_segments.add(('H', ex, ey))
            elif ed == 'E':
                open_segments.add(('V', ex + 1, ey))
            elif ed == 'W':
                open_segments.add(('V', ex, ey))

        top_z = len(ctx['cells_3d']) - 1
        if z == top_z and maze_data.exits:
            for ex, ey, ed in maze_data.exits:
                if ed == 'N':
                    open_segments.add(('H', ex, ey + 1))
                elif ed == 'S':
                    open_segments.add(('H', ex, ey))
                elif ed == 'E':
                    open_segments.add(('V', ex + 1, ey))
                elif ed == 'W':
                    open_segments.add(('V', ex, ey))

        for level in range(ctx['tiles_high']):
            z_off = z * ctx['level_height'] + level * ctx['seg_h']

            for seg_type, a, b in segments:
                if (seg_type, a, b) in open_segments:
                    continue

                if seg_type == 'H':
                    owner_cell = (z, b, a) if b < maze_data.depth else (z, b - 1, a)
                else:
                    owner_cell = (z, b, a) if a < maze_data.width else (z, b, a - 1)

                if dirty_cells is not None and owner_cell not in dirty_cells:
                    continue



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

                        # Boundary Bottom Cap
                        is_below_active = (0 <= a < maze_data.width and 0 <= b - 1 < maze_data.depth and (ctx.get('shape_blocked') is None or not ctx['shape_blocked'][b - 1][a]))
                        is_above_active = (0 <= a < maze_data.width and 0 <= b < maze_data.depth and (ctx.get('shape_blocked') is None or not ctx['shape_blocked'][b][a]))
                        if is_below_active != is_above_active:
                            T = Matrix.Translation(Vector((x0 + ctx['ts']/2, yc, hw))) @ ctx['mat_wall_offset']
                            dx_l = dx_left if not has_any_wall_custom else 0.0
                            dx_r = dx_right if not has_any_wall_custom else 0.0
                            v_pts = [T @ Vector(p) for p in [
                                (-ctx['ts']/2 - dx_l, -tw, -sh/2),
                                (-ctx['ts']/2 - dx_l, tw, -sh/2),
                                (ctx['ts']/2 + dx_r, tw, -sh/2),
                                (ctx['ts']/2 + dx_r, -tw, -sh/2)
                            ]]
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

                        # Boundary Bottom Cap
                        is_left_active = (0 <= a - 1 < maze_data.width and 0 <= b < maze_data.depth and (ctx.get('shape_blocked') is None or not ctx['shape_blocked'][b][a - 1]))
                        is_right_active = (0 <= a < maze_data.width and 0 <= b < maze_data.depth and (ctx.get('shape_blocked') is None or not ctx['shape_blocked'][b][a]))
                        if is_left_active != is_right_active:
                            T = Matrix.Translation(Vector((xc, y0 + ctx['ts']/2, hw))) @ ctx['mat_wall_offset']
                            dy_s = dy_south if not has_any_wall_custom else 0.0
                            dy_n = dy_north if not has_any_wall_custom else 0.0
                            v_pts = [T @ Vector(p) for p in [
                                (-tw, -ctx['ts']/2 - dy_s, -sh/2),
                                (-tw, ctx['ts']/2 + dy_n, -sh/2),
                                (tw, ctx['ts']/2 + dy_n, -sh/2),
                                (tw, -ctx['ts']/2 - dy_s, -sh/2)
                            ]]
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

        if smooth_edges:
            from ..shape_boundaries import _ROTATION_ANGLES, _rotate_point
            w = maze_data.width
            d = maze_data.depth
            ts = ctx['ts']
            sb = ctx['shape_blocked']
            angle_rad = _ROTATION_ANGLES.get(props.shape_rotation, 0.0)

            is_clip_mode = (getattr(props, 'smooth_boundary_method', 'filler') == 'clip')
            corner_items = []
            if is_clip_mode:
                from ..shape_boundaries import _SHAPE_TESTS_STRICT
                test_fn = _SHAPE_TESTS_STRICT.get(props.maze_shape)
                if test_fn is not None:
                    # Clip mode: collect perfect boundary edges segment-by-segment
                    pad = 3
                    perfect_contour = []
                    from ..shape_boundaries import get_boundary_edges
                    for y in range(-pad, d + pad):
                        for x in range(-pad, w + pad):
                            edges = get_boundary_edges(x, y, w, d, ts, props.maze_shape, props.shape_rotation, offset=props.smooth_boundary_offset)
                            for v0, v1 in edges:
                                perfect_contour.append((v0[0], v0[1], v1[0], v1[1], x, y))

                    if perfect_contour:
                        cx_center = w * ts / 2
                        cy_center = d * ts / 2
                        perfect_contour.sort(key=lambda seg: math.atan2((seg[1] + seg[3])/2 - cy_center, (seg[0] + seg[2])/2 - cx_center))

                        for seg in perfect_contour:
                            corner_items.append((seg[0], seg[1], frozenset([(seg[4], seg[5])])))
            else:
                # Filler mode: collect corners of active grid boundary cells facing outside using original logic
                from ..shape_boundaries import _SHAPE_TESTS
                test_fn_filler = _SHAPE_TESTS.get(props.maze_shape)
                if test_fn_filler is not None:
                    def _pt_outside_filler(u, v):
                        pu, pv = u, v
                        if angle_rad != 0.0:
                            pu, pv = _rotate_point(pu, pv, -angle_rad)
                        return not test_fn_filler(pu, pv)

                    corner_dict = {}
                    for y in range(d):
                        for x in range(w):
                            if sb[y][x]:
                                continue
                            boundary = False
                            for ny, nx in ((y-1,x),(y+1,x),(y,x-1),(y,x+1)):
                                if nx < 0 or nx >= w or ny < 0 or ny >= d or sb[ny][nx]:
                                    boundary = True
                                    break
                            if not boundary:
                                continue
                            for cu, cv in [(x, y), (x+1, y), (x+1, y+1), (x, y+1)]:
                                u, v = cu / w, cv / d
                                if _pt_outside_filler(u, v):
                                    key = (cu * ts, cv * ts)
                                    corner_dict.setdefault(key, set()).add((x, y))

                    if corner_dict:
                        corner_items = [(wx, wy, frozenset(cells)) for (wx, wy), cells in corner_dict.items()]
                        cx_center = w * ts / 2
                        cy_center = d * ts / 2
                        corner_items.sort(key=lambda c: math.atan2(c[1] - cy_center, c[0] - cx_center))

            n = len(corner_items)

            # Pre-calculate normals and miter vectors for corner vertices
            normals = []
            for i in range(n):
                c_curr = corner_items[i]
                c_next = corner_items[(i + 1) % n]
                v_dir = Vector((c_next[0] - c_curr[0], c_next[1] - c_curr[1], 0.0))
                if v_dir.length > 0:
                    norm = Vector((v_dir.y, -v_dir.x, 0.0)).normalized()
                else:
                    norm = Vector((0, 0, 0))
                normals.append(norm)

            def get_miter_vector(m_prev, m_curr, W):
                """Return the miter offset vector for a corner, given previous and next edge normals and wall thickness W."""
                denom = 1.0 + m_prev.dot(m_curr)
                if denom < 0.05:
                    avg = (m_prev + m_curr).normalized() if (m_prev + m_curr).length > 0 else m_curr
                    return avg * (W * 4.0)
                miter = W * (m_prev + m_curr) / denom
                max_len = 4.0 * W
                if miter.length > max_len:
                    miter = miter.normalized() * max_len
                return miter

            miter_double = []
            miter_single = []
            for i in range(n):
                m_prev = normals[i - 1]
                m_curr = normals[i]
                miter_double.append(get_miter_vector(m_prev, m_curr, ctx['wt']))
                miter_single.append(get_miter_vector(m_prev, m_curr, ctx['wt'] / 2.0))

            segment_skipped = [False] * n
            for idx in range(n):
                c1 = corner_items[idx]
                c2 = corner_items[(idx + 1) % n]
                x0, y0 = c1[0], c1[1]
                x1, y1 = c2[0], c2[1]
                
                associated_cells = set()
                for cell_set in (c1[2], c2[2]):
                    associated_cells.update(cell_set)

                min_cx = max(0, int(min(x0, x1) / ts - 1))
                max_cx = min(w - 1, int(max(x0, x1) / ts + 1))
                min_cy = max(0, int(min(y0, y1) / ts - 1))
                max_cy = min(d - 1, int(max(y0, y1) / ts + 1))

                for cy in range(min_cy, max_cy + 1):
                    for cx in range(min_cx, max_cx + 1):
                        if sb[cy][cx]:
                            continue
                        ccx = cx * ts + ts / 2
                        ccy = cy * ts + ts / 2
                        dx = x1 - x0
                        dy = y1 - y0
                        if dx == 0 and dy == 0:
                            dist = math.hypot(ccx - x0, ccy - y0)
                        else:
                            t = ((ccx - x0) * dx + (ccy - y0) * dy) / (dx * dx + dy * dy)
                            t = max(0.0, min(1.0, t))
                            qx = x0 + t * dx
                            qy = y0 + t * dy
                            dist = math.hypot(ccx - qx, ccy - qy)

                        if dist < 1.25 * ts:
                            associated_cells.add((cx, cy))

                entrance_exit_cells = set()
                if z == 0 and maze_data.entrance:
                    ex, ey = maze_data.entrance[0], maze_data.entrance[1]
                    entrance_exit_cells.add((ey, ex))
                if maze_data.exits:
                    top_z = max(ctx['z_range'])
                    if z == top_z:
                        for e in maze_data.exits:
                            entrance_exit_cells.add((e[1], e[0]))

                for (cx, cy) in associated_cells:
                    if (cy, cx) in entrance_exit_cells:
                        segment_skipped[idx] = True
                        break

            for idx in range(n):
                c1 = corner_items[idx]
                c2 = corner_items[(idx + 1) % n]

                if segment_skipped[idx]:
                    continue

                x0, y0 = c1[0], c1[1]
                x1, y1 = c2[0], c2[1]

                # Identify associated cells
                associated_cells = set()
                for cell_set in (c1[2], c2[2]):
                    associated_cells.update(cell_set)

                min_cx = max(0, int(min(x0, x1) / ts - 1))
                max_cx = min(w - 1, int(max(x0, x1) / ts + 1))
                min_cy = max(0, int(min(y0, y1) / ts - 1))
                max_cy = min(d - 1, int(max(y0, y1) / ts + 1))

                for cy in range(min_cy, max_cy + 1):
                    for cx in range(min_cx, max_cx + 1):
                        if sb[cy][cx]:
                            continue
                        ccx = cx * ts + ts / 2
                        ccy = cy * ts + ts / 2
                        dx = x1 - x0
                        dy = y1 - y0
                        if dx == 0 and dy == 0:
                            dist = math.hypot(ccx - x0, ccy - y0)
                        else:
                            t = ((ccx - x0) * dx + (ccy - y0) * dy) / (dx * dx + dy * dy)
                            t = max(0.0, min(1.0, t))
                            qx = x0 + t * dx
                            qy = y0 + t * dy
                            dist = math.hypot(ccx - qx, ccy - qy)

                        if dist < 1.25 * ts:
                            associated_cells.add((cx, cy))

                has_dirty = dirty_cells is None
                if not has_dirty:
                    for (cx, cy) in associated_cells:
                        cx_clamp = max(0, min(w - 1, cx))
                        cy_clamp = max(0, min(d - 1, cy))
                        if (z, cy_clamp, cx_clamp) in dirty_cells:
                            has_dirty = True
                            break
                if not has_dirty:
                    continue

                ref_cx, ref_cy = next(iter(c1[2]))
                z_off_floor = z * ctx['level_height']
                cx_w = ref_cx * ts + ts / 2
                cy_w = ref_cy * ts + ts / 2

                for level in range(ctx['tiles_high']):
                    z_off = z_off_floor + level * ctx['seg_h']
                    hw = z_off + ctx['seg_h'] / 2
                    start_idx = len(bm_wall.faces)
                    start_cap_idx = len(bm_cap.faces)

                    md0 = miter_double[idx]
                    md1 = miter_double[(idx + 1) % n]
                    ms0 = miter_single[idx]
                    ms1 = miter_single[(idx + 1) % n]

                    if props.thin_wall_double_sided:
                        # First Face (Inner Face) - CCW when looking from inside
                        # This face lies exactly on the original boundary (no offset)
                        v0_l = Vector((x0 - cx_w, y0 - cy_w, 0.0))
                        v1_l = Vector((x1 - cx_w, y1 - cy_w, 0.0))
                        pts = [
                            Vector((v1_l.x, v1_l.y, -ctx['seg_h']/2)),
                            Vector((v0_l.x, v0_l.y, -ctx['seg_h']/2)),
                            Vector((v0_l.x, v0_l.y, ctx['seg_h']/2)),
                            Vector((v1_l.x, v1_l.y, ctx['seg_h']/2))
                        ]
                        final_pts = []
                        for p in pts:
                            p_trans = Matrix.Translation(Vector((cx_w, cy_w, hw))) @ ctx['mat_wall_offset'] @ p
                            final_pts.append(p_trans)

                        f_verts = [bm_wall.verts.new(pt) for pt in final_pts]
                        face = bm_wall.faces.new(f_verts)
                        for loop, uv in zip(face.loops, [(0,0),(1,0),(1,1),(0,1)]):
                            loop[uv_wall].uv = uv

                        # Second Face (Outer Face) - CCW when looking from outside
                        # This face is offset outwards by a full wall thickness 'wt' using miter vectors
                        v0_l2 = Vector((x0 + md0.x - cx_w, y0 + md0.y - cy_w, 0.0))
                        v1_l2 = Vector((x1 + md1.x - cx_w, y1 + md1.y - cy_w, 0.0))
                        pts2 = [
                            Vector((v0_l2.x, v0_l2.y, -ctx['seg_h']/2)),
                            Vector((v1_l2.x, v1_l2.y, -ctx['seg_h']/2)),
                            Vector((v1_l2.x, v1_l2.y, ctx['seg_h']/2)),
                            Vector((v0_l2.x, v0_l2.y, ctx['seg_h']/2))
                        ]
                        final_pts2 = []
                        for p in pts2:
                            p_trans = Matrix.Translation(Vector((cx_w, cy_w, hw))) @ ctx['mat_wall_offset'] @ p
                            final_pts2.append(p_trans)

                        f_verts2 = [bm_wall.verts.new(pt) for pt in final_pts2]
                        face2 = bm_wall.faces.new(f_verts2)
                        for loop, uv in zip(face2.loops, [(0,0),(1,0),(1,1),(0,1)]):
                            loop[uv_wall].uv = uv

                        # --- Top Cap (Roof Segment of the Wall) ---
                        v0_inner_top = Vector((x0 - cx_w, y0 - cy_w, ctx['seg_h']/2))
                        v1_inner_top = Vector((x1 - cx_w, y1 - cy_w, ctx['seg_h']/2))
                        v1_outer_top = Vector((x1 + md1.x - cx_w, y1 + md1.y - cy_w, ctx['seg_h']/2))
                        v0_outer_top = Vector((x0 + md0.x - cx_w, y0 + md0.y - cy_w, ctx['seg_h']/2))
                        pts_top = [v0_inner_top, v0_outer_top, v1_outer_top, v1_inner_top]
                        final_pts_top = []
                        for p in pts_top:
                            p_trans = Matrix.Translation(Vector((cx_w, cy_w, hw))) @ ctx['mat_wall_offset'] @ p
                            final_pts_top.append(p_trans)
                        f_top = bm_cap.faces.new([bm_cap.verts.new(pt) for pt in final_pts_top])
                        for loop, uv in zip(f_top.loops, [(0,0),(1,0),(1,1),(0,1)]):
                            loop[uv_cap].uv = uv

                        # --- Bottom Cap (Floor Segment of the Wall) ---
                        v0_inner_bot = Vector((x0 - cx_w, y0 - cy_w, -ctx['seg_h']/2))
                        v1_inner_bot = Vector((x1 - cx_w, y1 - cy_w, -ctx['seg_h']/2))
                        v1_outer_bot = Vector((x1 + md1.x - cx_w, y1 + md1.y - cy_w, -ctx['seg_h']/2))
                        v0_outer_bot = Vector((x0 + md0.x - cx_w, y0 + md0.y - cy_w, -ctx['seg_h']/2))
                        pts_bot = [v0_inner_bot, v1_inner_bot, v1_outer_bot, v0_outer_bot]
                        final_pts_bot = []
                        for p in pts_bot:
                            p_trans = Matrix.Translation(Vector((cx_w, cy_w, hw))) @ ctx['mat_wall_offset'] @ p
                            final_pts_bot.append(p_trans)
                        f_bot = bm_cap.faces.new([bm_cap.verts.new(pt) for pt in final_pts_bot])
                        for loop, uv in zip(f_bot.loops, [(0,0),(1,0),(1,1),(0,1)]):
                            loop[uv_cap].uv = uv

                        # --- Vertical End Caps ---
                        prev_skipped = segment_skipped[(idx - 1) % n]
                        next_skipped = segment_skipped[(idx + 1) % n]

                        # Start End Cap (at corner c1)
                        if prev_skipped:
                            v_bottom_inner = Vector((x0 - cx_w, y0 - cy_w, -ctx['seg_h']/2))
                            v_bottom_outer = Vector((x0 + md0.x - cx_w, y0 + md0.y - cy_w, -ctx['seg_h']/2))
                            v_top_outer = Vector((x0 + md0.x - cx_w, y0 + md0.y - cy_w, ctx['seg_h']/2))
                            v_top_inner = Vector((x0 - cx_w, y0 - cy_w, ctx['seg_h']/2))
                            pts_start_cap = [v_bottom_inner, v_bottom_outer, v_top_outer, v_top_inner]
                            final_pts_start = []
                            for p in pts_start_cap:
                                p_trans = Matrix.Translation(Vector((cx_w, cy_w, hw))) @ ctx['mat_wall_offset'] @ p
                                final_pts_start.append(p_trans)
                            f_start = bm_cap.faces.new([bm_cap.verts.new(pt) for pt in final_pts_start])
                            for loop, uv in zip(f_start.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                loop[uv_cap].uv = uv

                        # End End Cap (at corner c2)
                        if next_skipped:
                            v_bottom_inner = Vector((x1 - cx_w, y1 - cy_w, -ctx['seg_h']/2))
                            v_bottom_outer = Vector((x1 + md1.x - cx_w, y1 + md1.y - cy_w, -ctx['seg_h']/2))
                            v_top_outer = Vector((x1 + md1.x - cx_w, y1 + md1.y - cy_w, ctx['seg_h']/2))
                            v_top_inner = Vector((x1 - cx_w, y1 - cy_w, ctx['seg_h']/2))
                            pts_end_cap = [v_bottom_outer, v_bottom_inner, v_top_inner, v_top_outer]
                            final_pts_end = []
                            for p in pts_end_cap:
                                p_trans = Matrix.Translation(Vector((cx_w, cy_w, hw))) @ ctx['mat_wall_offset'] @ p
                                final_pts_end.append(p_trans)
                            f_end = bm_cap.faces.new([bm_cap.verts.new(pt) for pt in final_pts_end])
                            for loop, uv in zip(f_end.loops, [(0,0),(1,0),(1,1),(0,1)]):
                                loop[uv_cap].uv = uv
                    else:
                        # Single centered face - CCW when looking from inside
                        # Offset outwards by half wall thickness 'wt/2' using miter vectors
                        v0_l = Vector((x0 + ms0.x - cx_w, y0 + ms0.y - cy_w, 0.0))
                        v1_l = Vector((x1 + ms1.x - cx_w, y1 + ms1.y - cy_w, 0.0))
                        pts = [
                            Vector((v1_l.x, v1_l.y, -ctx['seg_h']/2)),
                            Vector((v0_l.x, v0_l.y, -ctx['seg_h']/2)),
                            Vector((v0_l.x, v0_l.y, ctx['seg_h']/2)),
                            Vector((v1_l.x, v1_l.y, ctx['seg_h']/2))
                        ]
                        final_pts = []
                        for p in pts:
                            p_trans = Matrix.Translation(Vector((cx_w, cy_w, hw))) @ ctx['mat_wall_offset'] @ p
                            final_pts.append(p_trans)

                        f_verts = [bm_wall.verts.new(pt) for pt in final_pts]
                        face = bm_wall.faces.new(f_verts)
                        for loop, uv in zip(face.loops, [(1,0),(0,0),(0,1),(1,1)]):
                            loop[uv_wall].uv = uv

                    ref_cx_clamp = max(0, min(w - 1, ref_cx))
                    ref_cy_clamp = max(0, min(d - 1, ref_cy))
                    cell_id = get_cell_id(z, ref_cy_clamp, ref_cx_clamp)
                    bm_wall.faces.ensure_lookup_table()
                    for i_face in range(start_idx, len(bm_wall.faces)):
                        bm_wall.faces[i_face][cell_layer] = cell_id
                    
                    if bm_cap.faces:
                        bm_cap.faces.ensure_lookup_table()
                        for i_face in range(start_cap_idx, len(bm_cap.faces)):
                            bm_cap.faces[i_face][cell_layer_cap] = cell_id

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

        bm_wall.faces.ensure_lookup_table()
        _safe_remove_doubles(bm_wall, dist=0.001)

    if not is_external_bm:
        if props.single_wall_object:
            if not is_external_cap:
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
                if not is_external_cap:
                    cap_obj = _create_object_from_bm(bm_cap, f"FireMaze_WallEndCaps{name_suffix}", ctx['col'], None)
                    for mat in cap_materials:
                        cap_obj.data.materials.append(mat)
                    created_objects.append(cap_obj)
            elif not is_external_cap:
                bm_cap.free()
    else:
        if props.single_wall_object:
            if not is_external_cap:
                bm_cap.free()
        elif not props.single_wall_object and bm_cap.verts:
            if not is_external_cap:
                cap_obj = _create_object_from_bm(bm_cap, f"FireMaze_WallEndCaps{name_suffix}", ctx['col'], None)
                for mat in cap_materials:
                    cap_obj.data.materials.append(mat)
                created_objects.append(cap_obj)
        elif not bm_cap.verts:
            if not is_external_cap:
                bm_cap.free()



def _build_rect_thin_roof(ctx, props, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, dirty_cells=None):
    """Build thin-mode roof tiles for a rectangular grid, optionally updating an existing BMesh."""
    if name_suffix != "_EditHelper":
        if not props.thin_wall_double_sided and (ctx.get('custom_wall') or ctx.get('wall_meshes_list')):
            return
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
            
            if ctx.get('shape_blocked') is not None:
                from .bmesh_utils import _filter_wall_segments
                segments = list(_filter_wall_segments(segments, maze_data.width, maze_data.depth, ctx['shape_blocked']))

            open_segments = set()
            if z == 0 and maze_data.entrance:
                ex, ey, ed = maze_data.entrance
                if ed == 'N':
                    open_segments.add(('H', ex, ey + 1))
                elif ed == 'S':
                    open_segments.add(('H', ex, ey))
                elif ed == 'E':
                    open_segments.add(('V', ex + 1, ey))
                elif ed == 'W':
                    open_segments.add(('V', ex, ey))

            top_z = len(ctx['cells_3d']) - 1
            if z == top_z and maze_data.exits:
                for ex, ey, ed in maze_data.exits:
                    if ed == 'N':
                        open_segments.add(('H', ex, ey + 1))
                    elif ed == 'S':
                        open_segments.add(('H', ex, ey))
                    elif ed == 'E':
                        open_segments.add(('V', ex + 1, ey))
                    elif ed == 'W':
                        open_segments.add(('V', ex, ey))

            h_positions = set()
            v_positions = set()
            h_endpoints = set()
            for seg_type, a, b in segments:
                if (seg_type, a, b) in open_segments:
                    continue
                if seg_type == 'H':
                    h_positions.add((a, b))
                    h_endpoints.add((a, b))
                    h_endpoints.add((a + 1, b))
                else:
                    v_positions.add((a, b))
            sz = z * ctx['level_height'] + ctx['wh']
            clean_corners = ctx['clean_wall_corners']
            if ctx['custom_roof'] or ctx['roof_meshes_list']:
                filled = set()
                for seg_type, a, b in segments:
                    if (seg_type, a, b) in open_segments:
                        continue
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
                    
                    if roof_idx < 0 and ctx['roof_meshes_list']:
                        roof_idx = 0

                    if ctx['roof_meshes_list'] and isinstance(roof_idx, int) and 0 <= roof_idx < len(ctx['roof_meshes_list']):
                        mat = mat_base @ ctx['mat_roof_offset']
                        _add_mesh_at(bm_roof, ctx['roof_meshes_list'][roof_idx], mat, uv_roof, final_materials_list=roof_materials)
                    elif ctx['custom_roof']:
                        mat = mat_base @ ctx['mat_roof_offset']
                        _add_mesh_at(bm_roof, ctx['custom_roof'], mat, uv_roof, final_materials_list=roof_materials)
                    else:
                        # Fallback to procedural thin-roof path
                        if seg_type == 'H':
                            _add_horizontal_roof_face_transformed(bm_roof, uv_roof, a, b, ctx['ts'], sz, ctx['wt'], ctx['mat_roof_offset'], extend_left=(dx_left > 0.0), extend_right=(dx_right > 0.0))
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
                            _add_vertical_roof_face_transformed(bm_roof, uv_roof, a, b, ctx['ts'], sz, ctx['wt'], ctx['mat_roof_offset'], trim_south=(dy_south > 0.0), trim_north=(dy_north > 0.0))
                            tsouth = ((a, b) in h_positions) or ((a - 1, b) in h_positions)
                            tnorth = ((a, b + 1) in h_positions) or ((a - 1, b + 1) in h_positions)
                            yc = b * ctx['ts']
                            if tsouth:
                                xc = a * ctx['ts']
                                x_lo = xc if (a - 1, b) not in h_positions else xc - tw
                                x_hi = xc if (a, b) not in h_positions else xc + tw
                                for gx, gy, side in [(a - 1, b - 1, 'u'), (a, b - 1, 'u')]:
                                    key = (gx, gy, side)
                                    if (gx, gy) not in v_positions and key not in filled:
                                        filled.add(key)
                                        _add_horizontal_roof_filler_transformed(bm_roof, uv_roof, xc, yc, sz, tw, x_lo - xc, x_hi - xc, -tw, 0.0, ctx['mat_roof_offset'])
                            yc = (b + 1) * ctx['ts']
                            if tnorth:
                                xc = a * ctx['ts']
                                x_lo = xc if (a - 1, b + 1) not in h_positions else xc - tw
                                x_hi = xc if (a, b + 1) not in h_positions else xc + tw
                                for gx, gy, side in [(a - 1, b + 1, 'd'), (a, b + 1, 'd')]:
                                    key = (gx, gy, side)
                                    if (gx, gy) not in v_positions and key not in filled:
                                        filled.add(key)
                                        _add_horizontal_roof_filler_transformed(bm_roof, uv_roof, xc, yc, sz, tw, x_lo - xc, x_hi - xc, 0.0, tw, ctx['mat_roof_offset'])

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
                    if (seg_type, a, b) in open_segments:
                        continue
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
            if not ctx['custom_roof'] and not ctx.get('roof_meshes_list'):
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
        _build_rect_cube_floor(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
        _build_rect_cube_walls(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
        _build_rect_cube_roof(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
        _build_rect_stairs(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
    else:
        _build_rect_thin_floor(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
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
            try:
                _remove_doubles_on_obj(obj)
            except Exception as e:
                print(f"FireMaze Warning: Post-build remove_doubles failed on {obj.name}: {e}")

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
