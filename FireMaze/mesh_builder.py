import math
import random
import bpy
import bmesh
from collections import deque
from mathutils import Matrix, Vector


def _get_wall_segments(maze_data):
    segments = set()
    for y in range(maze_data.depth):
        for x in range(maze_data.width):
            c = maze_data.cells[y][x]
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
    bm.verts.ensure_lookup_table()
    face = bm.faces.new(verts)
    for loop, uv in zip(face.loops, uvs):
        loop[uv_layer].uv = uv

def _merge_bmesh_geometries(src_bm, dst_bm):
    # Use a unique local temporary mesh to copy geometry at C-speed
    # without sharing state or causing layout mutation crashes.
    temp_mesh = bpy.data.meshes.new("_FM_Temp_Local")
    src_bm.to_mesh(temp_mesh)
    dst_bm.from_mesh(temp_mesh)
    bpy.data.meshes.remove(temp_mesh)

def _add_mesh_at(bm, src_mesh, matrix, uv_layer, final_materials_list=None, swap_uv=False, temp_mesh=None):
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

    temp_bm = bmesh.new()
    temp_bm.from_mesh(src_mesh)
    bmesh.ops.transform(temp_bm, matrix=matrix, verts=temp_bm.verts)
    
    if swap_uv:
        src_uv = temp_bm.loops.layers.uv.active
        if not src_uv and temp_bm.loops.layers.uv:
            src_uv = temp_bm.loops.layers.uv[0]
        if src_uv:
            for f in temp_bm.faces:
                for loop in f.loops:
                    u, v = loop[src_uv].uv
                    loop[src_uv].uv = (v, u)

    if final_materials_list is not None and material_map:
        for f in temp_bm.faces:
            if f.material_index < len(material_map):
                f.material_index = material_map[f.material_index]
            else:
                f.material_index = 0

    _merge_bmesh_geometries(temp_bm, bm)
    temp_bm.free()

def _add_floor_tile_transformed(bm, uv_layer, x, y, ts, mat_offset):
    cx = x * ts + ts / 2
    cy = y * ts + ts / 2
    T = Matrix.Translation(Vector((cx, cy, 0.0))) @ mat_offset

    t2 = ts / 2
    pts = [(-t2, -t2, 0.0), (t2, -t2, 0.0), (t2, t2, 0.0), (-t2, t2, 0.0)]
    verts = [bm.verts.new(T @ Vector(p)) for p in pts]
    bm.verts.ensure_lookup_table()
    face = bm.faces.new(verts)
    uvs = [(0, 0), (1, 0), (1, 1), (0, 1)]
    for loop, uv in zip(face.loops, uvs):
        loop[uv_layer].uv = uv

def _add_horizontal_wall_faces_transformed(bm, uv_layer, x, y, ts, wh, wt, mat_offset, z_base=0):
    xc = x * ts + ts / 2
    yc = y * ts
    zc = z_base + wh / 2
    T = Matrix.Translation(Vector((xc, yc, zc))) @ mat_offset
    tw = wt / 2
    w2 = wh / 2
    t2 = ts / 2

    v0 = T @ Vector((-t2, -tw, -w2))
    v1 = T @ Vector((t2, -tw, -w2))
    v2 = T @ Vector((t2, -tw, w2))
    v3 = T @ Vector((-t2, -tw, w2))

    v4 = T @ Vector((-t2, tw, -w2))
    v5 = T @ Vector((t2, tw, -w2))
    v6 = T @ Vector((t2, tw, w2))
    v7 = T @ Vector((-t2, tw, w2))

    bm.verts.ensure_lookup_table()
    f1 = bm.faces.new([bm.verts.new(v0), bm.verts.new(v1), bm.verts.new(v2), bm.verts.new(v3)])
    for loop, uv in zip(f1.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop[uv_layer].uv = uv

    f2 = bm.faces.new([bm.verts.new(v5), bm.verts.new(v4), bm.verts.new(v7), bm.verts.new(v6)])
    for loop, uv in zip(f2.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop[uv_layer].uv = uv

def _add_horizontal_end_cap_transformed(bm, uv_layer, x, y, ts, wh, wt, mat_offset, h_positions=None, z_base=0):
    xc = x * ts + ts / 2
    yc = y * ts
    zc = z_base + wh / 2
    T = Matrix.Translation(Vector((xc, yc, zc))) @ mat_offset
    tw = wt / 2
    w2 = wh / 2
    t2 = ts / 2

    if not h_positions or (x - 1, y) not in h_positions:
        v0 = T @ Vector((-t2, tw, -w2))
        v1 = T @ Vector((-t2, -tw, -w2))
        v2 = T @ Vector((-t2, -tw, w2))
        v3 = T @ Vector((-t2, tw, w2))
        bm.verts.ensure_lookup_table()
        f = bm.faces.new([bm.verts.new(v0), bm.verts.new(v1), bm.verts.new(v2), bm.verts.new(v3)])
        for loop, uv in zip(f.loops, [(0,0),(wt,0),(wt,wh),(0,wh)]):
            loop[uv_layer].uv = uv

    if not h_positions or (x + 1, y) not in h_positions:
        v4 = T @ Vector((t2, -tw, -w2))
        v5 = T @ Vector((t2, tw, -w2))
        v6 = T @ Vector((t2, tw, w2))
        v7 = T @ Vector((t2, -tw, w2))
        bm.verts.ensure_lookup_table()
        f = bm.faces.new([bm.verts.new(v4), bm.verts.new(v5), bm.verts.new(v6), bm.verts.new(v7)])
        for loop, uv in zip(f.loops, [(0,0),(wt,0),(wt,wh),(0,wh)]):
            loop[uv_layer].uv = uv

def _add_horizontal_roof_face_transformed(bm, uv_layer, x, y, ts, wh, wt, mat_offset):
    xc = x * ts + ts / 2
    yc = y * ts
    T = Matrix.Translation(Vector((xc, yc, wh))) @ mat_offset
    tw = wt / 2
    t2 = ts / 2
    v0 = T @ Vector((-t2, -tw, 0))
    v1 = T @ Vector((t2, -tw, 0))
    v2 = T @ Vector((t2, tw, 0))
    v3 = T @ Vector((-t2, tw, 0))
    bm.verts.ensure_lookup_table()
    face = bm.faces.new([bm.verts.new(v0), bm.verts.new(v1), bm.verts.new(v2), bm.verts.new(v3)])
    for loop, uv in zip(face.loops, [(0, 0), (ts, 0), (ts, wt), (0, wt)]):
        loop[uv_layer].uv = uv

def _add_vertical_wall_faces_transformed(bm, uv_layer, x, y, ts, wh, wt, mat_offset, z_base=0):
    xc = x * ts
    yc = y * ts + ts / 2
    zc = z_base + wh / 2
    T = Matrix.Translation(Vector((xc, yc, zc))) @ mat_offset
    tw = wt / 2
    w2 = wh / 2
    t2 = ts / 2

    v0 = T @ Vector((-tw, -t2, -w2))
    v1 = T @ Vector((-tw, t2, -w2))
    v2 = T @ Vector((-tw, t2, w2))
    v3 = T @ Vector((-tw, -t2, w2))

    v4 = T @ Vector((tw, -t2, -w2))
    v5 = T @ Vector((tw, t2, -w2))
    v6 = T @ Vector((tw, t2, w2))
    v7 = T @ Vector((tw, -t2, w2))

    bm.verts.ensure_lookup_table()
    f1 = bm.faces.new([bm.verts.new(v0), bm.verts.new(v3), bm.verts.new(v2), bm.verts.new(v1)])
    for loop, uv in zip(f1.loops, [(0, 0), (0, 1), (1, 1), (1, 0)]):
        loop[uv_layer].uv = uv

    f2 = bm.faces.new([bm.verts.new(v4), bm.verts.new(v5), bm.verts.new(v6), bm.verts.new(v7)])
    for loop, uv in zip(f2.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop[uv_layer].uv = uv

def _add_vertical_end_cap_transformed(bm, uv_layer, x, y, ts, wh, wt, mat_offset, v_positions=None, z_base=0):
    xc = x * ts
    yc = y * ts + ts / 2
    zc = z_base + wh / 2
    T = Matrix.Translation(Vector((xc, yc, zc))) @ mat_offset
    tw = wt / 2
    w2 = wh / 2
    t2 = ts / 2

    if not v_positions or (x, y - 1) not in v_positions:
        v0 = T @ Vector((-tw, -t2, -w2))
        v1 = T @ Vector((tw, -t2, -w2))
        v2 = T @ Vector((tw, -t2, w2))
        v3 = T @ Vector((-tw, -t2, w2))
        bm.verts.ensure_lookup_table()
        f = bm.faces.new([bm.verts.new(v0), bm.verts.new(v1), bm.verts.new(v2), bm.verts.new(v3)])
        for loop, uv in zip(f.loops, [(0,0),(wt,0),(wt,wh),(0,wh)]):
            loop[uv_layer].uv = uv

    if not v_positions or (x, y + 1) not in v_positions:
        v4 = T @ Vector((tw, t2, -w2))
        v5 = T @ Vector((-tw, t2, -w2))
        v6 = T @ Vector((-tw, t2, w2))
        v7 = T @ Vector((tw, t2, w2))
        bm.verts.ensure_lookup_table()
        f = bm.faces.new([bm.verts.new(v4), bm.verts.new(v5), bm.verts.new(v6), bm.verts.new(v7)])
        for loop, uv in zip(f.loops, [(0,0),(wt,0),(wt,wh),(0,wh)]):
            loop[uv_layer].uv = uv

def _add_vertical_roof_face_transformed(bm, uv_layer, x, y, ts, wh, wt, mat_offset, trim_south=False, trim_north=False):
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
    bm.verts.ensure_lookup_table()
    face = bm.faces.new([bm.verts.new(v0), bm.verts.new(v1), bm.verts.new(v2), bm.verts.new(v3)])
    for loop, uv in zip(face.loops, [(0, 0), (wt, 0), (wt, dy), (0, dy)]):
        loop[uv_layer].uv = uv

def _add_vertical_roof_filler_transformed(bm, uv_layer, xc, yc, wh, tw, y_lo_rel, y_hi_rel, hx0_rel, hx1_rel, mat_offset):
    T = Matrix.Translation(Vector((xc, yc, wh))) @ mat_offset
    v0 = T @ Vector((hx0_rel, y_lo_rel, 0))
    v1 = T @ Vector((hx1_rel, y_lo_rel, 0))
    v2 = T @ Vector((hx1_rel, y_hi_rel, 0))
    v3 = T @ Vector((hx0_rel, y_hi_rel, 0))
    bm.verts.ensure_lookup_table()
    f = bm.faces.new([bm.verts.new(v0), bm.verts.new(v1), bm.verts.new(v2), bm.verts.new(v3)])
    df = y_hi_rel - y_lo_rel
    for loop, uv in zip(f.loops, [(0,0),(tw,0),(tw,df),(0,df)]):
        loop[uv_layer].uv = uv

def _add_cube_roof_face_transformed(bm, uv_layer, cx, cy, sx, sy, sz, mat_offset):
    T = Matrix.Translation(Vector((cx, cy, sz))) @ mat_offset
    pts = [(-sx/2, -sy/2, 0.0), (sx/2, -sy/2, 0.0), (sx/2, sy/2, 0.0), (-sx/2, sy/2, 0.0)]
    verts = [bm.verts.new(T @ Vector(p)) for p in pts]
    bm.verts.ensure_lookup_table()
    face = bm.faces.new(verts)
    for loop, uv in zip(face.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop[uv_layer].uv = uv

def _create_object_from_bm(bm, name, collection, material):
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
    if not props.generate_guide or not maze_data.guide_path:
        return None

    ts = props.tile_size
    ho = props.guide_height_offset
    amp = props.guide_wave_amplitude
    freq = props.guide_wave_frequency

    curve_data = bpy.data.curves.new("FireMaze_Guide", type='CURVE')
    curve_data.dimensions = '3D'

    spline = curve_data.splines.new(type='POLY')
    spline.points.add(len(maze_data.guide_path) - 1)

    for i, coord in enumerate(maze_data.guide_path):
        if maze_data.grid_type == 'polar':
            r, theta = coord
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
            x, y = coord
            px = x * ts + ts / 2
            py = y * ts + ts / 2

        pz = ho
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
    if not obj or obj.type != 'MESH':
        return
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)
    bm.normal_update()
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()

def _generate_lightmap_on_obj(obj, context, method='smart'):
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
        except Exception:
            pass
            
    # Add a new UV map named 'Lightmap'
    uv_map = obj.data.uv_layers.get("Lightmap")
    if not uv_map:
        uv_map = obj.data.uv_layers.new(name="Lightmap")
    
    # Set the new UV map as active for unwrapping
    original_active_uv = obj.data.uv_layers.active
    obj.data.uv_layers.active = uv_map
    
    # Select only this object and make it active
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
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
            except Exception:
                # Fallback to default arguments if API differs
                bpy.ops.uv.lightmap_pack()
        else:
            # Perform Smart UV Project (angle_limit 66, island_margin 0.02)
            bpy.ops.uv.smart_project(angle_limit=66.0, island_margin=0.02)
        # Switch back to Object Mode
        bpy.ops.object.mode_set(mode='OBJECT')
    except Exception as e:
        print(f"Failed to unwrap lightmap for {obj.name}: {e}")
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass
            
    # Restore original active UV map
    if original_active_uv:
        obj.data.uv_layers.active = original_active_uv
        
    # Restore original active object and selection
    bpy.ops.object.select_all(action='DESELECT')
    for o in original_selected:
        try:
            o.select_set(True)
        except Exception:
            pass
    context.view_layer.objects.active = original_active
    
    if original_active:
        try:
            bpy.ops.object.mode_set(mode=original_mode)
        except Exception:
            pass

def _merge_maze_objects(objects, context, name="FireMaze_Merged"):
    objects = [obj for obj in objects if obj is not None]
    if len(objects) == 0:
        return None
    if len(objects) == 1:
        objects[0].name = name
        return objects[0]

    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode='OBJECT')

    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)

    context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    
    merged_obj = context.view_layer.objects.active
    merged_obj.name = name
    return merged_obj

def _optimize_coplanar_on_obj(obj):
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
    if maze_data.grid_type == 'polar':
        start_r = maze_data.entrance[0]
        start_theta = maze_data.entrance[1]
        
        distances = {}
        queue = deque([(start_r, start_theta, 0)])
        distances[(start_r, start_theta)] = 0
        
        rings = maze_data.polar_rings
        ring_sectors = maze_data.ring_sectors
        
        while queue:
            r, theta, d = queue.popleft()
            Nr = ring_sectors[r]
            
            accessible = []
            if r >= 1 and not maze_data.cells[r][theta][0]:
                accessible.append((r, (theta + 1) % Nr))
            if r >= 1 and not maze_data.cells[r][(theta - 1) % Nr][0]:
                accessible.append((r, (theta - 1) % Nr))
            if r > 0 and not maze_data.cells[r][theta][1]:
                N_in = ring_sectors[r - 1]
                if N_in == Nr:
                    accessible.append((r - 1, theta))
                elif N_in == 1:
                    accessible.append((r - 1, 0))
                else:
                    accessible.append((r - 1, theta // 2))
            if r < rings - 1:
                N_out = ring_sectors[r + 1]
                if N_out == Nr:
                    if not maze_data.cells[r + 1][theta][1]:
                        accessible.append((r + 1, theta))
                elif Nr == 1:
                    for t in range(N_out):
                        if not maze_data.cells[r + 1][t][1]:
                            accessible.append((r + 1, t))
                else:
                    if not maze_data.cells[r + 1][2 * theta][1]:
                        accessible.append((r + 1, 2 * theta))
                    if not maze_data.cells[r + 1][2 * theta + 1][1]:
                        accessible.append((r + 1, 2 * theta + 1))
            
            for nr, ntheta in accessible:
                if (nr, ntheta) not in distances:
                    distances[(nr, ntheta)] = d + 1
                    queue.append((nr, ntheta, d + 1))
        return distances

    width = maze_data.width
    depth = maze_data.depth
    # Start at entrance
    start_x = maze_data.entrance[0]
    start_y = maze_data.entrance[1]
    
    distances = {}
    queue = deque([(start_x, start_y, 0)])
    distances[(start_x, start_y)] = 0
    
    while queue:
        cx, cy, d = queue.popleft()
        
        # Determine neighbors
        neighbors = []
        if wall_mode == 'thin':
            c = maze_data.cells[cy][cx]
            # North
            if not c[0] and cy + 1 < depth:
                neighbors.append((cx, cy + 1))
            # South
            if not c[1] and cy - 1 >= 0:
                neighbors.append((cx, cy - 1))
            # East
            if not c[2] and cx + 1 < width:
                neighbors.append((cx + 1, cy))
            # West
            if not c[3] and cx - 1 >= 0:
                neighbors.append((cx - 1, cy))
        else: # cube
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < width and 0 <= ny < depth:
                    if not maze_data.cells[ny][nx][0]:
                        neighbors.append((nx, ny))
                        
        for nx, ny in neighbors:
            if (nx, ny) not in distances:
                distances[(nx, ny)] = d + 1
                queue.append((nx, ny, d + 1))
                
    return distances

def _apply_vertex_painting_on_obj(obj, props, maze_data):
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
            
    # Identify dead ends
    dead_ends = set()
    if mode == 'blend':
        if maze_data.grid_type == 'polar':
            rings = maze_data.polar_rings
            ring_sectors = maze_data.ring_sectors
            for r in range(rings):
                for theta in range(ring_sectors[r]):
                    Nr = ring_sectors[r]
                    accessible_count = 0
                    if r >= 1 and not maze_data.cells[r][theta][0]:
                        accessible_count += 1
                    if r >= 1 and not maze_data.cells[r][(theta - 1) % Nr][0]:
                        accessible_count += 1
                    if r > 0 and not maze_data.cells[r][theta][1]:
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
                            if not maze_data.cells[r + 1][theta][1]:
                                accessible_count += 1
                        elif Nr == 1:
                            for t in range(N_out):
                                if not maze_data.cells[r + 1][t][1]:
                                    accessible_count += 1
                        else:
                            if not maze_data.cells[r + 1][2 * theta][1]:
                                accessible_count += 1
                            if not maze_data.cells[r + 1][2 * theta + 1][1]:
                                accessible_count += 1
                    if accessible_count == 1:
                        # Exclude entrance and exits
                        is_ent_or_exit = False
                        if maze_data.entrance and (r, theta) == maze_data.entrance[0:2]:
                            is_ent_or_exit = True
                        if maze_data.exits:
                            for ex_r, ex_theta, _ in maze_data.exits:
                                if (r, theta) == (ex_r, ex_theta):
                                    is_ent_or_exit = True
                        if not is_ent_or_exit and r > 0:
                            dead_ends.add((r, theta))
        else:
            for cy in range(maze_data.depth):
                for cx in range(maze_data.width):
                    if wall_mode == 'thin':
                        c = maze_data.cells[cy][cx]
                        if sum(c[:4]) == 3:
                            dead_ends.add((cx, cy))
                    else: # cube
                        if not maze_data.cells[cy][cx][0]:
                            open_neighbors = 0
                            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                                nx, ny = cx + dx, cy + dy
                                if 0 <= nx < maze_data.width and 0 <= ny < maze_data.depth:
                                    if not maze_data.cells[ny][nx][0]:
                                        open_neighbors += 1
                            if open_neighbors == 1:
                                dead_ends.add((cx, cy))

    # Pre-calculate guide path cell set for faster lookup in path mode
    guide_cells = set(maze_data.guide_path) if maze_data.guide_path else set()

    for face in bm.faces:
        for loop in face.loops:
            co = loop.vert.co
            px, py, pz = co.x, co.y, co.z
            h_rel = (pz - z_min) / z_range
            
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
                a_val = 1.0 if (cx, cy) in dead_ends else 0.0
                
                r_col = r_val * intensity
                g_col = g_val * intensity
                b_col = b_val * intensity
                a_col = a_val * intensity
                
            elif mode == 'path':
                if not guide_cells:
                    r_col, g_col, b_col, a_col = 1.0, 1.0, 1.0, 1.0
                else:
                    min_d = float('inf')
                    for coord in guide_cells:
                        if maze_data.grid_type == 'polar':
                            gr, gtheta = coord
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
                            gcx, gcy = coord
                            gcx_world = gcx * ts + ts / 2
                            gcy_world = gcy * ts + ts / 2
                        d = math.sqrt((px - gcx_world)**2 + (py - gcy_world)**2)
                        if d < min_d:
                            min_d = d
                    
                    radius = 0.75 * ts
                    f_path = max(0.0, 1.0 - min_d / radius)
                    r_col = 0.0
                    g_col = f_path * intensity
                    b_col = 0.0
                    
            elif mode == 'distance':
                d_val = distances.get((cx, cy), 0)
                norm_d = d_val / max_d
                val = norm_d * intensity
                r_col, g_col, b_col, a_col = val, val, val, 1.0
                
            loop[color_layer] = (r_col, g_col, b_col, a_col)
            
    bm.normal_update()
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()

def _spawn_decorations(props, maze_data, context, parent_collection):
    has_props = (props.prop_torch_mesh or props.prop_chest_mesh or props.prop_door_mesh)
    if not has_props:
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
    rng = random.Random(props.seed + 1000 if props.seed else None)
 
    def place_prop(src_obj, pos, rot_z):
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
        if props.prop_torch_mesh:
            torch_src = props.prop_torch_mesh
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
        if props.prop_chest_mesh:
            chest_src = props.prop_chest_mesh
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
        if props.prop_door_mesh:
            door_src = props.prop_door_mesh
            
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
    if props.prop_torch_mesh:
        torch_src = props.prop_torch_mesh
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
    if props.prop_chest_mesh:
        chest_src = props.prop_chest_mesh
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
    if props.prop_door_mesh:
        door_src = props.prop_door_mesh
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

            elif side == 'W':
                pos = (ex * ts, ey * ts + ts/2, 0.0)
                place_prop(door_src, pos, -math.pi / 2)

def _add_polar_center_fan(bm, uv_layer, ts, z_base, is_roof=False, flip_normal=False):
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
        
    bm.verts.ensure_lookup_table()
    
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
        
        bm.verts.ensure_lookup_table()
        
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
    subdivs = 8
    alpha_total = phi_end - phi_start
    
    R_a = radius - wt / 2
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
        
    bm.verts.ensure_lookup_table()
    
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
        
    bm.verts.ensure_lookup_table()
    
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
    ux = math.cos(phi)
    uy = math.sin(phi)
    
    v_in_bot = bm.verts.new((r_in * ux, r_in * uy, z_base))
    v_out_bot = bm.verts.new((r_out * ux, r_out * uy, z_base))
    v_in_top = bm.verts.new((r_in * ux, r_in * uy, z_base + h))
    v_out_top = bm.verts.new((r_out * ux, r_out * uy, z_base + h))
    
    bm.verts.ensure_lookup_table()
    
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
    
    bm.verts.ensure_lookup_table()
    
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

def _add_mesh_polar_center(bm, src_mesh, mat_offset, uv_layer, final_materials_list, ts, z_off, centered, temp_mesh=None):
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

    _merge_bmesh_geometries(temp_bm, bm)
    temp_bm.free()

def _add_mesh_polar_bend_with_matrix(bm, src_mesh, mat_combined, uv_layer, final_materials_list, r, theta, Nr, ts, z_off, scale_angular=True, cuts=4, reverse_faces=True, temp_mesh=None):
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
    temp_bm = bmesh.new()
    temp_bm.from_mesh(src_mesh)

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
        
        r_local = r_mid + y_rel
        theta_local = theta_mid + (x_rel * scale_x / r_mid) if r_mid > 0 else theta_mid
        
        v.co.x = r_local * math.cos(theta_local)
        v.co.y = r_local * math.sin(theta_local)
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


def _add_mesh_polar_bend(bm, src_mesh, mat_offset, uv_layer, final_materials_list, r, theta, Nr, ts, z_off, centered, cuts=4, temp_mesh=None):
    cent = Matrix.Translation(Vector((-ts / 2, -ts / 2, 0))) if not centered else Matrix.Identity(4)
    mat_combined = mat_offset @ cent
    _add_mesh_polar_bend_with_matrix(bm, src_mesh, mat_combined, uv_layer, final_materials_list, r, theta, Nr, ts, z_off, scale_angular=True, cuts=cuts, reverse_faces=True, temp_mesh=temp_mesh)


def _add_wall_polar_bend(bm, src_mesh, mat_wall_offset, uv_layer, final_materials_list, wall_type, r, theta, Nr, ts, z_off, centered, cuts=4, flip_out=False, reverse_faces=True, temp_mesh=None):
    cent = Matrix.Translation(Vector((-ts / 2, -ts / 2, 0))) if not centered else Matrix.Identity(4)
    alpha_r = 2 * math.pi / Nr
    theta_mid = (theta + 0.5) * alpha_r
    r_mid = r * ts

    # Lift the wall by ts/2 to correct the Z-sinking caused by rotation
    z_lifted = z_off + ts / 2

    if wall_type in ('CW', 'CCW'):
        phi = theta_mid + (alpha_r / 2) if wall_type == 'CW' else theta_mid - (alpha_r / 2)
        mat_base = Matrix.Translation(Vector((r_mid * math.cos(phi), r_mid * math.sin(phi), z_lifted))) @ Matrix.Rotation(phi, 4, 'Z')
        # Stand tile upright with Rotation(X): original X stays radial, original Y becomes Z height.
        if wall_type == 'CW':
            mat_local = Matrix.Rotation(math.radians(90), 4, 'X')
        else:  # CCW
            mat_local = Matrix.Rotation(math.radians(-90), 4, 'X')
        mat_combined = mat_base @ mat_wall_offset @ mat_local @ cent
        _add_mesh_at(bm, src_mesh, mat_combined, uv_layer, final_materials_list, temp_mesh=temp_mesh)
    else:
        # IN wall: placed at inner edge (y=-ts/2), face should point outward (+Y/+radial)
        # OUT wall: placed at outer edge (y=+ts/2), face should point inward (-Y/-radial)
        if wall_type == 'IN':
            rot_angle = 90 if r == 1 else -90
            mat_place = Matrix.Translation(Vector((0, -ts/2, 0))) @ Matrix.Rotation(math.radians(rot_angle), 4, 'X')
        elif wall_type == 'OUT':
            out_rot = -90 if flip_out else 90
            mat_place = Matrix.Translation(Vector((0, ts/2, 0))) @ Matrix.Rotation(math.radians(out_rot), 4, 'X')
        else:
            mat_place = Matrix.Identity(4)
        mat_combined = mat_place @ mat_wall_offset @ cent
        _add_mesh_polar_bend_with_matrix(bm, src_mesh, mat_combined, uv_layer, final_materials_list, r, theta, Nr, ts, z_lifted, scale_angular=True, cuts=cuts, reverse_faces=reverse_faces, temp_mesh=temp_mesh)


def _add_mesh_polar_trapezoid_with_matrix(bm, src_mesh, mat_combined, uv_layer, final_materials_list, r, theta, Nr, ts, z_off, scale_angular=True, reverse_faces=True, temp_mesh=None):
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
    temp_bm = bmesh.new()
    temp_bm.from_mesh(src_mesh)

    bmesh.ops.transform(temp_bm, matrix=mat_combined, verts=temp_bm.verts)

    r_mid = r * ts
    alpha_r = 2 * math.pi / Nr
    theta_mid = (theta + 0.5) * alpha_r

    for v in temp_bm.verts:
        co = v.co
        x_rel = co.x
        y_rel = co.y
        
        # Scale X (width/angular direction) linearly based on Y (radial distance)
        scale_x = ((r_mid + y_rel) * alpha_r) / ts if (r_mid > 0 and scale_angular) else 1.0
        x_scaled = x_rel * scale_x
        
        # Rotate and translate in 3D space:
        cos_t = math.cos(theta_mid)
        sin_t = math.sin(theta_mid)
        v.co.x = (r_mid + y_rel) * cos_t - x_scaled * sin_t
        v.co.y = (r_mid + y_rel) * sin_t + x_scaled * cos_t
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


def _add_mesh_polar_trapezoid(bm, src_mesh, mat_offset, uv_layer, final_materials_list, r, theta, Nr, ts, z_off, centered, temp_mesh=None):
    cent = Matrix.Translation(Vector((-ts / 2, -ts / 2, 0))) if not centered else Matrix.Identity(4)
    mat_combined = mat_offset @ cent
    _add_mesh_polar_trapezoid_with_matrix(bm, src_mesh, mat_combined, uv_layer, final_materials_list, r, theta, Nr, ts, z_off, scale_angular=True, reverse_faces=True, temp_mesh=temp_mesh)


def _add_wall_polar_trapezoid(bm, src_mesh, mat_wall_offset, uv_layer, final_materials_list, wall_type, r, theta, Nr, ts, z_off, centered, flip_out=False, reverse_faces=True, temp_mesh=None):
    cent = Matrix.Translation(Vector((-ts / 2, -ts / 2, 0))) if not centered else Matrix.Identity(4)
    alpha_r = 2 * math.pi / Nr
    theta_mid = (theta + 0.5) * alpha_r
    r_mid = r * ts

    # Lift the wall by ts/2 to correct the Z-sinking caused by rotation
    z_lifted = z_off + ts / 2

    if wall_type in ('CW', 'CCW'):
        phi = theta_mid + (alpha_r / 2) if wall_type == 'CW' else theta_mid - (alpha_r / 2)
        mat_base = Matrix.Translation(Vector((r_mid * math.cos(phi), r_mid * math.sin(phi), z_lifted))) @ Matrix.Rotation(phi, 4, 'Z')
        if wall_type == 'CW':
            mat_local = Matrix.Rotation(math.radians(90), 4, 'X')
        else:  # CCW
            mat_local = Matrix.Rotation(math.radians(-90), 4, 'X')
        mat_combined = mat_base @ mat_wall_offset @ mat_local @ cent
        _add_mesh_at(bm, src_mesh, mat_combined, uv_layer, final_materials_list, temp_mesh=temp_mesh)
    else:
        if wall_type == 'IN':
            rot_angle = 90 if r == 1 else -90
            mat_place = Matrix.Translation(Vector((0, -ts/2, 0))) @ Matrix.Rotation(math.radians(rot_angle), 4, 'X')
        elif wall_type == 'OUT':
            out_rot = -90 if flip_out else 90
            mat_place = Matrix.Translation(Vector((0, ts/2, 0))) @ Matrix.Rotation(math.radians(out_rot), 4, 'X')
        else:
            mat_place = Matrix.Identity(4)
        mat_combined = mat_place @ mat_wall_offset @ cent
        _add_mesh_polar_trapezoid_with_matrix(bm, src_mesh, mat_combined, uv_layer, final_materials_list, r, theta, Nr, ts, z_lifted, scale_angular=True, reverse_faces=reverse_faces, temp_mesh=temp_mesh)

def _build_polar_maze_objects(props, maze_data, context, collection=None, force_simple=False, name_suffix=""):

    ts = props.tile_size
    tiled = props.wall_height_tiled
    tiles_high = props.wall_height_tiles if tiled else 1
    if tiled:
        wh = ts * tiles_high
    else:
        wh = props.wall_height
    seg_h = ts if tiled else wh
    wt = props.tile_size if props.wall_mode == 'cube' else props.wall_thickness
    
    if force_simple:
        custom_floor = None
        custom_wall_north = None
        custom_wall_south = None
        custom_wall_east = None
        custom_wall_west = None
        custom_roof = None
        centered = props.tiles_centered
    else:
        custom_floor = props.custom_floor_mesh
        custom_wall_north = props.custom_wall_north
        custom_wall_south = props.custom_wall_south
        custom_wall_east = props.custom_wall_east
        custom_wall_west = props.custom_wall_west
        custom_roof = props.custom_roof_mesh
        centered = props.tiles_centered

    mat_floor_offset = _get_offset_matrix(props.floor_translate, props.floor_rotate, props.floor_scale)
    mat_wall_offset = _get_offset_matrix(props.wall_translate, props.wall_rotate, props.wall_scale)

    wall_meshes_list = []
    if not force_simple and props.custom_wall_collection:
        wall_meshes_list = [obj.data for obj in props.custom_wall_collection.objects if obj.type == 'MESH']
    floor_meshes_list = []
    if not force_simple and props.custom_floor_collection:
        floor_meshes_list = [obj.data for obj in props.custom_floor_collection.objects if obj.type == 'MESH']
    roof_meshes_list = []
    if not force_simple and props.custom_roof_collection:
        roof_meshes_list = [obj.data for obj in props.custom_roof_collection.objects if obj.type == 'MESH']

    if collection is None:
        col = bpy.data.collections.new("FireMaze")
        context.scene.collection.children.link(col)
    else:
        col = collection
        if col.name not in context.scene.collection.children:
            context.scene.collection.children.link(col)

    materials = _ensure_materials()
    created_objects = []

    rings = maze_data.polar_rings
    ring_sectors = maze_data.ring_sectors
    alignment = props.polar_custom_alignment

    # 1. Floor
    bm_floor = bmesh.new()
    uv_floor = bm_floor.loops.layers.uv.new("UVMap")
    floor_materials = [materials["floor"]]

    for r in range(rings):
        Nr = ring_sectors[r]
        alpha_r = 2 * math.pi / Nr
        for theta in range(Nr):
            if props.wall_mode == 'cube' and maze_data.cells[r][theta][0]:
                continue
            if len(maze_data.cells[r][theta]) >= 8:
                floor_idx = maze_data.cells[r][theta][4]
            else:
                floor_idx = maze_data.cells[r][theta][4] if len(maze_data.cells[r][theta]) > 4 else -1

            # Default uninitialized index (-1) to 0 when a collection is available so
            # the custom mesh is used from the very first generation (no shift-click needed).
            if floor_idx < 0 and floor_meshes_list:
                floor_idx = 0

            src_floor = None
            if floor_meshes_list and isinstance(floor_idx, int) and 0 <= floor_idx < len(floor_meshes_list):
                src_floor = floor_meshes_list[floor_idx]
            elif custom_floor:
                src_floor = custom_floor

            if r == 0:
                if src_floor and alignment != 'procedural':
                    _add_mesh_polar_center(bm_floor, src_floor, mat_floor_offset, uv_floor, floor_materials, ts, 0.0, centered)
                else:
                    _add_polar_center_fan(bm_floor, uv_floor, ts, 0.0, is_roof=False, flip_normal=False)
            elif src_floor and alignment != 'procedural':
                if alignment == 'trapezoid':
                    _add_mesh_polar_trapezoid(bm_floor, src_floor, mat_floor_offset, uv_floor, floor_materials, r, theta, Nr, ts, 0.0, centered)
                elif alignment == 'bend':
                    N_max = ring_sectors[-1]
                    ratio = N_max // Nr if Nr > 0 else 1
                    cuts = max(1, ratio * 8 - 1)
                    _add_mesh_polar_bend(bm_floor, src_floor, mat_floor_offset, uv_floor, floor_materials, r, theta, Nr, ts, 0.0, centered, cuts=cuts)
            else:
                _add_polar_floor_wedge(bm_floor, uv_floor, r, theta, Nr, ts, 0.0, is_roof=False)

    floor_obj = _create_object_from_bm(bm_floor, f"FireMaze_Floor{name_suffix}", col, None)
    for mat in floor_materials:
        floor_obj.data.materials.append(mat)
    created_objects.append(floor_obj)

    # 2. Walls
    bm_wall = bmesh.new()
    uv_wall = bm_wall.loops.layers.uv.new("UVMap")
    wall_materials = [materials["wall"]]


    if props.wall_mode == 'cube':
        for level in range(tiles_high):
            z_off = level * seg_h
            
            for r in range(rings):
                Nr = ring_sectors[r]
                alpha_r = 2 * math.pi / Nr
                
                for theta in range(Nr):
                    is_wall = maze_data.cells[r][theta][0]
                    
                    if is_wall:
                        if props.cube_mode_pillar and (wall_meshes_list or custom_wall_north or custom_wall_east):
                            src_mesh = None
                            wall_idx = maze_data.cells[r][theta][2] if len(maze_data.cells[r][theta]) > 2 else -1
                            if wall_meshes_list:
                                if isinstance(wall_idx, int) and 0 <= wall_idx < len(wall_meshes_list):
                                    src_mesh = wall_meshes_list[wall_idx]
                                else:
                                    src_mesh = random.choice(wall_meshes_list)
                            else:
                                src_mesh = custom_wall_north if custom_wall_north else custom_wall_east
                                
                            if src_mesh:
                                if alignment == 'trapezoid':
                                    _add_mesh_polar_trapezoid(bm_wall, src_mesh, mat_wall_offset, uv_wall, wall_materials, r, theta, Nr, ts, z_off, centered)
                                elif alignment == 'bend':
                                    N_max = ring_sectors[-1]
                                    ratio = N_max // Nr if Nr > 0 else 1
                                    cuts = max(1, ratio * 8 - 1)
                                    _add_mesh_polar_bend(bm_wall, src_mesh, mat_wall_offset, uv_wall, wall_materials, r, theta, Nr, ts, z_off, centered, cuts=cuts)
                        elif r == rings - 1:
                            src_out_wall = None
                            if len(maze_data.cells[r][theta]) >= 8:
                                out_idx = maze_data.cells[r][theta][6]
                            else:
                                out_idx = maze_data.cells[r][theta][3] if len(maze_data.cells[r][theta]) > 3 else -1
                            
                            if wall_meshes_list and isinstance(out_idx, int) and 0 <= out_idx < len(wall_meshes_list):
                                src_out_wall = wall_meshes_list[out_idx]
                            elif custom_wall_north:
                                src_out_wall = custom_wall_north
                            elif custom_wall_east:
                                src_out_wall = custom_wall_east
                                
                            if src_out_wall and alignment != 'procedural':
                                if alignment == 'trapezoid':
                                    _add_wall_polar_trapezoid(bm_wall, src_out_wall, mat_wall_offset, uv_wall, wall_materials, 'OUT', r, theta, Nr, ts, z_off, centered, flip_out=True)
                                elif alignment == 'bend':
                                    N_max = ring_sectors[-1]
                                    ratio = N_max // Nr if Nr > 0 else 1
                                    cuts = max(1, ratio * 8 - 1)
                                    _add_wall_polar_bend(bm_wall, src_out_wall, mat_wall_offset, uv_wall, wall_materials, 'OUT', r, theta, Nr, ts, z_off, centered, cuts=cuts, flip_out=True)
                            else:
                                radius = (r + 0.5) * ts
                                phi_start = theta * alpha_r
                                phi_end = (theta + 1) * alpha_r
                                _add_circular_wall_flat(bm_wall, uv_wall, radius, phi_start, phi_end, ts, seg_h, z_off, facing_outward=True)
                        continue
                    
                    if props.cube_mode_pillar and (wall_meshes_list or custom_wall_north or custom_wall_east) and not force_simple:
                        continue
                    
                    if len(maze_data.cells[r][theta]) >= 8:
                        cw_idx = maze_data.cells[r][theta][2]
                        ccw_idx = maze_data.cells[r][theta][3]
                        in_idx = maze_data.cells[r][theta][7] if len(maze_data.cells[r][theta]) > 7 else -1
                        out_idx = maze_data.cells[r][theta][8] if len(maze_data.cells[r][theta]) > 8 else -1
                    else:
                        cw_idx = maze_data.cells[r][theta][2] if len(maze_data.cells[r][theta]) > 2 else -1
                        ccw_idx = cw_idx
                        in_idx = maze_data.cells[r][theta][3] if len(maze_data.cells[r][theta]) > 3 else -1
                        out_idx = in_idx
                    
                    src_cw_wall = None
                    if wall_meshes_list and isinstance(cw_idx, int) and 0 <= cw_idx < len(wall_meshes_list):
                        src_cw_wall = wall_meshes_list[cw_idx]
                    elif custom_wall_east:
                        src_cw_wall = custom_wall_east
                    elif custom_wall_north:
                        src_cw_wall = custom_wall_north

                    src_ccw_wall = None
                    if wall_meshes_list and isinstance(ccw_idx, int) and 0 <= ccw_idx < len(wall_meshes_list):
                        src_ccw_wall = wall_meshes_list[ccw_idx]
                    elif custom_wall_east:
                        src_ccw_wall = custom_wall_east
                    elif custom_wall_north:
                        src_ccw_wall = custom_wall_north
                        
                    src_in_wall = None
                    if wall_meshes_list and isinstance(in_idx, int) and 0 <= in_idx < len(wall_meshes_list):
                        src_in_wall = wall_meshes_list[in_idx]
                    elif custom_wall_north:
                        src_in_wall = custom_wall_north
                    elif custom_wall_east:
                        src_in_wall = custom_wall_east

                    src_out_wall = None
                    if wall_meshes_list and isinstance(out_idx, int) and 0 <= out_idx < len(wall_meshes_list):
                        src_out_wall = wall_meshes_list[out_idx]
                    elif custom_wall_north:
                        src_out_wall = custom_wall_north
                    elif custom_wall_east:
                        src_out_wall = custom_wall_east

                    # 1. Clockwise boundary
                    cw_neighbor_is_wall = maze_data.cells[r][(theta + 1) % Nr][0]
                    if cw_neighbor_is_wall:
                        if src_cw_wall and alignment != 'procedural':
                            if alignment == 'trapezoid':
                                _add_wall_polar_trapezoid(bm_wall, src_cw_wall, mat_wall_offset, uv_wall, wall_materials, 'CW', r, theta, Nr, ts, z_off, centered)
                            elif alignment == 'bend':
                                N_max = ring_sectors[-1]
                                ratio = N_max // Nr if Nr > 0 else 1
                                cuts = max(1, ratio * 8 - 1)
                                _add_wall_polar_bend(bm_wall, src_cw_wall, mat_wall_offset, uv_wall, wall_materials, 'CW', r, theta, Nr, ts, z_off, centered, cuts=cuts)
                        else:
                            phi = (theta + 1) * alpha_r
                            r_in = (r - 0.5) * ts
                            r_out = (r + 0.5) * ts
                            _add_radial_wall_flat(bm_wall, uv_wall, phi, r_in, r_out, ts, seg_h, z_off, facing_clockwise=True)

                    # 2. Counter-clockwise boundary
                    ccw_neighbor_is_wall = maze_data.cells[r][(theta - 1) % Nr][0]
                    if ccw_neighbor_is_wall:
                        if src_ccw_wall and alignment != 'procedural':
                            if alignment == 'trapezoid':
                                _add_wall_polar_trapezoid(bm_wall, src_ccw_wall, mat_wall_offset, uv_wall, wall_materials, 'CCW', r, theta, Nr, ts, z_off, centered)
                            elif alignment == 'bend':
                                N_max = ring_sectors[-1]
                                ratio = N_max // Nr if Nr > 0 else 1
                                cuts = max(1, ratio * 8 - 1)
                                _add_wall_polar_bend(bm_wall, src_ccw_wall, mat_wall_offset, uv_wall, wall_materials, 'CCW', r, theta, Nr, ts, z_off, centered, cuts=cuts)
                        else:
                            phi = theta * alpha_r
                            r_in = (r - 0.5) * ts
                            r_out = (r + 0.5) * ts
                            _add_radial_wall_flat(bm_wall, uv_wall, phi, r_in, r_out, ts, seg_h, z_off, facing_clockwise=False)

                    # 3. Inward boundary
                    if r > 0:
                        N_in = ring_sectors[r - 1]
                        theta_in = 0 if N_in == 1 else (theta if N_in == Nr else theta // 2)
                        in_neighbor_is_wall = maze_data.cells[r - 1][theta_in][0]
                        if in_neighbor_is_wall:
                            if src_in_wall and alignment != 'procedural':
                                if alignment == 'trapezoid':
                                    _add_wall_polar_trapezoid(bm_wall, src_in_wall, mat_wall_offset, uv_wall, wall_materials, 'IN', r, theta, Nr, ts, z_off, centered, reverse_faces=(r != 1))
                                elif alignment == 'bend':
                                    N_max = ring_sectors[-1]
                                    ratio = N_max // Nr if Nr > 0 else 1
                                    cuts = max(1, ratio * 8 - 1)
                                    _add_wall_polar_bend(bm_wall, src_in_wall, mat_wall_offset, uv_wall, wall_materials, 'IN', r, theta, Nr, ts, z_off, centered, cuts=cuts, reverse_faces=(r != 1))
                            else:
                                radius = (r - 0.5) * ts
                                phi_start = theta * alpha_r
                                phi_end = (theta + 1) * alpha_r
                                _add_circular_wall_flat(bm_wall, uv_wall, radius, phi_start, phi_end, ts, seg_h, z_off, facing_outward=True)

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
                            out_neighbor_is_wall = maze_data.cells[r + 1][ot][0]
                            if out_neighbor_is_wall:
                                phi_start_ot = ot * (2 * math.pi / N_out)
                                phi_end_ot = (ot + 1) * (2 * math.pi / N_out)
                                if src_out_wall and alignment != 'procedural':
                                    w_type = 'IN' if r == 0 else 'OUT'
                                    r_val = 1 if r == 0 else r
                                    if alignment == 'trapezoid':
                                        _add_wall_polar_trapezoid(bm_wall, src_out_wall, mat_wall_offset, uv_wall, wall_materials, w_type, r_val, ot, N_out, ts, z_off, centered, reverse_faces=True)
                                    elif alignment == 'bend':
                                        N_max = ring_sectors[-1]
                                        ratio = N_max // N_out if N_out > 0 else 1
                                        cuts = max(1, ratio * 8 - 1)
                                        _add_wall_polar_bend(bm_wall, src_out_wall, mat_wall_offset, uv_wall, wall_materials, w_type, r_val, ot, N_out, ts, z_off, centered, cuts=cuts, reverse_faces=True)
                                else:
                                    radius = (r + 0.5) * ts
                                    _add_circular_wall_flat(bm_wall, uv_wall, radius, phi_start_ot, phi_end_ot, ts, seg_h, z_off, facing_outward=False)
                    else:
                        is_entrance = (theta == 1)
                        is_exit = False
                        if maze_data.exits:
                            for ex_r, ex_theta, ex_side in maze_data.exits:
                                if ex_r == r and ex_theta == theta and ex_side == 'OUT':
                                    is_exit = True
                                    break
                                    
                        if not is_entrance and not is_exit:
                            if src_out_wall and alignment != 'procedural':
                                if alignment == 'trapezoid':
                                    _add_wall_polar_trapezoid(bm_wall, src_out_wall, mat_wall_offset, uv_wall, wall_materials, 'OUT', r, theta, Nr, ts, z_off, centered, flip_out=True)
                                elif alignment == 'bend':
                                    N_max = ring_sectors[-1]
                                    ratio = N_max // Nr if Nr > 0 else 1
                                    cuts = max(1, ratio * 8 - 1)
                                    _add_wall_polar_bend(bm_wall, src_out_wall, mat_wall_offset, uv_wall, wall_materials, 'OUT', r, theta, Nr, ts, z_off, centered, cuts=cuts, flip_out=True)
                            else:
                                radius = (r + 0.5) * ts
                                phi_start = theta * alpha_r
                                phi_end = (theta + 1) * alpha_r
                                _add_circular_wall_flat(bm_wall, uv_wall, radius, phi_start, phi_end, ts, seg_h, z_off, facing_outward=True)

    else:
        for level in range(tiles_high):
            z_off = level * seg_h
            
            for r in range(rings):
                Nr = ring_sectors[r]
                alpha_r = 2 * math.pi / Nr
                
                for theta in range(Nr):
                    cw_wall = maze_data.cells[r][theta][0]
                    in_wall = maze_data.cells[r][theta][1]
                    
                    cw_idx = maze_data.cells[r][theta][2] if len(maze_data.cells[r][theta]) > 2 else -1
                    in_idx = maze_data.cells[r][theta][3] if len(maze_data.cells[r][theta]) > 3 else -1
                    out_idx = maze_data.cells[r][theta][6] if len(maze_data.cells[r][theta]) > 6 else in_idx
                    
                    src_cw_wall = None
                    if wall_meshes_list and isinstance(cw_idx, int) and 0 <= cw_idx < len(wall_meshes_list):
                        src_cw_wall = wall_meshes_list[cw_idx]
                    elif custom_wall_east:
                        src_cw_wall = custom_wall_east
                    elif custom_wall_north:
                        src_cw_wall = custom_wall_north
                    
                    src_in_wall = None
                    if wall_meshes_list and isinstance(in_idx, int) and 0 <= in_idx < len(wall_meshes_list):
                        src_in_wall = wall_meshes_list[in_idx]
                    elif custom_wall_north:
                        src_in_wall = custom_wall_north
                    elif custom_wall_east:
                        src_in_wall = custom_wall_east

                    src_out_wall = None
                    if wall_meshes_list and isinstance(out_idx, int) and 0 <= out_idx < len(wall_meshes_list):
                        src_out_wall = wall_meshes_list[out_idx]
                    elif custom_wall_north:
                        src_out_wall = custom_wall_north
                    elif custom_wall_east:
                        src_out_wall = custom_wall_east
                    
                    if cw_wall and r >= 1:
                        if src_cw_wall and alignment != 'procedural':
                            if alignment == 'trapezoid':
                                _add_wall_polar_trapezoid(bm_wall, src_cw_wall, mat_wall_offset, uv_wall, wall_materials, 'CW', r, theta, Nr, ts, z_off, centered)
                            elif alignment == 'bend':
                                N_max = ring_sectors[-1]
                                ratio = N_max // Nr if Nr > 0 else 1
                                cuts = max(1, ratio * 8 - 1)
                                _add_wall_polar_bend(bm_wall, src_cw_wall, mat_wall_offset, uv_wall, wall_materials, 'CW', r, theta, Nr, ts, z_off, centered, cuts=cuts)
                        else:
                            phi = (theta + 1) * alpha_r
                            r_in = (r - 0.5) * ts
                            r_out = (r + 0.5) * ts
                            _add_radial_wall(bm_wall, uv_wall, phi, r_in, r_out, ts, seg_h, wt, z_off)

                    if in_wall and r >= 1:
                        if src_in_wall and alignment != 'procedural':
                            if alignment == 'trapezoid':
                                _add_wall_polar_trapezoid(bm_wall, src_in_wall, mat_wall_offset, uv_wall, wall_materials, 'IN', r, theta, Nr, ts, z_off, centered)
                            elif alignment == 'bend':
                                N_max = ring_sectors[-1]
                                ratio = N_max // Nr if Nr > 0 else 1
                                cuts = max(1, ratio * 8 - 1)
                                _add_wall_polar_bend(bm_wall, src_in_wall, mat_wall_offset, uv_wall, wall_materials, 'IN', r, theta, Nr, ts, z_off, centered, cuts=cuts)
                        else:
                            radius = (r - 0.5) * ts
                            phi_start = theta * alpha_r
                            phi_end = (theta + 1) * alpha_r
                            _add_circular_wall(bm_wall, uv_wall, radius, phi_start, phi_end, ts, seg_h, wt, z_off)

                    if r == rings - 1:
                        is_entrance = (theta == 0)
                        is_exit = False
                        if maze_data.exits:
                            for ex_r, ex_theta, ex_side in maze_data.exits:
                                if ex_r == r and ex_theta == theta and ex_side == 'OUT':
                                    is_exit = True
                                    break
                        if not is_entrance and not is_exit:
                            if src_out_wall and alignment != 'procedural':
                                if alignment == 'trapezoid':
                                    _add_wall_polar_trapezoid(bm_wall, src_out_wall, mat_wall_offset, uv_wall, wall_materials, 'OUT', r, theta, Nr, ts, z_off, centered, flip_out=True)
                                elif alignment == 'bend':
                                    N_max = ring_sectors[-1]
                                    ratio = N_max // Nr if Nr > 0 else 1
                                    cuts = max(1, ratio * 8 - 1)
                                    _add_wall_polar_bend(bm_wall, src_out_wall, mat_wall_offset, uv_wall, wall_materials, 'OUT', r, theta, Nr, ts, z_off, centered, cuts=cuts, flip_out=True)
                            else:
                                radius = (r + 0.5) * ts
                                phi_start = theta * alpha_r
                                phi_end = (theta + 1) * alpha_r
                                _add_circular_wall(bm_wall, uv_wall, radius, phi_start, phi_end, ts, seg_h, wt, z_off)

    bmesh.ops.remove_doubles(bm_wall, verts=bm_wall.verts, dist=0.001)
    wall_obj = _create_object_from_bm(bm_wall, f"FireMaze_Walls{name_suffix}", col, None)
    for mat in wall_materials:
        wall_obj.data.materials.append(mat)
    created_objects.append(wall_obj)

    # 3. Roof
    bm_roof = bmesh.new()
    uv_roof = bm_roof.loops.layers.uv.new("UVMap")
    roof_materials = [materials["roof"]]

    for r in range(rings):
        Nr = ring_sectors[r]
        alpha_r = 2 * math.pi / Nr
        for theta in range(Nr):
            is_wall = maze_data.cells[r][theta][0] if props.wall_mode == 'cube' else False
            if props.wall_mode == 'cube':
                if not is_wall:
                    continue
                if props.cube_mode_pillar and (wall_meshes_list or custom_wall_north or custom_wall_east):
                    continue
                    
            if len(maze_data.cells[r][theta]) >= 8:
                roof_idx = maze_data.cells[r][theta][5]
            else:
                roof_idx = maze_data.cells[r][theta][5] if len(maze_data.cells[r][theta]) > 5 else -1
            
            # Default uninitialized index (-1) to 0 when a collection is available so
            # the custom mesh is used from the very first generation (no shift-click needed).
            if roof_idx < 0 and roof_meshes_list:
                roof_idx = 0

            src_roof = None
            if roof_meshes_list and isinstance(roof_idx, int) and 0 <= roof_idx < len(roof_meshes_list):
                src_roof = roof_meshes_list[roof_idx]
            elif custom_roof:
                src_roof = custom_roof

            if r == 0:
                                if src_roof and alignment != 'procedural':
                                    _add_mesh_polar_center(bm_roof, src_roof, mat_floor_offset, uv_roof, roof_materials, ts, wh, centered)
                                else:
                                    _add_polar_center_fan(bm_roof, uv_roof, ts, wh, is_roof=True, flip_normal=(props.wall_mode == 'thin'))
            elif src_roof and alignment != 'procedural':
                if alignment == 'trapezoid':
                    _add_mesh_polar_trapezoid(bm_roof, src_roof, mat_floor_offset, uv_roof, roof_materials, r, theta, Nr, ts, wh, centered)
                elif alignment == 'bend':
                    N_max = ring_sectors[-1]
                    ratio = N_max // Nr if Nr > 0 else 1
                    cuts = max(1, ratio * 8 - 1)
                    _add_mesh_polar_bend(bm_roof, src_roof, mat_floor_offset, uv_roof, roof_materials, r, theta, Nr, ts, wh, centered, cuts=cuts)
            else:
                flip = props.wall_mode == 'thin'
                _add_polar_floor_wedge(bm_roof, uv_roof, r, theta, Nr, ts, wh, is_roof=True, flip_normal=flip)

    bmesh.ops.remove_doubles(bm_roof, verts=bm_roof.verts, dist=0.001)
    roof_obj = _create_object_from_bm(bm_roof, f"FireMaze_Roof{name_suffix}", col, None)
    for mat in roof_materials:
        roof_obj.data.materials.append(mat)
    created_objects.append(roof_obj)

    # Build guide path if requested
    if not force_simple:
        guide_obj = _build_guide_path(props, maze_data, col, materials)
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
        visual_meshes = [obj for obj in col.objects if obj.type == 'MESH']
        if props.optimize_coplanar:
            for obj in visual_meshes:
                _optimize_coplanar_on_obj(obj)
        if props.vertex_paint_enable:
            for obj in visual_meshes:
                _apply_vertex_painting_on_obj(obj, props, maze_data)
        if props.generate_lightmap:
            for obj in visual_meshes:
                _generate_lightmap_on_obj(obj, context, method=props.lightmap_method)
        _spawn_decorations(props, maze_data, context, col)

    return col

def build_maze_objects(props, maze_data, context, collection=None, force_simple=False, name_suffix=""):
    if maze_data.grid_type == 'polar':
        return _build_polar_maze_objects(props, maze_data, context, collection, force_simple, name_suffix)


    ts = props.tile_size
    tiled = props.wall_height_tiled
    tiles_high = props.wall_height_tiles if tiled else 1
    if tiled:
        wh = ts * tiles_high
    else:
        wh = props.wall_height
    seg_h = ts if tiled else wh
    wt = props.wall_thickness
    wall_mode = props.wall_mode

    if force_simple:
        custom_floor = None
        custom_wall_north = None
        custom_wall_south = None
        custom_wall_east = None
        custom_wall_west = None
        custom_roof = None
        centered = props.tiles_centered
    else:
        custom_floor = props.custom_floor_mesh
        custom_wall_north = props.custom_wall_north
        custom_wall_south = props.custom_wall_south
        custom_wall_east = props.custom_wall_east
        custom_wall_west = props.custom_wall_west
        custom_roof = props.custom_roof_mesh
        centered = props.tiles_centered

    # Compute offset matrices
    mat_floor_offset = _get_offset_matrix(props.floor_translate, props.floor_rotate, props.floor_scale)
    mat_wall_offset = _get_offset_matrix(props.wall_translate, props.wall_rotate, props.wall_scale)

    # Pick wall meshes randomly from custom_wall_collection if defined
    wall_meshes_list = []
    if not force_simple and props.custom_wall_collection:
        wall_meshes_list = [obj.data for obj in props.custom_wall_collection.objects if obj.type == 'MESH']

    # Pick floor meshes randomly from custom_floor_collection if defined
    floor_meshes_list = []
    if not force_simple and props.custom_floor_collection:
        floor_meshes_list = [obj.data for obj in props.custom_floor_collection.objects if obj.type == 'MESH']

    # Pick roof meshes randomly from custom_roof_collection if defined
    roof_meshes_list = []
    if not force_simple and props.custom_roof_collection:
        roof_meshes_list = [obj.data for obj in props.custom_roof_collection.objects if obj.type == 'MESH']

    if collection is None:
        col = bpy.data.collections.new("FireMaze")
        context.scene.collection.children.link(col)
    else:
        col = collection
        if col.name not in context.scene.collection.children:
            context.scene.collection.children.link(col)

    materials = _ensure_materials()

    created_objects = []

    if wall_mode == 'cube':
        # Floor
        bm_floor = bmesh.new()
        uv_floor = bm_floor.loops.layers.uv.new("UVMap")
        floor_materials = [materials["floor"]]
        for y in range(maze_data.depth):
            for x in range(maze_data.width):
                is_wall = maze_data.cells[y][x][0]
                if not is_wall:
                    floor_idx = maze_data.cells[y][x][5] if len(maze_data.cells[y][x]) > 5 else -1
                    if floor_meshes_list and isinstance(floor_idx, int) and 0 <= floor_idx < len(floor_meshes_list):
                        off = ts / 2 if centered else 0
                        mat_base = Matrix.Translation(Vector((x * ts + off, y * ts + off, 0)))
                        mat = mat_base @ mat_floor_offset
                        _add_mesh_at(bm_floor, floor_meshes_list[floor_idx], mat, uv_floor, final_materials_list=floor_materials)
                    elif custom_floor:
                        off = ts / 2 if centered else 0
                        mat_base = Matrix.Translation(Vector((x * ts + off, y * ts + off, 0)))
                        mat = mat_base @ mat_floor_offset
                        _add_mesh_at(bm_floor, custom_floor, mat, uv_floor, final_materials_list=floor_materials)
                    else:
                        _add_floor_tile_transformed(bm_floor, uv_floor, x, y, ts, mat_floor_offset)
        floor_obj = _create_object_from_bm(bm_floor, f"FireMaze_Floor{name_suffix}", col, None)
        for mat in floor_materials:
            floor_obj.data.materials.append(mat)
        created_objects.append(floor_obj)

        # Walls
        bm_wall = bmesh.new()
        uv_wall = bm_wall.loops.layers.uv.new("UVMap")
        wall_materials = [materials["wall"]]
        cent = Matrix.Translation(Vector((-ts / 2, -ts / 2, 0))) if not centered else Matrix.Identity(4)
        for level in range(tiles_high):
            z_off = level * seg_h
            hw = z_off + seg_h / 2
            for y in range(maze_data.depth):
                for x in range(maze_data.width):
                    is_wall = maze_data.cells[y][x][0]
                    if is_wall:
                        cx, cy = x * ts, y * ts
                        wall_idx = maze_data.cells[y][x][1] if len(maze_data.cells[y][x]) > 1 else -1
                        
                        if props.cube_mode_pillar and wall_meshes_list:
                            # Instanced Pillar Mode
                            src_mesh = None
                            if isinstance(wall_idx, int) and 0 <= wall_idx < len(wall_meshes_list):
                                src_mesh = wall_meshes_list[wall_idx]
                            else:
                                src_mesh = random.choice(wall_meshes_list)
                            
                            mat_base = Matrix.Translation(Vector((cx + ts / 2, cy + ts / 2, z_off))) @ cent
                            mat = mat_base @ mat_wall_offset
                            _add_mesh_at(bm_wall, src_mesh, mat, uv_wall, final_materials_list=wall_materials)
                        else:
                            # Face Assembled Mode
                            def place_wall_face(direction, offset_rot, custom_mesh_fallback, f_idx):
                                if wall_meshes_list:
                                    src_mesh = None
                                    if isinstance(f_idx, int) and 0 <= f_idx < len(wall_meshes_list):
                                        src_mesh = wall_meshes_list[f_idx]
                                    else:
                                        src_mesh = random.choice(wall_meshes_list)
                                    mat_base = Matrix.Translation(Vector((cx + ts / 2, cy + ts / 2, hw))) @ offset_rot @ cent
                                    mat = mat_base @ mat_wall_offset
                                    _add_mesh_at(bm_wall, src_mesh, mat, uv_wall, final_materials_list=wall_materials)
                                elif custom_mesh_fallback:
                                    mat_base = Matrix.Translation(Vector((cx + ts / 2, cy + ts / 2, hw))) @ offset_rot @ cent
                                    mat = mat_base @ mat_wall_offset
                                    _add_mesh_at(bm_wall, custom_mesh_fallback, mat, uv_wall, final_materials_list=wall_materials)
                                else:
                                    _add_wall_face_transformed(bm_wall, uv_wall, cx, cy, ts, seg_h, direction, mat_wall_offset, z_base=z_off)

                            # +Y (north)
                            if y + 1 >= maze_data.depth or not maze_data.cells[y + 1][x][0]:
                                f_idx = maze_data.cells[y][x][1] if len(maze_data.cells[y][x]) > 1 else -1
                                place_wall_face('+Y', Matrix.Translation(Vector((0, ts/2, 0))) @ Matrix.Rotation(math.radians(-90), 4, 'X'), custom_wall_north, f_idx)
                            # -Y (south)
                            if y - 1 < 0 or not maze_data.cells[y - 1][x][0]:
                                f_idx = maze_data.cells[y][x][2] if len(maze_data.cells[y][x]) > 2 else -1
                                place_wall_face('-Y', Matrix.Translation(Vector((0, -ts/2, 0))) @ Matrix.Rotation(math.radians(90), 4, 'X'), custom_wall_south, f_idx)
                            # +X (east)
                            if x + 1 >= maze_data.width or not maze_data.cells[y][x + 1][0]:
                                f_idx = maze_data.cells[y][x][3] if len(maze_data.cells[y][x]) > 3 else -1
                                place_wall_face('+X', Matrix.Translation(Vector((ts/2, 0, 0))) @ Matrix.Rotation(math.radians(90), 4, 'Y'), custom_wall_east, f_idx)
                            # -X (west)
                            if x - 1 < 0 or not maze_data.cells[y][x - 1][0]:
                                f_idx = maze_data.cells[y][x][4] if len(maze_data.cells[y][x]) > 4 else -1
                                place_wall_face('-X', Matrix.Translation(Vector((-ts/2, 0, 0))) @ Matrix.Rotation(math.radians(-90), 4, 'Y'), custom_wall_west, f_idx)

        bmesh.ops.remove_doubles(bm_wall, verts=bm_wall.verts, dist=0.001)
        wall_obj = _create_object_from_bm(bm_wall, f"FireMaze_Walls{name_suffix}", col, None)
        for mat in wall_materials:
            wall_obj.data.materials.append(mat)
        created_objects.append(wall_obj)

        # Roof
        if not props.cube_mode_pillar or name_suffix in {"_EditHelper", "_Collider"}:
            bm_roof = bmesh.new()
            uv_roof = bm_roof.loops.layers.uv.new("UVMap")
            roof_materials = [materials["roof"]]
            for y in range(maze_data.depth):
                for x in range(maze_data.width):
                    is_wall = maze_data.cells[y][x][0]
                    if is_wall:
                        roof_idx = maze_data.cells[y][x][6] if len(maze_data.cells[y][x]) > 6 else -1
                        if roof_meshes_list and isinstance(roof_idx, int) and 0 <= roof_idx < len(roof_meshes_list):
                            off = ts / 2 if centered else 0
                            mat_base = Matrix.Translation(Vector((x * ts + off, y * ts + off, wh)))
                            mat = mat_base @ mat_floor_offset
                            _add_mesh_at(bm_roof, roof_meshes_list[roof_idx], mat, uv_roof, final_materials_list=roof_materials)
                        elif custom_roof:
                            off = ts / 2 if centered else 0
                            mat_base = Matrix.Translation(Vector((x * ts + off, y * ts + off, wh)))
                            mat = mat_base @ mat_floor_offset
                            _add_mesh_at(bm_roof, custom_roof, mat, uv_roof, final_materials_list=roof_materials)
                        else:
                            _add_cube_roof_face_transformed(bm_roof, uv_roof, x * ts + ts / 2, y * ts + ts / 2, ts, ts, wh, mat_floor_offset)
            if not custom_roof and not roof_meshes_list:
                bmesh.ops.remove_doubles(bm_roof, verts=bm_roof.verts, dist=0.001)
            roof_obj = _create_object_from_bm(bm_roof, f"FireMaze_Roof{name_suffix}", col, None)
            for mat in roof_materials:
                roof_obj.data.materials.append(mat)
            created_objects.append(roof_obj)


    else:
        # Thin wall mode
        segments = list(_get_wall_segments(maze_data))
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

        # Floor
        bm_floor = bmesh.new()
        uv_floor = bm_floor.loops.layers.uv.new("UVMap")
        floor_materials = [materials["floor"]]
        off = ts / 2 if centered else 0
        for y in range(maze_data.depth):
            for x in range(maze_data.width):
                if len(maze_data.cells[y][x]) > 8:
                    floor_idx = maze_data.cells[y][x][8] if len(maze_data.cells[y][x]) > 8 else -1
                else:
                    floor_idx = maze_data.cells[y][x][6] if len(maze_data.cells[y][x]) > 6 else -1
                if floor_idx == -2:
                    continue
                if floor_meshes_list and isinstance(floor_idx, int) and 0 <= floor_idx < len(floor_meshes_list):
                    mat_base = Matrix.Translation(Vector((x * ts + off, y * ts + off, 0)))
                    mat = mat_base @ mat_floor_offset
                    _add_mesh_at(bm_floor, floor_meshes_list[floor_idx], mat, uv_floor, final_materials_list=floor_materials)
                elif custom_floor:
                    mat_base = Matrix.Translation(Vector((x * ts + off, y * ts + off, 0)))
                    mat = mat_base @ mat_floor_offset
                    _add_mesh_at(bm_floor, custom_floor, mat, uv_floor, final_materials_list=floor_materials)
                else:
                    _add_floor_tile_transformed(bm_floor, uv_floor, x, y, ts, mat_floor_offset)
        floor_obj = _create_object_from_bm(bm_floor, f"FireMaze_Floor{name_suffix}", col, None)
        for mat in floor_materials:
            floor_obj.data.materials.append(mat)
        created_objects.append(floor_obj)

        # Walls and caps
        bm_wall = bmesh.new()
        uv_wall = bm_wall.loops.layers.uv.new("UVMap")
        bm_cap = bmesh.new()
        uv_cap = bm_cap.loops.layers.uv.new("UVMap")
        wall_materials = [materials["wall"]]
        cap_materials = [materials["end_cap"]]

        has_any_wall_custom = (custom_wall_north or custom_wall_south or custom_wall_east or custom_wall_west or wall_meshes_list)
        cent = Matrix.Translation(Vector((-ts / 2, -ts / 2, 0))) if not centered else Matrix.Identity(4)
        tw = wt / 2

        for level in range(tiles_high):
            z_off = level * seg_h
            hw = z_off + seg_h / 2

            for seg_type, a, b in segments:
                wall_idx = -1
                if seg_type == 'H':
                    if len(maze_data.cells[0][0]) > 8:
                        if b < maze_data.depth:
                            wall_idx = maze_data.cells[b][a][5] if len(maze_data.cells[b][a]) > 5 else -1
                        else:
                            wall_idx = maze_data.cells[b - 1][a][4] if len(maze_data.cells[b - 1][a]) > 4 else -1
                    else:
                        if b < maze_data.depth and a < maze_data.width:
                            wall_idx = maze_data.cells[b][a][4] if len(maze_data.cells[b][a]) > 4 else -1
                        else:
                            # Boundary wall fallback for 8-item layout
                            wall_idx = a % len(wall_meshes_list) if len(wall_meshes_list) > 0 else -1
                else:
                    if len(maze_data.cells[0][0]) > 8:
                        if a < maze_data.width:
                            wall_idx = maze_data.cells[b][a][7] if len(maze_data.cells[b][a]) > 7 else -1
                        else:
                            wall_idx = maze_data.cells[b][a - 1][6] if len(maze_data.cells[b][a - 1]) > 6 else -1
                    else:
                        if a < maze_data.width and b < maze_data.depth:
                            wall_idx = maze_data.cells[b][a][5] if len(maze_data.cells[b][a]) > 5 else -1
                        else:
                            # Boundary wall fallback for 8-item layout
                            wall_idx = b % len(wall_meshes_list) if len(wall_meshes_list) > 0 else -1

                if seg_type == 'H':
                    x0, x1 = a * ts, (a + 1) * ts
                    yc = b * ts
                    
                    def add_horizontal_face(direction, offset_rot, custom_mesh_fallback, y_offset, uvs_standard, local_pts):
                        if wall_meshes_list:
                            src_mesh = None
                            if isinstance(wall_idx, int) and 0 <= wall_idx < len(wall_meshes_list):
                                src_mesh = wall_meshes_list[wall_idx]
                            else:
                                src_mesh = random.choice(wall_meshes_list)
                            mat_base = Matrix.Translation(Vector((x0 + ts / 2, yc + y_offset, hw))) @ offset_rot @ cent
                            mat = mat_base @ mat_wall_offset
                            _add_mesh_at(bm_wall, src_mesh, mat, uv_wall, final_materials_list=wall_materials)
                        elif custom_mesh_fallback:
                            mat_base = Matrix.Translation(Vector((x0 + ts / 2, yc + y_offset, hw))) @ offset_rot @ cent
                            mat = mat_base @ mat_wall_offset
                            _add_mesh_at(bm_wall, custom_mesh_fallback, mat, uv_wall, final_materials_list=wall_materials)
                        else:
                            T = Matrix.Translation(Vector((x0 + ts/2, yc, hw))) @ mat_wall_offset
                            verts = [bm_wall.verts.new(T @ Vector(p)) for p in local_pts]
                            bm_wall.verts.ensure_lookup_table()
                            f = bm_wall.faces.new(verts)
                            for loop, uv in zip(f.loops, uvs_standard):
                                loop[uv_wall].uv = uv

                    # North face (+Y)
                    add_horizontal_face('+Y', Matrix.Rotation(math.radians(-90), 4, 'X'), custom_wall_north, tw, 
                                        [(0,0),(1,0),(1,1),(0,1)],
                                        [(ts/2, tw, -seg_h/2), (-ts/2, tw, -seg_h/2), (-ts/2, tw, seg_h/2), (ts/2, tw, seg_h/2)])
                    # South face (-Y)
                    add_horizontal_face('-Y', Matrix.Rotation(math.radians(90), 4, 'X'), custom_wall_south, -tw, 
                                        [(0,0),(1,0),(1,1),(0,1)],
                                        [(-ts/2, -tw, -seg_h/2), (ts/2, -tw, -seg_h/2), (ts/2, -tw, seg_h/2), (-ts/2, -tw, seg_h/2)])
                    
                    # West end-cap
                    if (a - 1, b) not in h_positions:
                        T = Matrix.Translation(Vector((x0 + ts/2, yc, hw))) @ mat_wall_offset
                        v_pts = [T @ Vector(p) for p in [(-ts/2, tw, -seg_h/2), (-ts/2, -tw, -seg_h/2), (-ts/2, -tw, seg_h/2), (-ts/2, tw, seg_h/2)]]
                        bm_cap.verts.ensure_lookup_table()
                        f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                        for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                            loop[uv_cap].uv = uv
                    # East end-cap
                    if (a + 1, b) not in h_positions:
                        T = Matrix.Translation(Vector((x0 + ts/2, yc, hw))) @ mat_wall_offset
                        v_pts = [T @ Vector(p) for p in [(ts/2, -tw, -seg_h/2), (ts/2, tw, -seg_h/2), (ts/2, tw, seg_h/2), (ts/2, -tw, seg_h/2)]]
                        bm_cap.verts.ensure_lookup_table()
                        f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                        for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                            loop[uv_cap].uv = uv

                else:
                    xc = a * ts
                    y0, y1 = b * ts, (b + 1) * ts
                    
                    def add_vertical_face(direction, offset_rot, custom_mesh_fallback, x_offset, uvs_standard, local_pts):
                        if wall_meshes_list:
                            src_mesh = None
                            if isinstance(wall_idx, int) and 0 <= wall_idx < len(wall_meshes_list):
                                src_mesh = wall_meshes_list[wall_idx]
                            else:
                                src_mesh = random.choice(wall_meshes_list)
                            mat_base = Matrix.Translation(Vector((xc + x_offset, y0 + ts / 2, hw))) @ offset_rot @ cent
                            mat = mat_base @ mat_wall_offset
                            _add_mesh_at(bm_wall, src_mesh, mat, uv_wall, final_materials_list=wall_materials, swap_uv=True)
                        elif custom_mesh_fallback:
                            mat_base = Matrix.Translation(Vector((xc + x_offset, y0 + ts / 2, hw))) @ offset_rot @ cent
                            mat = mat_base @ mat_wall_offset
                            _add_mesh_at(bm_wall, custom_mesh_fallback, mat, uv_wall, final_materials_list=wall_materials, swap_uv=True)
                        else:
                            T = Matrix.Translation(Vector((xc, y0 + ts/2, hw))) @ mat_wall_offset
                            verts = [bm_wall.verts.new(T @ Vector(p)) for p in local_pts]
                            bm_wall.verts.ensure_lookup_table()
                            f = bm_wall.faces.new(verts)
                            for loop, uv in zip(f.loops, uvs_standard):
                                loop[uv_wall].uv = uv

                    # East face (+X)
                    add_vertical_face('+X', Matrix.Rotation(math.radians(90), 4, 'Y'), custom_wall_east, tw, 
                                      [(0,0),(1,0),(1,1),(0,1)],
                                      [(tw, -ts/2, -seg_h/2), (tw, ts/2, -seg_h/2), (tw, ts/2, seg_h/2), (tw, -ts/2, seg_h/2)])
                    # West face (-X)
                    add_vertical_face('-X', Matrix.Rotation(math.radians(-90), 4, 'Y'), custom_wall_west, -tw, 
                                      [(1,0),(0,0),(0,1),(1,1)],
                                      [(-tw, ts/2, -seg_h/2), (-tw, -ts/2, -seg_h/2), (-tw, -ts/2, seg_h/2), (-tw, ts/2, seg_h/2)])

                    # South end-cap
                    if (a, b - 1) not in v_positions:
                        T = Matrix.Translation(Vector((xc, y0 + ts/2, hw))) @ mat_wall_offset
                        v_pts = [T @ Vector(p) for p in [(-tw, -ts/2, -seg_h/2), (tw, -ts/2, -seg_h/2), (tw, -ts/2, seg_h/2), (-tw, -ts/2, seg_h/2)]]
                        bm_cap.verts.ensure_lookup_table()
                        f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                        for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                            loop[uv_cap].uv = uv
                    # North end-cap
                    if (a, b + 1) not in v_positions:
                        T = Matrix.Translation(Vector((xc, y0 + ts/2, hw))) @ mat_wall_offset
                        v_pts = [T @ Vector(p) for p in [(tw, ts/2, -seg_h/2), (-tw, ts/2, -seg_h/2), (-tw, ts/2, seg_h/2), (tw, ts/2, seg_h/2)]]
                        bm_cap.verts.ensure_lookup_table()
                        f = bm_cap.faces.new([bm_cap.verts.new(p) for p in v_pts])
                        for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                            loop[uv_cap].uv = uv

        # Handle single wall object merge of caps
        if props.single_wall_object and bm_cap.verts:
            vert_map = {}
            for v in bm_cap.verts:
                new_v = bm_wall.verts.new(v.co)
                vert_map[v] = new_v
            bm_wall.verts.ensure_lookup_table()
            if materials["end_cap"] not in wall_materials:
                wall_materials.append(materials["end_cap"])
            cap_mat_idx = wall_materials.index(materials["end_cap"])
            
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
            wall_obj = _create_object_from_bm(bm_wall, f"FireMaze_Walls{name_suffix}", col, None)
            for mat in wall_materials:
                wall_obj.data.materials.append(mat)
            created_objects.append(wall_obj)
        else:
            wall_obj = _create_object_from_bm(bm_wall, f"FireMaze_Walls{name_suffix}", col, None)
            for mat in wall_materials:
                wall_obj.data.materials.append(mat)
            created_objects.append(wall_obj)
            if bm_cap.verts:
                cap_obj = _create_object_from_bm(bm_cap, f"FireMaze_WallEndCaps{name_suffix}", col, None)
                for mat in cap_materials:
                    cap_obj.data.materials.append(mat)
                created_objects.append(cap_obj)
            else:
                bm_cap.free()

        # Roof
        bm_roof = bmesh.new()
        uv_roof = bm_roof.loops.layers.uv.new("UVMap")
        roof_materials = [materials["roof"]]
        if custom_roof or roof_meshes_list:
            for seg_type, a, b in segments:
                if seg_type == 'H':
                    cx, cy = a * ts + ts / 2, b * ts
                    if len(maze_data.cells[0][0]) > 8:
                        target_y = min(b, maze_data.depth - 1)
                        roof_idx = maze_data.cells[target_y][a][9] if len(maze_data.cells[target_y][a]) > 9 else -1
                    else:
                        if b < maze_data.depth and a < maze_data.width:
                            roof_idx = maze_data.cells[b][a][7] if len(maze_data.cells[b][a]) > 7 else -1
                        else:
                            roof_idx = a % len(roof_meshes_list) if len(roof_meshes_list) > 0 else -1
                else:
                    cx, cy = a * ts, b * ts + ts / 2
                    if len(maze_data.cells[0][0]) > 8:
                        target_x = min(a, maze_data.width - 1)
                        roof_idx = maze_data.cells[b][target_x][9] if len(maze_data.cells[b][target_x]) > 9 else -1
                    else:
                        if a < maze_data.width and b < maze_data.depth:
                            roof_idx = maze_data.cells[b][a][7] if len(maze_data.cells[b][a]) > 7 else -1
                        else:
                            roof_idx = b % len(roof_meshes_list) if len(roof_meshes_list) > 0 else -1
                
                if roof_meshes_list and isinstance(roof_idx, int) and 0 <= roof_idx < len(roof_meshes_list):
                    mat_base = Matrix.Translation(Vector((cx, cy, wh)))
                    mat = mat_base @ mat_floor_offset
                    _add_mesh_at(bm_roof, roof_meshes_list[roof_idx], mat, uv_roof, final_materials_list=roof_materials)
                elif custom_roof:
                    mat_base = Matrix.Translation(Vector((cx, cy, wh)))
                    mat = mat_base @ mat_floor_offset
                    _add_mesh_at(bm_roof, custom_roof, mat, uv_roof, final_materials_list=roof_materials)
        else:
            filled = set()
            for seg_type, a, b in segments:
                if seg_type == 'H':
                    _add_horizontal_roof_face_transformed(bm_roof, uv_roof, a, b, ts, wh, wt, mat_floor_offset)
                else:
                    tsouth = (a, b) in h_endpoints
                    tnorth = (a, b + 1) in h_endpoints
                    _add_vertical_roof_face_transformed(bm_roof, uv_roof, a, b, ts, wh, wt, mat_floor_offset, trim_south=tsouth, trim_north=tnorth)
                    tw = wt / 2
                    xc = a * ts
                    if tsouth:
                        yc = b * ts
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
                                _add_vertical_roof_filler_transformed(bm_roof, uv_roof, xc, yc, wh, tw, y_lo - yc, y_hi - yc, hx0_rel, hx1_rel, mat_floor_offset)
                    if tnorth:
                        yc = (b + 1) * ts
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
                                _add_vertical_roof_filler_transformed(bm_roof, uv_roof, xc, yc, wh, tw, y_lo - yc, y_hi - yc, hx0_rel, hx1_rel, mat_floor_offset)

        if not custom_roof:
            bmesh.ops.remove_doubles(bm_roof, verts=bm_roof.verts, dist=0.001)
        roof_obj = _create_object_from_bm(bm_roof, f"FireMaze_Roof{name_suffix}", col, None)
        for mat in roof_materials:
            roof_obj.data.materials.append(mat)
        created_objects.append(roof_obj)

    # Build guide path if requested
    if not force_simple:
        guide_obj = _build_guide_path(props, maze_data, col, materials)
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
        visual_meshes = [obj for obj in col.objects if obj.type == 'MESH']
        
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
        _spawn_decorations(props, maze_data, context, col)

    return col

def _ensure_materials():
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
