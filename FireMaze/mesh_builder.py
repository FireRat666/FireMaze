import math
import random
import bpy
import bmesh
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
        uvs = [(0, 0), (0, 1), (1, 1), (0, 1)]

    verts = [bm.verts.new(T @ Vector(p)) for p in pts]
    bm.verts.ensure_lookup_table()
    face = bm.faces.new(verts)
    for loop, uv in zip(face.loops, uvs):
        loop[uv_layer].uv = uv

def _add_mesh_at(bm, src_mesh, matrix, uv_layer, final_materials_list=None, swap_uv=False):
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
    src_uv = temp_bm.loops.layers.uv.active
    if not src_uv and temp_bm.loops.layers.uv:
        src_uv = temp_bm.loops.layers.uv[0]

    vert_map = {}
    for v in temp_bm.verts:
        new_v = bm.verts.new(v.co)
        vert_map[v.index] = new_v
    bm.verts.ensure_lookup_table()

    for f in temp_bm.faces:
        new_verts = [vert_map[loop.vert.index] for loop in f.loops]
        new_face = bm.faces.new(new_verts)
        
        # Copy material index, mapped to the final combined list
        if final_materials_list is not None and material_map and f.material_index < len(material_map):
            new_face.material_index = material_map[f.material_index]
        else:
            if final_materials_list is not None:
                new_face.material_index = 0

        if src_uv and uv_layer:
            for src_loop, new_loop in zip(f.loops, new_face.loops):
                u, v = src_loop[src_uv].uv
                if swap_uv:
                    u, v = v, u
                new_loop[uv_layer].uv = (u, v)

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
    bm.to_mesh(mesh)
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

    for i, (x, y) in enumerate(maze_data.guide_path):
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
    bm.to_mesh(obj.data)
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

def build_maze_objects(props, maze_data, context, collection=None, force_simple=False, name_suffix=""):
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
    
    # Lightmap UV generation for visual mesh objects
    if not name_suffix and props.generate_lightmap:
        mesh_objs = [obj for obj in col.objects if obj.type == 'MESH']
        for obj in mesh_objs:
            _generate_lightmap_on_obj(obj, context, method=props.lightmap_method)

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
