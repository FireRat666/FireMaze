"""Procedural stair and ramp mesh generation for FireMaze."""

import math
import bmesh
from mathutils import Matrix, Vector

def _build_spiral_stair_1x1(
    bm: bmesh.types.BMesh,
    uv_layer: bmesh.types.BMLayerItem,
    cx: float,
    cy: float,
    ts: float,
    wh: float,
    z_offset: float,
    mat_offset: Matrix,
) -> None:
    """Procedural 1x1 spiral staircase: central post + 12 wedge steps, full 360° rotation, rising wh."""
    T_base = Matrix.Translation(Vector((cx, cy, 0))) @ mat_offset
    steps = 12
    rise_per_step = wh / steps
    angle_per_step = 2 * math.pi / steps
    post_r = max(0.02, ts * 0.08)
    step_span = ts * 0.48
    thickness = 0.02 * ts

    # Top exit landing platform on the +X side (exit side) THIS IS CORRECT
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


def _build_ramp_1x1(
    bm: bmesh.types.BMesh,
    uv_layer: bmesh.types.BMLayerItem,
    cx: float,
    cy: float,
    ts: float,
    wh: float,
    z_offset: float,
    mat_offset: Matrix,
) -> None:
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
