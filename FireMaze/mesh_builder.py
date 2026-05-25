import math
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


def _add_wall_face(bm, uv_layer, cx, cy, ts, wh, direction):
    if direction == '+Y':
        pts = [(cx, cy + ts, 0), (cx, cy + ts, wh), (cx + ts, cy + ts, wh), (cx + ts, cy + ts, 0)]
        uvs = [(0, 0), (0, 1), (1, 1), (1, 0)]
    elif direction == '-Y':
        pts = [(cx, cy, 0), (cx + ts, cy, 0), (cx + ts, cy, wh), (cx, cy, wh)]
        uvs = [(0, 0), (1, 0), (1, 1), (0, 1)]
    elif direction == '+X':
        pts = [(cx + ts, cy, 0), (cx + ts, cy + ts, 0), (cx + ts, cy + ts, wh), (cx + ts, cy, wh)]
        uvs = [(0, 0), (1, 0), (1, 1), (0, 1)]
    elif direction == '-X':
        pts = [(cx, cy, 0), (cx, cy, wh), (cx, cy + ts, wh), (cx, cy + ts, 0)]
        uvs = [(0, 0), (0, 1), (1, 1), (1, 0)]
    verts = [bm.verts.new(p) for p in pts]
    bm.verts.ensure_lookup_table()
    face = bm.faces.new(verts)
    for loop, uv in zip(face.loops, uvs):
        loop[uv_layer].uv = uv


def _add_mesh_at(bm, src_mesh, matrix, uv_layer, swap_uv=False):
    temp_bm = bmesh.new()
    temp_bm.from_mesh(src_mesh)
    bmesh.ops.transform(temp_bm, matrix=matrix, verts=temp_bm.verts)
    src_uv = temp_bm.loops.layers.uv.active

    vert_map = {}
    for v in temp_bm.verts:
        new_v = bm.verts.new(v.co)
        vert_map[v.index] = new_v
    bm.verts.ensure_lookup_table()

    for f in temp_bm.faces:
        new_verts = [vert_map[loop.vert.index] for loop in f.loops]
        new_face = bm.faces.new(new_verts)
        if src_uv and uv_layer:
            for src_loop, new_loop in zip(f.loops, new_face.loops):
                u, v = src_loop[src_uv].uv
                if swap_uv:
                    u, v = v, u
                new_loop[uv_layer].uv = (u, v)

    temp_bm.free()


def _add_floor_tile(bm, uv_layer, x, y, ts):
    x0, y0 = x * ts, y * ts
    x1, y1 = (x + 1) * ts, (y + 1) * ts
    verts = [
        bm.verts.new((x0, y0, 0)),
        bm.verts.new((x1, y0, 0)),
        bm.verts.new((x1, y1, 0)),
        bm.verts.new((x0, y1, 0)),
    ]
    bm.verts.ensure_lookup_table()
    face = bm.faces.new(verts)
    uvs = [(0, 0), (1, 0), (1, 1), (0, 1)]
    for loop, uv in zip(face.loops, uvs):
        loop[uv_layer].uv = uv


def _add_horizontal_wall_faces(bm, uv_layer, x, y, ts, wh, wt):
    x0, x1 = x * ts, (x + 1) * ts
    yc = y * ts
    t = wt / 2
    v0 = bm.verts.new((x0, yc - t, 0))
    v1 = bm.verts.new((x1, yc - t, 0))
    v2 = bm.verts.new((x1, yc - t, wh))
    v3 = bm.verts.new((x0, yc - t, wh))
    v4 = bm.verts.new((x0, yc + t, 0))
    v5 = bm.verts.new((x1, yc + t, 0))
    v6 = bm.verts.new((x1, yc + t, wh))
    v7 = bm.verts.new((x0, yc + t, wh))
    bm.verts.ensure_lookup_table()

    face_data = [
        ([v0, v1, v2, v3], [(0, 0), (1, 0), (1, 1), (0, 1)]),
        ([v5, v4, v7, v6], [(0, 0), (1, 0), (1, 1), (0, 1)]),
        ([v4, v0, v3, v7], [(0, 0), (1, 0), (1, 1), (0, 1)]),
        ([v1, v5, v6, v2], [(0, 0), (1, 0), (1, 1), (0, 1)]),
    ]
    for verts, uvs in face_data:
        face = bm.faces.new(verts)
        for loop, uv in zip(face.loops, uvs):
            loop[uv_layer].uv = uv


def _add_horizontal_roof_face(bm, uv_layer, x, y, ts, wh, wt):
    x0, x1 = x * ts, (x + 1) * ts
    yc = y * ts
    t = wt / 2
    v0 = bm.verts.new((x0, yc - t, wh))
    v1 = bm.verts.new((x1, yc - t, wh))
    v2 = bm.verts.new((x1, yc + t, wh))
    v3 = bm.verts.new((x0, yc + t, wh))
    bm.verts.ensure_lookup_table()
    face = bm.faces.new([v0, v1, v2, v3])
    for loop, uv in zip(face.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop[uv_layer].uv = uv


def _add_vertical_wall_faces(bm, uv_layer, x, y, ts, wh, wt):
    xc = x * ts
    y0, y1 = y * ts, (y + 1) * ts
    t = wt / 2
    v0 = bm.verts.new((xc - t, y0, 0))
    v1 = bm.verts.new((xc - t, y1, 0))
    v2 = bm.verts.new((xc - t, y1, wh))
    v3 = bm.verts.new((xc - t, y0, wh))
    v4 = bm.verts.new((xc + t, y0, 0))
    v5 = bm.verts.new((xc + t, y1, 0))
    v6 = bm.verts.new((xc + t, y1, wh))
    v7 = bm.verts.new((xc + t, y0, wh))
    bm.verts.ensure_lookup_table()

    face_data = [
        ([v0, v3, v2, v1], [(0, 0), (1, 0), (1, 1), (0, 1)]),
        ([v4, v5, v6, v7], [(0, 0), (1, 0), (1, 1), (0, 1)]),
        ([v0, v4, v7, v3], [(0, 0), (1, 0), (1, 1), (0, 1)]),
        ([v1, v2, v6, v5], [(0, 0), (1, 0), (1, 1), (0, 1)]),
    ]
    for verts, uvs in face_data:
        face = bm.faces.new(verts)
        for loop, uv in zip(face.loops, uvs):
            loop[uv_layer].uv = uv


def _add_vertical_roof_face(bm, uv_layer, x, y, ts, wh, wt):
    xc = x * ts
    y0, y1 = y * ts, (y + 1) * ts
    t = wt / 2
    v0 = bm.verts.new((xc - t, y0, wh))
    v1 = bm.verts.new((xc + t, y0, wh))
    v2 = bm.verts.new((xc + t, y1, wh))
    v3 = bm.verts.new((xc - t, y1, wh))
    bm.verts.ensure_lookup_table()
    face = bm.faces.new([v0, v1, v2, v3])
    for loop, uv in zip(face.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop[uv_layer].uv = uv


def _add_cube_wall_faces(bm, uv_layer, cx, cy, sx, sy, sz):
    v0 = bm.verts.new((cx - sx / 2, cy - sy / 2, 0))
    v1 = bm.verts.new((cx + sx / 2, cy - sy / 2, 0))
    v2 = bm.verts.new((cx + sx / 2, cy + sy / 2, 0))
    v3 = bm.verts.new((cx - sx / 2, cy + sy / 2, 0))
    v4 = bm.verts.new((cx - sx / 2, cy - sy / 2, sz))
    v5 = bm.verts.new((cx + sx / 2, cy - sy / 2, sz))
    v6 = bm.verts.new((cx + sx / 2, cy + sy / 2, sz))
    v7 = bm.verts.new((cx - sx / 2, cy + sy / 2, sz))
    bm.verts.ensure_lookup_table()
    face_data = [
        ([v0, v1, v5, v4], [(0, 0), (1, 0), (1, 1), (0, 1)]),
        ([v3, v7, v6, v2], [(0, 0), (1, 0), (1, 1), (0, 1)]),
        ([v0, v4, v7, v3], [(0, 0), (1, 0), (1, 1), (0, 1)]),
        ([v1, v2, v6, v5], [(0, 0), (1, 0), (1, 1), (0, 1)]),
    ]
    for verts, uvs in face_data:
        face = bm.faces.new(verts)
        for loop, uv in zip(face.loops, uvs):
            loop[uv_layer].uv = uv



def _add_cube_roof_face(bm, uv_layer, cx, cy, sx, sy, sz):
    v0 = bm.verts.new((cx - sx / 2, cy - sy / 2, sz))
    v1 = bm.verts.new((cx + sx / 2, cy - sy / 2, sz))
    v2 = bm.verts.new((cx + sx / 2, cy + sy / 2, sz))
    v3 = bm.verts.new((cx - sx / 2, cy + sy / 2, sz))
    bm.verts.ensure_lookup_table()
    face = bm.faces.new([v0, v1, v2, v3])
    for loop, uv in zip(face.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop[uv_layer].uv = uv


def _add_horizontal_cube_wall(bm, uv_layer, x, y, ts, wh):
    _add_cube_wall_faces(bm, uv_layer, x * ts + ts / 2, y * ts, ts, ts, wh)


def _add_vertical_cube_wall(bm, uv_layer, x, y, ts, wh):
    _add_cube_wall_faces(bm, uv_layer, x * ts, y * ts + ts / 2, ts, ts, wh)


def _add_horizontal_cube_roof(bm, uv_layer, x, y, ts, wh):
    _add_cube_roof_face(bm, uv_layer, x * ts + ts / 2, y * ts, ts, ts, wh)


def _add_vertical_cube_roof(bm, uv_layer, x, y, ts, wh):
    _add_cube_roof_face(bm, uv_layer, x * ts, y * ts + ts / 2, ts, ts, wh)


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


def _add_floor_tile_at(bm, uv_layer, x0, y0, ts):
    verts = [
        bm.verts.new((x0, y0, 0)),
        bm.verts.new((x0 + ts, y0, 0)),
        bm.verts.new((x0 + ts, y0 + ts, 0)),
        bm.verts.new((x0, y0 + ts, 0)),
    ]
    bm.verts.ensure_lookup_table()
    face = bm.faces.new(verts)
    for loop, uv in zip(face.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop[uv_layer].uv = uv


def build_maze_objects(props, maze_data, context, collection=None):
    ts = props.tile_size
    wh = props.wall_height
    wt = props.wall_thickness
    wall_mode = props.wall_mode

    custom_floor = props.custom_floor_mesh
    custom_wall_north = props.custom_wall_north
    custom_wall_south = props.custom_wall_south
    custom_wall_east = props.custom_wall_east
    custom_wall_west = props.custom_wall_west
    custom_roof = props.custom_roof_mesh
    centered = props.tiles_centered

    if collection is None:
        col = bpy.data.collections.new("FireMaze")
        context.scene.collection.children.link(col)
    else:
        col = collection
        if col.name not in context.scene.collection.children:
            context.scene.collection.children.link(col)

    materials = _ensure_materials()

    if wall_mode == 'cube':
        gw = maze_data.width * 2 + 1
        gh = maze_data.depth * 2 + 1

        # True = wall tile, False = floor tile
        tiles = [[True] * gw for _ in range(gh)]

        # Cell centers are always floor
        for y in range(maze_data.depth):
            for x in range(maze_data.width):
                tiles[2 * y + 1][2 * x + 1] = False

        # Open passages are floor
        for y in range(maze_data.depth):
            for x in range(maze_data.width):
                c = maze_data.cells[y][x]
                if not c[0]:
                    tiles[2 * y + 2][2 * x + 1] = False
                if not c[1]:
                    tiles[2 * y][2 * x + 1] = False
                if not c[2]:
                    tiles[2 * y + 1][2 * x + 2] = False
                if not c[3]:
                    tiles[2 * y + 1][2 * x] = False

        # Entrance
        ex, ey, ed = maze_data.entrance
        if ed == 'S':
            tiles[0][2 * ex + 1] = False
        elif ed == 'N':
            tiles[2 * maze_data.depth][2 * ex + 1] = False
        elif ed == 'W':
            tiles[2 * ey + 1][0] = False
        elif ed == 'E':
            tiles[2 * ey + 1][2 * maze_data.width] = False

        # Exits
        for ex, ey, ed in maze_data.exits:
            if ed == 'S':
                tiles[0][2 * ex + 1] = False
            elif ed == 'N':
                tiles[2 * maze_data.depth][2 * ex + 1] = False
            elif ed == 'W':
                tiles[2 * ey + 1][0] = False
            elif ed == 'E':
                tiles[2 * ey + 1][2 * maze_data.width] = False

        # Floor
        bm_floor = bmesh.new()
        uv_floor = bm_floor.loops.layers.uv.new("UVMap")
        for gy in range(gh):
            for gx in range(gw):
                if not tiles[gy][gx]:
                    if custom_floor:
                        off = ts / 2 if centered else 0
                        mat = Matrix.Translation(Vector((gx * ts + off, gy * ts + off, 0)))
                        _add_mesh_at(bm_floor, custom_floor, mat, uv_floor)
                    else:
                        _add_floor_tile_at(bm_floor, uv_floor, gx * ts, gy * ts, ts)
        _create_object_from_bm(bm_floor, "FireMaze_Floor", col, materials["floor"])

        # Walls
        bm_wall = bmesh.new()
        uv_wall = bm_wall.loops.layers.uv.new("UVMap")
        cent = Matrix.Translation(Vector((-ts / 2, -ts / 2, 0))) if not centered else Matrix.Identity(4)
        hw = wh / 2
        for gy in range(gh):
            for gx in range(gw):
                if tiles[gy][gx]:
                    cx, cy = gx * ts, gy * ts
                    # +Y (north)
                    if gy + 1 >= gh or not tiles[gy + 1][gx]:
                        if custom_wall_north:
                            mat = Matrix.Translation(Vector((cx + ts / 2, cy + ts, hw))) @ Matrix.Rotation(math.radians(-90), 4, 'X') @ cent
                            _add_mesh_at(bm_wall, custom_wall_north, mat, uv_wall)
                        else:
                            _add_wall_face(bm_wall, uv_wall, cx, cy, ts, wh, '+Y')
                    # -Y (south)
                    if gy - 1 < 0 or not tiles[gy - 1][gx]:
                        if custom_wall_south:
                            mat = Matrix.Translation(Vector((cx + ts / 2, cy, hw))) @ Matrix.Rotation(math.radians(90), 4, 'X') @ cent
                            _add_mesh_at(bm_wall, custom_wall_south, mat, uv_wall)
                        else:
                            _add_wall_face(bm_wall, uv_wall, cx, cy, ts, wh, '-Y')
                    # +X (east)
                    if gx + 1 >= gw or not tiles[gy][gx + 1]:
                        if custom_wall_east:
                            mat = Matrix.Translation(Vector((cx + ts, cy + ts / 2, hw))) @ Matrix.Rotation(math.radians(90), 4, 'Y') @ cent
                            _add_mesh_at(bm_wall, custom_wall_east, mat, uv_wall, swap_uv=True)
                        else:
                            _add_wall_face(bm_wall, uv_wall, cx, cy, ts, wh, '+X')
                    # -X (west)
                    if gx - 1 < 0 or not tiles[gy][gx - 1]:
                        if custom_wall_west:
                            mat = Matrix.Translation(Vector((cx, cy + ts / 2, hw))) @ Matrix.Rotation(math.radians(-90), 4, 'Y') @ cent
                            _add_mesh_at(bm_wall, custom_wall_west, mat, uv_wall, swap_uv=True)
                        else:
                            _add_wall_face(bm_wall, uv_wall, cx, cy, ts, wh, '-X')
        bmesh.ops.remove_doubles(bm_wall, verts=bm_wall.verts, dist=0.001)
        _create_object_from_bm(bm_wall, "FireMaze_Walls", col, materials["wall"])

        # Roof
        bm_roof = bmesh.new()
        uv_roof = bm_roof.loops.layers.uv.new("UVMap")
        for gy in range(gh):
            for gx in range(gw):
                if tiles[gy][gx]:
                    if custom_roof:
                        off = ts / 2 if centered else 0
                        mat = Matrix.Translation(Vector((gx * ts + off, gy * ts + off, wh)))
                        _add_mesh_at(bm_roof, custom_roof, mat, uv_roof)
                    else:
                        _add_cube_roof_face(bm_roof, uv_roof, gx * ts + ts / 2, gy * ts + ts / 2, ts, ts, wh)
        if not custom_roof:
            bmesh.ops.remove_doubles(bm_roof, verts=bm_roof.verts, dist=0.001)
        _create_object_from_bm(bm_roof, "FireMaze_Roof", col, materials["roof"])

    else:
        # Thin wall mode — walls on grid lines between adjacent floor cells
        segments = list(_get_wall_segments(maze_data))

        # Floor
        bm_floor = bmesh.new()
        uv_floor = bm_floor.loops.layers.uv.new("UVMap")
        if custom_floor:
            off = ts / 2 if centered else 0
            for y in range(maze_data.depth):
                for x in range(maze_data.width):
                    mat = Matrix.Translation(Vector((x * ts + off, y * ts + off, 0)))
                    _add_mesh_at(bm_floor, custom_floor, mat, uv_floor)
        else:
            for y in range(maze_data.depth):
                for x in range(maze_data.width):
                    _add_floor_tile_at(bm_floor, uv_floor, x * ts, y * ts, ts)
        _create_object_from_bm(bm_floor, "FireMaze_Floor", col, materials["floor"])

        # Walls
        bm_wall = bmesh.new()
        uv_wall = bm_wall.loops.layers.uv.new("UVMap")
        has_any_wall_custom = custom_wall_north or custom_wall_south or custom_wall_east or custom_wall_west
        if has_any_wall_custom:
            cent = Matrix.Translation(Vector((-ts / 2, -ts / 2, 0))) if not centered else Matrix.Identity(4)
            hw = wh / 2
            tw = wt / 2
            for seg_type, a, b in segments:
                if seg_type == 'H':
                    x0, x1 = a * ts, (a + 1) * ts
                    yc = b * ts
                    # North face (+Y)
                    if custom_wall_north:
                        mat = Matrix.Translation(Vector((x0 + ts / 2, yc + tw, hw))) @ Matrix.Rotation(math.radians(-90), 4, 'X') @ cent
                        _add_mesh_at(bm_wall, custom_wall_north, mat, uv_wall)
                    else:
                        verts = [bm_wall.verts.new(v) for v in [(x0, yc + tw, 0), (x1, yc + tw, 0), (x1, yc + tw, wh), (x0, yc + tw, wh)]]
                        bm_wall.verts.ensure_lookup_table()
                        f = bm_wall.faces.new(verts)
                        for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                            loop[uv_wall].uv = uv
                    # South face (-Y)
                    if custom_wall_south:
                        mat = Matrix.Translation(Vector((x0 + ts / 2, yc - tw, hw))) @ Matrix.Rotation(math.radians(90), 4, 'X') @ cent
                        _add_mesh_at(bm_wall, custom_wall_south, mat, uv_wall)
                    else:
                        verts = [bm_wall.verts.new(v) for v in [(x0, yc - tw, 0), (x1, yc - tw, 0), (x1, yc - tw, wh), (x0, yc - tw, wh)]]
                        bm_wall.verts.ensure_lookup_table()
                        f = bm_wall.faces.new(verts)
                        for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                            loop[uv_wall].uv = uv
                    # West end-cap (always generated)
                    verts = [bm_wall.verts.new(v) for v in [(x0, yc + tw, 0), (x0, yc - tw, 0), (x0, yc - tw, wh), (x0, yc + tw, wh)]]
                    bm_wall.verts.ensure_lookup_table()
                    f = bm_wall.faces.new(verts)
                    for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                        loop[uv_wall].uv = uv
                    # East end-cap (always generated)
                    verts = [bm_wall.verts.new(v) for v in [(x1, yc - tw, 0), (x1, yc + tw, 0), (x1, yc + tw, wh), (x1, yc - tw, wh)]]
                    bm_wall.verts.ensure_lookup_table()
                    f = bm_wall.faces.new(verts)
                    for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                        loop[uv_wall].uv = uv
                else:
                    xc = a * ts
                    y0, y1 = b * ts, (b + 1) * ts
                    # East face (+X)
                    if custom_wall_east:
                        mat = Matrix.Translation(Vector((xc + tw, y0 + ts / 2, hw))) @ Matrix.Rotation(math.radians(90), 4, 'Y') @ cent
                        _add_mesh_at(bm_wall, custom_wall_east, mat, uv_wall, swap_uv=True)
                    else:
                        verts = [bm_wall.verts.new(v) for v in [(xc + tw, y0, 0), (xc + tw, y1, 0), (xc + tw, y1, wh), (xc + tw, y0, wh)]]
                        bm_wall.verts.ensure_lookup_table()
                        f = bm_wall.faces.new(verts)
                        for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                            loop[uv_wall].uv = uv
                    # West face (-X)
                    if custom_wall_west:
                        mat = Matrix.Translation(Vector((xc - tw, y0 + ts / 2, hw))) @ Matrix.Rotation(math.radians(-90), 4, 'Y') @ cent
                        _add_mesh_at(bm_wall, custom_wall_west, mat, uv_wall, swap_uv=True)
                    else:
                        verts = [bm_wall.verts.new(v) for v in [(xc - tw, y0, 0), (xc - tw, y1, 0), (xc - tw, y1, wh), (xc - tw, y0, wh)]]
                        bm_wall.verts.ensure_lookup_table()
                        f = bm_wall.faces.new(verts)
                        for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                            loop[uv_wall].uv = uv
                    # South end-cap (always generated)
                    verts = [bm_wall.verts.new(v) for v in [(xc - tw, y0, 0), (xc + tw, y0, 0), (xc + tw, y0, wh), (xc - tw, y0, wh)]]
                    bm_wall.verts.ensure_lookup_table()
                    f = bm_wall.faces.new(verts)
                    for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                        loop[uv_wall].uv = uv
                    # North end-cap (always generated)
                    verts = [bm_wall.verts.new(v) for v in [(xc + tw, y1, 0), (xc - tw, y1, 0), (xc - tw, y1, wh), (xc + tw, y1, wh)]]
                    bm_wall.verts.ensure_lookup_table()
                    f = bm_wall.faces.new(verts)
                    for loop, uv in zip(f.loops, [(0,0),(1,0),(1,1),(0,1)]):
                        loop[uv_wall].uv = uv
        else:
            for seg_type, a, b in segments:
                if seg_type == 'H':
                    _add_horizontal_wall_faces(bm_wall, uv_wall, a, b, ts, wh, wt)
                else:
                    _add_vertical_wall_faces(bm_wall, uv_wall, a, b, ts, wh, wt)
        _create_object_from_bm(bm_wall, "FireMaze_Walls", col, materials["wall"])

        # Roof
        bm_roof = bmesh.new()
        uv_roof = bm_roof.loops.layers.uv.new("UVMap")
        if custom_roof:
            for seg_type, a, b in segments:
                if seg_type == 'H':
                    cx = a * ts + ts / 2
                    cy = b * ts
                else:
                    cx = a * ts
                    cy = b * ts + ts / 2
                mat = Matrix.Translation(Vector((cx, cy, wh)))
                _add_mesh_at(bm_roof, custom_roof, mat, uv_roof)
        else:
            for seg_type, a, b in segments:
                if seg_type == 'H':
                    _add_horizontal_roof_face(bm_roof, uv_roof, a, b, ts, wh, wt)
                else:
                    _add_vertical_roof_face(bm_roof, uv_roof, a, b, ts, wh, wt)
        _create_object_from_bm(bm_roof, "FireMaze_Roof", col, materials["roof"])

    return col


def _ensure_materials():
    mats = {}
    for key, label, color in [
        ("floor", "FireMaze_Floor", (0.15, 0.15, 0.15, 1.0)),
        ("wall", "FireMaze_Walls", (0.35, 0.35, 0.35, 1.0)),
        ("roof", "FireMaze_Roof", (0.25, 0.25, 0.25, 1.0)),
    ]:
        mat = bpy.data.materials.get(label)
        if not mat:
            mat = bpy.data.materials.new(label)
            mat.use_nodes = True
            bsdf = mat.node_tree.nodes.get("Principled BSDF")
            if bsdf:
                bsdf.inputs["Base Color"].default_value = color
        mats[key] = mat
    return mats
