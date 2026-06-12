"""Polar grid mesh builders for FireMaze."""

import math
import bpy
import bmesh
from mathutils import Matrix, Vector
from ..utils import get_rng, get_cell_id
from ..maze_data import MazeData
from .bmesh_utils import (
    _create_bmesh_element,
    _add_mesh_at,
    _create_object_from_bm,
    _safe_remove_doubles,
    _prepare_maze_building_context,
    _build_guide_path,
    _remove_doubles_on_obj,
    _get_bmesh_cache,
    _merge_bmesh_geometries,
)
from .stair_builder import _build_ramp_1x1, _build_spiral_stair_1x1
from .post_processor import (
    _optimize_coplanar_on_obj,
    _merge_maze_objects,
    _apply_vertex_painting_on_obj,
    _generate_lightmap_on_obj,
    _spawn_decorations,
)

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

def _add_circular_wall(bm, uv_layer, radius, phi_start, phi_end, ts, h, wt, z_base, flip=False, add_start=True, add_end=True):
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
            
    # 3. Bottom cap panels
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
            
    # 4. Top cap panels
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
    if add_start:
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
    if add_end:
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

def _add_radial_wall(bm, uv_layer, phi, r_in, r_out, ts, h, wt, z_base, add_inner=True, add_outer=True):
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
    
    # Left face
    f_left = bm.faces.new([v_l_in_top, v_l_out_top, v_l_out_bot, v_l_in_bot])
    uvs_left = [(0.0, h / ts), (1.0, h / ts), (1.0, 0.0), (0.0, 0.0)]
    for loop, uv in zip(f_left.loops, uvs_left):
        loop[uv_layer].uv = uv
        
    # Right face
    f_right = bm.faces.new([v_r_out_top, v_r_in_top, v_r_in_bot, v_r_out_bot])
    uvs_right = [(0.0, h / ts), (1.0, h / ts), (1.0, 0.0), (0.0, 0.0)]
    for loop, uv in zip(f_right.loops, uvs_right):
        loop[uv_layer].uv = uv
        
    # Inner face
    if add_inner:
        f_inner = bm.faces.new([v_r_in_top, v_l_in_top, v_l_in_bot, v_r_in_bot])
        uvs_inner = [(0.0, h / ts), (wt / ts, h / ts), (wt / ts, 0.0), (0.0, 0.0)]
        for loop, uv in zip(f_inner.loops, uvs_inner):
            loop[uv_layer].uv = uv
        
    # Outer face
    if add_outer:
        f_outer = bm.faces.new([v_l_out_top, v_r_out_top, v_r_out_bot, v_l_out_bot])
        uvs_outer = [(0.0, h / ts), (wt / ts, h / ts), (wt / ts, 0.0), (0.0, 0.0)]
        for loop, uv in zip(f_outer.loops, uvs_outer):
            loop[uv_layer].uv = uv
        
    # Bottom face
    f_bot = bm.faces.new([v_l_out_bot, v_r_out_bot, v_r_in_bot, v_l_in_bot])
    uvs_bot = [(0.0, 1.0), (wt / ts, 1.0), (wt / ts, 0.0), (0.0, 0.0)]
    for loop, uv in zip(f_bot.loops, uvs_bot):
        loop[uv_layer].uv = uv
        
    # Top face
    f_top = bm.faces.new([v_l_in_top, v_r_in_top, v_r_out_top, v_l_out_top])
    uvs_top = [(0.0, 0.0), (wt / ts, 0.0), (wt / ts, 1.0), (0.0, 1.0)]
    for loop, uv in zip(f_top.loops, uvs_top):
        loop[uv_layer].uv = uv

def _add_radial_wall_caps(bm_cap, uv_cap, phi, r_in, r_out, wt, h, z_base, add_inner=True, add_outer=True):
    """Add end-cap faces for a thick radial wall at the inner and outer boundaries."""
    ux = math.cos(phi)
    uy = math.sin(phi)
    vx = -math.sin(phi)
    vy = math.cos(phi)
    tw = wt / 2
    
    if add_inner:
        v_l_in_bot = bm_cap.verts.new((r_in * ux + tw * vx, r_in * uy + tw * vy, z_base))
        v_r_in_bot = bm_cap.verts.new((r_in * ux - tw * vx, r_in * uy - tw * vy, z_base))
        v_l_in_top = bm_cap.verts.new((r_in * ux + tw * vx, r_in * uy + tw * vy, z_base + h))
        v_r_in_top = bm_cap.verts.new((r_in * ux - tw * vx, r_in * uy - tw * vy, z_base + h))
        f_inner = bm_cap.faces.new([v_r_in_top, v_l_in_top, v_l_in_bot, v_r_in_bot])
        for loop, uv in zip(f_inner.loops, [(0,0),(1,0),(1,1),(0,1)]):
            loop[uv_cap].uv = uv
            
    if add_outer:
        v_l_out_bot = bm_cap.verts.new((r_out * ux + tw * vx, r_out * uy + tw * vy, z_base))
        v_r_out_bot = bm_cap.verts.new((r_out * ux - tw * vx, r_out * uy - tw * vy, z_base))
        v_l_out_top = bm_cap.verts.new((r_out * ux + tw * vx, r_out * uy + tw * vy, z_base + h))
        v_r_out_top = bm_cap.verts.new((r_out * ux - tw * vx, r_out * uy - tw * vy, z_base + h))
        f_outer = bm_cap.faces.new([v_l_out_top, v_r_out_top, v_r_out_bot, v_l_out_bot])
        for loop, uv in zip(f_outer.loops, [(0,0),(1,0),(1,1),(0,1)]):
            loop[uv_cap].uv = uv

def _add_circular_wall_caps(bm_cap, uv_cap, radius, phi_start, phi_end, wt, h, z_base, add_start=True, add_end=True):
    """Add end-cap faces for a thick circular wall arc at the start and end angles."""
    R_a = max(radius - wt / 2, 0.001)
    R_b = radius + wt / 2
    
    if add_start:
        v_a_bot_s = bm_cap.verts.new((R_a * math.cos(phi_start), R_a * math.sin(phi_start), z_base))
        v_b_bot_s = bm_cap.verts.new((R_b * math.cos(phi_start), R_b * math.sin(phi_start), z_base))
        v_a_top_s = bm_cap.verts.new((R_a * math.cos(phi_start), R_a * math.sin(phi_start), z_base + h))
        v_b_top_s = bm_cap.verts.new((R_b * math.cos(phi_start), R_b * math.sin(phi_start), z_base + h))
        f_start = bm_cap.faces.new([v_a_bot_s, v_b_bot_s, v_b_top_s, v_a_top_s])
        for loop, uv in zip(f_start.loops, [(0,0),(1,0),(1,1),(0,1)]):
            loop[uv_cap].uv = uv
            
    if add_end:
        v_a_bot_e = bm_cap.verts.new((R_a * math.cos(phi_end), R_a * math.sin(phi_end), z_base))
        v_b_bot_e = bm_cap.verts.new((R_b * math.cos(phi_end), R_b * math.sin(phi_end), z_base))
        v_a_top_e = bm_cap.verts.new((R_a * math.cos(phi_end), R_a * math.sin(phi_end), z_base + h))
        v_b_top_e = bm_cap.verts.new((R_b * math.cos(phi_end), R_b * math.sin(phi_end), z_base + h))
        f_end = bm_cap.faces.new([v_b_bot_e, v_a_bot_e, v_a_top_e, v_b_top_e])
        for loop, uv in zip(f_end.loops, [(0,0),(1,0),(1,1),(0,1)]):
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

    cache = _get_bmesh_cache()
    if cache is not None and src_mesh in cache:
        temp_bm = cache[src_mesh].copy()
    else:
        temp_bm = bmesh.new()
        temp_bm.from_mesh(src_mesh)
        if cache is not None:
            cache[src_mesh] = temp_bm.copy()

    bmesh.ops.transform(temp_bm, matrix=mat_combined, verts=temp_bm.verts)

    if cuts > 0:
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
    z_lifted = z_off + ts / 2

    if wall_type in ('CW', 'CCW'):
        phi = theta_mid + (alpha_r / 2) if wall_type == 'CW' else theta_mid - (alpha_r / 2)
        shift_val = thin_wall_offset if wall_type == 'CW' else -thin_wall_offset
        mat_base = Matrix.Translation(Vector((r_mid * math.cos(phi), r_mid * math.sin(phi), z_lifted))) @ Matrix.Rotation(phi, 4, 'Z') @ Matrix.Translation(Vector((0, shift_val, 0)))
        if wall_type == 'CW':
            mat_local = Matrix.Rotation(math.radians(90), 4, 'X')
        else: # CCW
            mat_local = Matrix.Rotation(math.radians(180), 4, 'Z') @ Matrix.Rotation(math.radians(90), 4, 'X')
        if flip_out:
            mat_local = Matrix.Rotation(math.radians(180), 4, 'Z') @ mat_local
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
    z_lifted = z_off + ts / 2

    if wall_type in ('CW', 'CCW'):
        phi = theta_mid + (alpha_r / 2) if wall_type == 'CW' else theta_mid - (alpha_r / 2)
        shift_val = thin_wall_offset if wall_type == 'CW' else -thin_wall_offset
        mat_base = Matrix.Translation(Vector((r_mid * math.cos(phi), r_mid * math.sin(phi), z_lifted))) @ Matrix.Rotation(phi, 4, 'Z') @ Matrix.Translation(Vector((0, shift_val, 0)))
        if wall_type == 'CW':
            mat_local = Matrix.Rotation(math.radians(90), 4, 'X')
        else:  # CCW
            mat_local = Matrix.Rotation(math.radians(180), 4, 'Z') @ Matrix.Rotation(math.radians(90), 4, 'X')
        if flip_out:
            mat_local = Matrix.Rotation(math.radians(180), 4, 'Z') @ mat_local
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

def _build_polar_floor(ctx, props, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, dirty_cells=None):
    """Build polar floor wedges and optional mesh-based tiles, optionally updating an existing BMesh."""
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
                if dirty_cells is not None and (z, r, theta) not in dirty_cells:
                    continue
                if (z, r, theta) in ctx['stair_top_cells']:
                    continue
                if props.wall_mode == 'cube' and level_cells[r][theta][0]:
                    continue

                if dirty_cells is None:
                    start_idx = len(bm_floor.faces)
                else:
                    existing_faces = set(bm_floor.faces)

                if len(level_cells[r][theta]) >= 8:
                    floor_idx = level_cells[r][theta][4]
                else:
                    floor_idx = level_cells[r][theta][4] if len(level_cells[r][theta]) > 4 else -1

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

                cell_id = get_cell_id(z, r, theta)
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



def _build_polar_walls(ctx, props, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, bm_cap=None, uv_layer_cap=None, materials_cap=None, dirty_cells=None):
    """Build polar wall and end-cap geometry, optionally updating an existing BMesh."""
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
    rings = maze_data.polar_rings
    ring_sectors = maze_data.ring_sectors
    alignment = props.polar_custom_alignment
    wall_rng = get_rng()

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
                        if dirty_cells is not None and (z, r, theta) not in dirty_cells:
                            continue
                        
                        if dirty_cells is None:
                            start_idx = len(bm_wall.faces)
                        else:
                            existing_faces = set(bm_wall.faces)
                        
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
                            
                            cell_id = get_cell_id(z, r, theta)
                            if dirty_cells is None:
                                bm_wall.faces.ensure_lookup_table()
                                for i in range(start_idx, len(bm_wall.faces)):
                                    bm_wall.faces[i][cell_layer] = cell_id
                            else:
                                for f in bm_wall.faces:
                                    if f not in existing_faces:
                                        f[cell_layer] = cell_id
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
                                
                                if src_out_wall and alignment != 'procedural':
                                    if alignment == 'trapezoid':
                                        _add_wall_polar_trapezoid(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'])
                                    elif alignment == 'bend':
                                        N_max = ring_sectors[-1]
                                        ratio = N_max // Nr if Nr > 0 else 1
                                        cuts = max(1, ratio * 8 - 1)
                                        _add_wall_polar_bend(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts)
                                else:
                                    radius = (r + 0.5) * ctx['ts']
                                    phi_start = theta * alpha_r
                                    phi_end = (theta + 1) * alpha_r
                                    _add_circular_wall(bm_wall, uv_wall, radius, phi_start, phi_end, ctx['ts'], ctx['seg_h'], ctx['wt'], z_off, flip=False, add_start=False, add_end=False)
                            elif is_stair:
                                radius = (r + 0.5) * ctx['ts']
                                phi_start = theta * alpha_r
                                phi_end = (theta + 1) * alpha_r
                                _add_circular_wall(bm_wall, uv_wall, radius, phi_start, phi_end, ctx['ts'], ctx['seg_h'], ctx['wt'], z_off, flip=False, add_start=False, add_end=False)

 
                        cell_id = get_cell_id(z, r, theta)
                        if dirty_cells is None:
                            bm_wall.faces.ensure_lookup_table()
                            for i in range(start_idx, len(bm_wall.faces)):
                                bm_wall.faces[i][cell_layer] = cell_id
                        else:
                            for f in bm_wall.faces:
                                if f not in existing_faces:
                                    f[cell_layer] = cell_id
    else:
        for z in ctx['z_range']:
            z_off_floor = z * ctx['wh']
            level_cells = ctx['cells_3d'][z]
 
            def has_cw_wall(r_val, t_val):
                """Check if the clockwise (CW) wall exists at (r_val, t_val) for the current level."""
                if r_val < 1 or r_val >= rings:
                    return False
                Nr_val = ring_sectors[r_val]
                t_val = t_val % Nr_val
                if name_suffix == "_EditHelper":
                    return True
                return level_cells[r_val][t_val][0]
 
            def has_in_wall(r_val, t_val):
                """Check if the inward (IN) wall exists at (r_val, t_val) for the current level."""
                if r_val < 1 or r_val >= rings:
                    return False
                Nr_val = ring_sectors[r_val]
                t_val = t_val % Nr_val
                if name_suffix == "_EditHelper":
                    return True
                return level_cells[r_val][t_val][1]
 
            def has_out_wall(r_val, t_val):
                """Check if the outward (OUT) wall exists at the outer boundary, accounting for entrance/exits."""
                if r_val != rings - 1:
                    return has_in_wall(r_val + 1, t_val)
                Nr_val = ring_sectors[r_val]
                t_val = t_val % Nr_val
                is_entrance_actual = False
                if z == 0 and maze_data.entrance:
                    en_r, en_theta, en_side = maze_data.entrance
                    if en_r == r_val and en_theta == t_val and en_side == 'OUT':
                        is_entrance_actual = True
                is_exit_actual = False
                if z == (ctx['floors'] - 1) and maze_data.exits:
                    for ex_r, ex_theta, ex_side in maze_data.exits:
                        if ex_r == r_val and ex_theta == t_val and ex_side == 'OUT':
                            is_exit_actual = True
                            break
                is_entrance = is_entrance_actual if name_suffix != "_EditHelper" else False
                is_exit = is_exit_actual if name_suffix != "_EditHelper" else False
                is_stair = (z, r_val, t_val) in ctx['stair_cells']
                
                if not is_entrance and not is_exit and not is_stair:
                    return True
                return False
 
            def get_junction_active(r_grid, theta_grid):
                """Return the set of active wall directions (CW_CIRC, CCW_CIRC, OUT_RAD, IN_RAD) at a junction."""
                active = set()
                if r_grid < rings:
                    N_ref = ring_sectors[r_grid]
                    t_idx = theta_grid % N_ref
                    
                    if has_in_wall(r_grid, (t_idx - 1) % N_ref):
                        active.add('CW_CIRC')
                    if has_in_wall(r_grid, t_idx):
                        active.add('CCW_CIRC')
                    if has_cw_wall(r_grid, t_idx):
                        active.add('OUT_RAD')
                        
                    # Inward radial wall in ring r_grid - 1
                    if r_grid > 1:
                        N_in = ring_sectors[r_grid - 1]
                        ratio = N_ref // N_in
                        if t_idx % ratio == 0:
                            theta_in_boundary = t_idx // ratio
                            if has_cw_wall(r_grid - 1, theta_in_boundary):
                                active.add('IN_RAD')
                else:
                    # r_grid == rings (outermost boundary)
                    N_ref = ring_sectors[rings - 1]
                    t_idx = theta_grid % N_ref
                    
                    if has_out_wall(rings - 1, (t_idx - 1) % N_ref):
                        active.add('CW_CIRC')
                    if has_out_wall(rings - 1, t_idx):
                        active.add('CCW_CIRC')
                    if has_cw_wall(rings - 1, t_idx):
                        active.add('IN_RAD')
                        
                return active
 
            def should_generate_cap(r_grid, theta_grid):
                """Determine whether an end-cap face should be generated at this junction based on active walls."""
                active = get_junction_active(r_grid, theta_grid)
                count = len(active)
                if count == 1:
                    return True
                if count == 2:
                    if active == {'CW_CIRC', 'CCW_CIRC'} or active == {'OUT_RAD', 'IN_RAD'}:
                        return False
                    return True
                return False
 
            for level in range(ctx['tiles_high']):
                z_off = z_off_floor + level * ctx['seg_h']
                
                for r in range(rings):
                    Nr = ring_sectors[r]
                    alpha_r = 2 * math.pi / Nr
                    
                    for theta in range(Nr):
                        if dirty_cells is not None and (z, r, theta) not in dirty_cells:
                            continue
 
                        if dirty_cells is None:
                            start_idx = len(bm_wall.faces)
                            start_cap_idx = len(bm_cap.faces)
                        else:
                            existing_faces = set(bm_wall.faces)
                            existing_cap_faces = set(bm_cap.faces)
 
                        cw_wall = True if name_suffix == "_EditHelper" else level_cells[r][theta][0]
                        in_wall = True if name_suffix == "_EditHelper" else level_cells[r][theta][1]
                        
                        sh_cw = ctx['seg_h']
                        if name_suffix == "_EditHelper" and not level_cells[r][theta][0]:
                            sh_cw = 0.02 * ctx['ts']
                            
                        sh_in = ctx['seg_h']
                        if name_suffix == "_EditHelper" and not level_cells[r][theta][1]:
                            sh_in = 0.02 * ctx['ts']
                            
                        src_cw_wall = None
                        src_in_wall = None
                        src_out_wall = None
                        
                        if len(level_cells[r][theta]) >= 8:
                            cw_idx = level_cells[r][theta][2]
                            in_idx = level_cells[r][theta][3]
                            out_idx = level_cells[r][theta][6] if len(level_cells[r][theta]) > 6 else in_idx
                        else:
                            cw_idx = level_cells[r][theta][2] if len(level_cells[r][theta]) > 2 else -1
                            in_idx = level_cells[r][theta][3] if len(level_cells[r][theta]) > 3 else -1
                            out_idx = level_cells[r][theta][6] if len(level_cells[r][theta]) > 6 else in_idx
                            
                        if ctx['wall_meshes_list']:
                            if isinstance(cw_idx, int) and 0 <= cw_idx < len(ctx['wall_meshes_list']):
                                src_cw_wall = ctx['wall_meshes_list'][cw_idx]
                            else:
                                src_cw_wall = wall_rng.choice(ctx['wall_meshes_list'])
                                
                            if isinstance(in_idx, int) and 0 <= in_idx < len(ctx['wall_meshes_list']):
                                src_in_wall = ctx['wall_meshes_list'][in_idx]
                            else:
                                src_in_wall = wall_rng.choice(ctx['wall_meshes_list'])
                                
                            if isinstance(out_idx, int) and 0 <= out_idx < len(ctx['wall_meshes_list']):
                                src_out_wall = ctx['wall_meshes_list'][out_idx]
                            else:
                                src_out_wall = wall_rng.choice(ctx['wall_meshes_list'])
                        elif ctx['custom_wall']:
                            src_cw_wall = ctx['custom_wall']
                            src_in_wall = ctx['custom_wall']
                            src_out_wall = ctx['custom_wall']
                            
                        if r >= 1 and cw_wall:
                            add_start = should_generate_cap(r, theta) if props.thin_wall_double_sided else False
                            theta_ref = round(theta * (ring_sectors[min(r + 1, rings - 1)] / ring_sectors[r]))
                            add_end = should_generate_cap(r + 1, theta_ref) if props.thin_wall_double_sided else False
                            
                            phi = theta * alpha_r
                            r_in = (r - 0.5) * ctx['ts']
                            r_out = (r + 0.5) * ctx['ts']
                            
                            if src_cw_wall and alignment != 'procedural':
                                if props.thin_wall_double_sided:
                                    if alignment == 'trapezoid':
                                        _add_wall_polar_trapezoid(bm_wall, src_cw_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'CCW', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=False, thin_wall_offset=-ctx['wt']/2)
                                        _add_wall_polar_trapezoid(bm_wall, src_cw_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'CCW', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=True, thin_wall_offset=ctx['wt']/2)
                                    elif alignment == 'bend':
                                        N_max = ring_sectors[-1]
                                        ratio = N_max // Nr if Nr > 0 else 1
                                        cuts = max(1, ratio * 8 - 1)
                                        _add_wall_polar_bend(bm_wall, src_cw_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'CCW', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=False, thin_wall_offset=-ctx['wt']/2)
                                        _add_wall_polar_bend(bm_wall, src_cw_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'CCW', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=True, thin_wall_offset=ctx['wt']/2)
                                    
                                    if add_start or add_end:
                                        _add_radial_wall_caps(bm_cap, uv_cap, phi, r_in, r_out, ctx['wt'], sh_cw, z_off, add_inner=add_start, add_outer=add_end)
                                else:
                                    if alignment == 'trapezoid':
                                        _add_wall_polar_trapezoid(bm_wall, src_cw_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'CCW', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=False, thin_wall_offset=0.0)
                                    elif alignment == 'bend':
                                        N_max = ring_sectors[-1]
                                        ratio = N_max // Nr if Nr > 0 else 1
                                        cuts = max(1, ratio * 8 - 1)
                                        _add_wall_polar_bend(bm_wall, src_cw_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'CCW', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=False, thin_wall_offset=0.0)
                            else:
                                if props.thin_wall_double_sided:
                                    _add_radial_wall(bm_wall, uv_wall, phi, r_in, r_out, ctx['ts'], sh_cw, ctx['wt'], z_off, add_inner=add_start, add_outer=add_end)
                                else:
                                    _add_radial_wall_flat(bm_wall, uv_wall, phi, r_in, r_out, ctx['ts'], sh_cw, z_off, facing_clockwise=False)
                                
                        if r >= 1 and in_wall:
                            add_start = should_generate_cap(r, theta) if props.thin_wall_double_sided else False
                            add_end = should_generate_cap(r, theta + 1) if props.thin_wall_double_sided else False
                            
                            radius = (r - 0.5) * ctx['ts']
                            phi_start = theta * alpha_r
                            phi_end = (theta + 1) * alpha_r
                            
                            if src_in_wall and alignment != 'procedural':
                                if props.thin_wall_double_sided:
                                    if alignment == 'trapezoid':
                                        _add_wall_polar_trapezoid(bm_wall, src_in_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'IN', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=False, thin_wall_offset=-ctx['wt']/2)
                                        _add_wall_polar_trapezoid(bm_wall, src_in_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'IN', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=True, thin_wall_offset=ctx['wt']/2)
                                    elif alignment == 'bend':
                                        N_max = ring_sectors[-1]
                                        ratio = N_max // Nr if Nr > 0 else 1
                                        cuts = max(1, ratio * 8 - 1)
                                        _add_wall_polar_bend(bm_wall, src_in_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'IN', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=False, thin_wall_offset=-ctx['wt']/2)
                                        _add_wall_polar_bend(bm_wall, src_in_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'IN', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=True, thin_wall_offset=ctx['wt']/2)
                                    
                                    if add_start or add_end:
                                        _add_circular_wall_caps(bm_cap, uv_cap, radius, phi_start, phi_end, ctx['wt'], sh_in, z_off, add_start=add_start, add_end=add_end)
                                else:
                                    if alignment == 'trapezoid':
                                        _add_wall_polar_trapezoid(bm_wall, src_in_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'IN', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=False, thin_wall_offset=0.0)
                                    elif alignment == 'bend':
                                        N_max = ring_sectors[-1]
                                        ratio = N_max // Nr if Nr > 0 else 1
                                        cuts = max(1, ratio * 8 - 1)
                                        _add_wall_polar_bend(bm_wall, src_in_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'IN', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=False, thin_wall_offset=0.0)
                            else:
                                if props.thin_wall_double_sided:
                                    _add_circular_wall(bm_wall, uv_wall, radius, phi_start, phi_end, ctx['ts'], sh_in, ctx['wt'], z_off, flip=False, add_start=add_start, add_end=add_end)
                                else:
                                    _add_circular_wall_flat(bm_wall, uv_wall, radius, phi_start, phi_end, ctx['ts'], sh_in, z_off, facing_outward=False)
                                
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
                                add_start = should_generate_cap(rings, theta) if props.thin_wall_double_sided else False
                                add_end = should_generate_cap(rings, theta + 1) if props.thin_wall_double_sided else False
                                
                                radius = (r + 0.5) * ctx['ts']
                                phi_start = theta * alpha_r
                                phi_end = (theta + 1) * alpha_r
                                
                                if src_out_wall and alignment != 'procedural':
                                    if props.thin_wall_double_sided:
                                        if alignment == 'trapezoid':
                                            _add_wall_polar_trapezoid(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=False, thin_wall_offset=-ctx['wt']/2)
                                            _add_wall_polar_trapezoid(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=True, thin_wall_offset=ctx['wt']/2)
                                        elif alignment == 'bend':
                                            N_max = ring_sectors[-1]
                                            ratio = N_max // Nr if Nr > 0 else 1
                                            cuts = max(1, ratio * 8 - 1)
                                            _add_wall_polar_bend(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=False, thin_wall_offset=-ctx['wt']/2)
                                            _add_wall_polar_bend(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=True, thin_wall_offset=ctx['wt']/2)
                                        
                                        if add_start or add_end:
                                            _add_circular_wall_caps(bm_cap, uv_cap, radius, phi_start, phi_end, ctx['wt'], sh_out, z_off, add_start=add_start, add_end=add_end)
                                    else:
                                        if alignment == 'trapezoid':
                                            _add_wall_polar_trapezoid(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], flip_out=False, thin_wall_offset=0.0)
                                        elif alignment == 'bend':
                                            N_max = ring_sectors[-1]
                                            ratio = N_max // Nr if Nr > 0 else 1
                                            cuts = max(1, ratio * 8 - 1)
                                            _add_wall_polar_bend(bm_wall, src_out_wall, ctx['mat_wall_offset'], uv_wall, wall_materials, 'OUT', r, theta, Nr, ctx['ts'], z_off, ctx['centered'], cuts=cuts, flip_out=False, thin_wall_offset=0.0)
                                else:
                                    if props.thin_wall_double_sided:
                                        _add_circular_wall(bm_wall, uv_wall, radius, phi_start, phi_end, ctx['ts'], sh_out, ctx['wt'], z_off, flip=False, add_start=add_start, add_end=add_end)
                                    else:
                                        _add_circular_wall_flat(bm_wall, uv_wall, radius, phi_start, phi_end, ctx['ts'], sh_out, z_off, facing_outward=True)
 
                        cell_id = get_cell_id(z, r, theta)
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
 
    if bm_wall.verts and not is_external_bm:
        _safe_remove_doubles(bm_wall, dist=0.001)
    if bm_cap.verts and not is_external_bm:
        _safe_remove_doubles(bm_cap, dist=0.001)
        
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
                cap_obj = _create_object_from_bm(bm_cap, f"FireMaze_WallEndCaps{name_suffix}", ctx['col'], None)
                for mat in cap_materials:
                    cap_obj.data.materials.append(mat)
                created_objects.append(cap_obj)
            else:
                bm_cap.free()
    else:
        if not is_external_cap:
            if props.single_wall_object:
                bm_cap.free()
            else:
                if bm_cap.verts:
                    cap_obj = _create_object_from_bm(bm_cap, f"FireMaze_WallEndCaps{name_suffix}", ctx['col'], None)
                    for mat in cap_materials:
                        cap_obj.data.materials.append(mat)
                    created_objects.append(cap_obj)
                else:
                    bm_cap.free()


def _build_polar_roof(ctx, props, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, dirty_cells=None):
    """Build polar roof wedges and optional mesh-based tiles, optionally updating an existing BMesh."""
    if not (props.is_editing and props.wall_mode == 'thin' and name_suffix != "_Collider" and not props.edit_roof):
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
        rings = maze_data.polar_rings
        ring_sectors = maze_data.ring_sectors
        alignment = props.polar_custom_alignment

        for z in ctx['z_range']:
            z_off_roof = z * ctx['wh'] + ctx['wh']
            level_cells = ctx['cells_3d'][z]
            for r in range(rings):
                Nr = ring_sectors[r]
                alpha_r = 2 * math.pi / Nr
                for theta in range(Nr):
                    if dirty_cells is not None and (z, r, theta) not in dirty_cells:
                        continue
                    if (z, r, theta) in ctx['stair_bottom_cells']:
                        continue
                    is_wall = level_cells[r][theta][0] if props.wall_mode == 'cube' else False
                    if props.wall_mode == 'cube':
                        if not is_wall:
                            continue
                        if props.cube_mode_pillar and (ctx['wall_meshes_list'] or ctx['custom_wall']):
                            continue
                            
                    if dirty_cells is None:
                        start_idx = len(bm_roof.faces)
                    else:
                        existing_faces = set(bm_roof.faces)

                    if len(level_cells[r][theta]) >= 8:
                        roof_idx = level_cells[r][theta][5]
                    else:
                        roof_idx = level_cells[r][theta][5] if len(level_cells[r][theta]) > 5 else -1
                    
                    if roof_idx < 0 and ctx['roof_meshes_list']:
                        roof_idx = 0

                    src_roof = None
                    if ctx['roof_meshes_list'] and isinstance(roof_idx, int) and 0 <= roof_idx < len(ctx['roof_meshes_list']):
                        src_roof = ctx['roof_meshes_list'][roof_idx]
                    elif ctx['custom_roof']:
                        src_roof = ctx['custom_roof']

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

                    cell_id = get_cell_id(z, r, theta)
                    if dirty_cells is None:
                        bm_roof.faces.ensure_lookup_table()
                        for i in range(start_idx, len(bm_roof.faces)):
                            bm_roof.faces[i][cell_layer] = cell_id
                    else:
                        for f in bm_roof.faces:
                            if f not in existing_faces:
                                f[cell_layer] = cell_id

        if not is_external_bm:
            bmesh.ops.remove_doubles(bm_roof, verts=bm_roof.verts, dist=0.001)
            roof_obj = _create_object_from_bm(bm_roof, f"FireMaze_Roof{name_suffix}", ctx['col'], None)
            for mat in roof_materials:
                roof_obj.data.materials.append(mat)
            created_objects.append(roof_obj)


def _build_polar_stairs(ctx, props, maze_data, created_objects, name_suffix, bm=None, uv_layer=None, materials=None, dirty_cells=None):
    """Build stair/ramp geometry for a polar grid, optionally updating an existing BMesh."""
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
                
                if dirty_cells is not None and (zstair, sy, sx) not in dirty_cells:
                    continue

                if dirty_cells is None:
                    start_idx = len(bm_stairs.faces)
                else:
                    existing_faces = set(bm_stairs.faces)

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
                
                if style == 'ramp' and ctx['custom_ramp_mesh']:
                    _add_mesh_at(bm_stairs, ctx['custom_ramp_mesh'].data if ctx['custom_ramp_mesh'].type == 'MESH' else ctx['custom_ramp_mesh'], mat, uv_stairs, final_materials_list=stair_materials)
                elif style == 'stair' and ctx['custom_stair_mesh']:
                    _add_mesh_at(bm_stairs, ctx['custom_stair_mesh'].data if ctx['custom_stair_mesh'].type == 'MESH' else ctx['custom_stair_mesh'], mat, uv_stairs, final_materials_list=stair_materials)
                elif style == 'ramp':
                    _build_ramp_1x1(bm_stairs, uv_stairs, cx, cy, ctx['ts'], ctx['wh'], z_offset, combined_rot @ ctx['mat_floor_offset'])
                else:
                    _build_spiral_stair_1x1(bm_stairs, uv_stairs, cx, cy, ctx['ts'], ctx['wh'], z_offset, combined_rot @ ctx['mat_floor_offset'])

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


def _build_polar_maze_objects_impl(
    props: bpy.types.PropertyGroup,
    maze_data: MazeData,
    context: bpy.types.Context,
    collection: bpy.types.Collection = None,
    force_simple: bool = False,
    name_suffix: str = "",
    dirty_cells: set = None,
) -> bpy.types.Collection:
    """Build all polar maze meshes (floor, walls, roof, stairs, guide path, colliders) into a collection."""

    ctx = _prepare_maze_building_context(props, maze_data, context, collection, force_simple)
    created_objects = []

    _build_polar_floor(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
    _build_polar_walls(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
    _build_polar_roof(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)
    _build_polar_stairs(ctx, props, maze_data, created_objects, name_suffix, dirty_cells=dirty_cells)

    # Build guide path if requested
    if not force_simple and name_suffix == "":
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
            merged_obj = _merge_maze_objects(meshes_to_merge, context, name="FireMaze_Merged")
        else:
            merged_obj = None

    # Post-process
    if not name_suffix and not props.is_editing:
        if props.merge_objects:
            visual_meshes = [merged_obj] if (merged_obj and merged_obj.type == 'MESH') else []
        else:
            visual_meshes = [obj for obj in created_objects if obj.type == 'MESH']
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
