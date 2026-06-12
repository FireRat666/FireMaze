"""Post-processing operations for FireMaze meshes (vertex painting, merges, lightmaps, props)."""

import math
import bpy
import bmesh
import logging
from collections import deque
from mathutils import Vector
from ..utils import is_valid_ref, _resolve_cells_3d, get_rng
from .bmesh_utils import _compute_grid_distances, _create_object_from_bm

logger = logging.getLogger(__name__)

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
    rng = get_rng()

    cells_3d, floors = _resolve_cells_3d(maze_data.cells)
    resolved_cells = cells_3d[0]
 
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
        
        for z in range(floors):
            resolved_cells = cells_3d[z]
            # 1. Torches
            if torch_mesh:
                torch_src = torch_mesh
                density = props.prop_torch_density
                offset = 0.02 * ts
                
                for r in range(1, rings):
                    Nr = ring_sectors[r]
                    alpha_r = 2 * math.pi / Nr
                    for theta in range(Nr):
                        cw_wall = resolved_cells[r][theta][0]
                        in_wall = resolved_cells[r][theta][1]
                        
                        r_mid = r * ts
                        theta_mid = (theta + 0.5) * alpha_r
                        
                        # Clockwise wall torch
                        if cw_wall and rng.random() < density:
                            phi_cw = (theta + 1) * alpha_r
                            pos_x = r_mid * math.cos(phi_cw) + offset * math.sin(phi_cw)
                            pos_y = r_mid * math.sin(phi_cw) - offset * math.cos(phi_cw)
                            pos = (pos_x, pos_y, z * wh + 0.6 * wh)
                            place_prop(torch_src, pos, phi_cw - math.pi / 2)
                            
                        # Inward wall torch
                        if in_wall and rng.random() < density:
                            r_in = (r - 0.5) * ts
                            pos_x = (r_in + offset) * math.cos(theta_mid)
                            pos_y = (r_in + offset) * math.sin(theta_mid)
                            pos = (pos_x, pos_y, z * wh + 0.6 * wh)
                            place_prop(torch_src, pos, theta_mid)

                        # Outer boundary torch
                        if r == rings - 1:
                            is_entrance = (z == 0 and theta == 0)
                            is_exit = False
                            if z == floors - 1 and maze_data.exits:
                                for ex_r, ex_theta, _ in maze_data.exits:
                                    if ex_r == r and ex_theta == theta:
                                        is_exit = True
                                        break
                            if not is_entrance and not is_exit and rng.random() < density:
                                r_out = (r + 0.5) * ts
                                pos_x = (r_out - offset) * math.cos(theta_mid)
                                pos_y = (r_out - offset) * math.sin(theta_mid)
                                pos = (pos_x, pos_y, z * wh + 0.6 * wh)
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
                        
                        if r >= 1 and not resolved_cells[r][theta][0]:
                            accessible.append(('CW', (r, (theta + 1) % Nr)))
                        if r >= 1 and not resolved_cells[r][(theta - 1) % Nr][0]:
                            accessible.append(('CCW', (r, (theta - 1) % Nr)))
                        if r > 0 and not resolved_cells[r][theta][1]:
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
                                if not resolved_cells[r + 1][theta][1]:
                                    accessible.append(('OUT', (r + 1, theta)))
                            elif Nr == 1:
                                for t in range(N_out):
                                    if not resolved_cells[r + 1][t][1]:
                                        accessible.append(('OUT', (r + 1, t)))
                            else:
                                if not resolved_cells[r + 1][2 * theta][1]:
                                    accessible.append(('OUT', (r + 1, 2 * theta)))
                                if not resolved_cells[r + 1][2 * theta + 1][1]:
                                    accessible.append(('OUT', (r + 1, 2 * theta + 1)))
                                    
                        if len(accessible) == 1 and r > 0:
                            is_ent_or_exit = False
                            if z == 0 and maze_data.entrance and (r, theta) == maze_data.entrance[0:2]:
                                is_ent_or_exit = True
                            if z == floors - 1 and maze_data.exits:
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
                                    place_prop(chest_src, (pos_x, pos_y, z * wh), theta_mid + math.pi)
                                elif direction == 'OUT':
                                    pos_x = (r_mid - chest_offset) * math.cos(theta_mid)
                                    pos_y = (r_mid - chest_offset) * math.sin(theta_mid)
                                    place_prop(chest_src, (pos_x, pos_y, z * wh), theta_mid)
                                elif direction == 'CW':
                                    pos_x = r_mid * math.cos(theta_mid - alpha_r / 4)
                                    pos_y = r_mid * math.sin(theta_mid - alpha_r / 4)
                                    place_prop(chest_src, (pos_x, pos_y, z * wh), theta_mid - math.pi / 2)
                                else: # CCW
                                    pos_x = r_mid * math.cos(theta_mid + alpha_r / 4)
                                    pos_y = r_mid * math.sin(theta_mid + alpha_r / 4)
                                    place_prop(chest_src, (pos_x, pos_y, z * wh), theta_mid + math.pi / 2)

            # 3. Doors
            if door_mesh:
                door_src = door_mesh
                
                if z == 0 and maze_data.entrance:
                    er, etheta, eside = maze_data.entrance
                    eNr = ring_sectors[er]
                    ealpha = 2 * math.pi / eNr
                    etheta_mid = (etheta + 0.5) * ealpha
                    r_door = (er + 0.5) * ts
                    pos_x = r_door * math.cos(etheta_mid)
                    pos_y = r_door * math.sin(etheta_mid)
                    place_prop(door_src, (pos_x, pos_y, z * wh), etheta_mid)
                    
                if z == floors - 1 and maze_data.exits:
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
                        place_prop(door_src, (pos_x, pos_y, z * wh), extheta_mid)
        return

    for z in range(floors):
        resolved_cells = cells_3d[z]
        # 1. Torches
        if torch_mesh:
            torch_src = torch_mesh
            density = props.prop_torch_density
            offset = 0.02 * ts
            
            for y in range(maze_data.depth):
                for x in range(maze_data.width):
                    if wall_mode == 'thin':
                        c = resolved_cells[y][x]
                        # North wall
                        if c[0] and rng.random() < density:
                            pos = (x * ts + ts/2, (y + 1) * ts - offset, z * wh + 0.6 * wh)
                            place_prop(torch_src, pos, math.pi)
                        # South wall
                        if c[1] and rng.random() < density:
                            pos = (x * ts + ts/2, y * ts + offset, z * wh + 0.6 * wh)
                            place_prop(torch_src, pos, 0.0)
                        # East wall
                        if c[2] and rng.random() < density:
                            pos = ((x + 1) * ts - offset, y * ts + ts/2, z * wh + 0.6 * wh)
                            place_prop(torch_src, pos, math.pi / 2)
                        # West wall
                        if c[3] and rng.random() < density:
                            pos = (x * ts + offset, y * ts + ts/2, z * wh + 0.6 * wh)
                            place_prop(torch_src, pos, -math.pi / 2)
                    else: # cube
                        if resolved_cells[y][x][0]: # wall cube
                            # North
                            if y + 1 < maze_data.depth and not resolved_cells[y+1][x][0]:
                                if rng.random() < density:
                                    pos = (x * ts + ts/2, (y + 1) * ts + offset, z * wh + 0.6 * wh)
                                    place_prop(torch_src, pos, 0.0)
                            # South
                            if y - 1 >= 0 and not resolved_cells[y-1][x][0]:
                                if rng.random() < density:
                                    pos = (x * ts + ts/2, y * ts - offset, z * wh + 0.6 * wh)
                                    place_prop(torch_src, pos, math.pi)
                            # East
                            if x + 1 < maze_data.width and not resolved_cells[y][x+1][0]:
                                if rng.random() < density:
                                    pos = ((x + 1) * ts + offset, y * ts + ts/2, z * wh + 0.6 * wh)
                                    place_prop(torch_src, pos, math.pi / 2)
                            # West
                            if x - 1 >= 0 and not resolved_cells[y][x-1][0]:
                                if rng.random() < density:
                                    pos = (x * ts - offset, y * ts + ts/2, z * wh + 0.6 * wh)
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
                        c = resolved_cells[y][x]
                        if sum(c[:4]) == 3:
                            is_dead = True
                            if not c[0]: open_dir = 'N'
                            elif not c[1]: open_dir = 'S'
                            elif not c[2]: open_dir = 'E'
                            else: open_dir = 'W'
                    else: # cube
                        if not resolved_cells[y][x][0]:
                            neighbors = []
                            for d, dx, dy in [('N', 0, 1), ('S', 0, -1), ('E', 1, 0), ('W', -1, 0)]:
                                nx, y_neighbor = x + dx, y + dy
                                if 0 <= nx < maze_data.width and 0 <= y_neighbor < maze_data.depth:
                                    if not resolved_cells[y_neighbor][nx][0]:
                                        neighbors.append(d)
                            if len(neighbors) == 1:
                                is_dead = True
                                open_dir = neighbors[0]
                                
                    if is_dead:
                        is_ent_or_exit = False
                        if z == 0 and maze_data.entrance and (x, y) == (maze_data.entrance[0], maze_data.entrance[1]):
                            is_ent_or_exit = True
                        if z == floors - 1 and maze_data.exits:
                            for ex_x, ex_y, _ in maze_data.exits:
                                if (x, y) == (ex_x, ex_y):
                                    is_ent_or_exit = True
                        
                        if not is_ent_or_exit and rng.random() < density:
                            if open_dir == 'N':
                                pos = (x * ts + ts/2, y * ts + chest_offset, z * wh)
                                place_prop(chest_src, pos, 0.0)
                            elif open_dir == 'S':
                                pos = (x * ts + ts/2, (y + 1) * ts - chest_offset, z * wh)
                                place_prop(chest_src, pos, math.pi)
                            elif open_dir == 'E':
                                pos = (x * ts + chest_offset, y * ts + ts/2, z * wh)
                                place_prop(chest_src, pos, math.pi / 2)
                            elif open_dir == 'W':
                                pos = ((x + 1) * ts - chest_offset, y * ts + ts/2, z * wh)
                                place_prop(chest_src, pos, -math.pi / 2)

        # 3. Doors (Entrance / Exits)
        if door_mesh:
            door_src = door_mesh
            if z == 0 and maze_data.entrance:
                ex, ey, side = maze_data.entrance
                if side == 'N':
                    pos = (ex * ts + ts/2, (ey + 1) * ts, z * wh)
                    place_prop(door_src, pos, 0.0)
                elif side == 'S':
                    pos = (ex * ts + ts/2, ey * ts, z * wh)
                    place_prop(door_src, pos, 0.0)
                elif side == 'E':
                    pos = ((ex + 1) * ts, ey * ts + ts/2, z * wh)
                    place_prop(door_src, pos, math.pi / 2)
                elif side == 'W':
                    pos = (ex * ts, ey * ts + ts/2, z * wh)
                    place_prop(door_src, pos, -math.pi / 2)
            if z == floors - 1 and maze_data.exits:
                for ex, ey, side in maze_data.exits:
                    if side == 'N':
                        pos = (ex * ts + ts/2, (ey + 1) * ts, z * wh)
                        place_prop(door_src, pos, 0.0)
                    elif side == 'S':
                        pos = (ex * ts + ts/2, ey * ts, z * wh)
                        place_prop(door_src, pos, 0.0)
                    elif side == 'E':
                        pos = ((ex + 1) * ts, ey * ts + ts/2, z * wh)
                        place_prop(door_src, pos, math.pi / 2)
                    elif side == 'W':
                        pos = (ex * ts, ey * ts + ts/2, z * wh)
                        place_prop(door_src, pos, -math.pi / 2)
