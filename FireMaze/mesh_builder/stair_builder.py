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

    # NOTE: The top landing platform is correctly placed on the +X side to align with the CCW winding steps.
    # The user can rotate the stairs to face any desired direction. The platform MUST NOT be rotated separately
    # from the stairs (e.g. to +Y) as doing so will break the spiral staircase alignment.
    # Extends from x = 0 to x = ts/2, and y = -ts/2 to y = ts/2 at height z_offset + wh. Do not shift this.
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

    p_verts = [bm.verts.new(pt) for pt in p_plat]

    # Platform Top Face
    v_top_plat = [p_verts[4], p_verts[5], p_verts[6], p_verts[7]]
    f_top_plat = bm.faces.new(v_top_plat)
    for loop, uv in zip(f_top_plat.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop[uv_layer].uv = uv

    # Platform Bottom Face
    v_bot_plat = [p_verts[3], p_verts[2], p_verts[1], p_verts[0]]
    f_bot_plat = bm.faces.new(v_bot_plat)
    for loop, uv in zip(f_bot_plat.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop[uv_layer].uv = uv

    # Platform Side Faces (South, East, North, West)
    sides = [
        [p_verts[0], p_verts[1], p_verts[5], p_verts[4]], # South
        [p_verts[1], p_verts[2], p_verts[6], p_verts[5]], # East
        [p_verts[2], p_verts[3], p_verts[7], p_verts[6]], # North
        [p_verts[3], p_verts[0], p_verts[4], p_verts[7]], # West
    ]
    for side_verts in sides:
        f_side = bm.faces.new(side_verts)
        for loop, uv in zip(f_side.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
            loop[uv_layer].uv = uv

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
        for loop, uv in zip(f.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
            loop[uv_layer].uv = uv

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
        for loop, uv in zip(f_top.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
            loop[uv_layer].uv = uv

        # Bottom face (normal points DOWN)
        v_bot = [bm.verts.new(p0), bm.verts.new(p3), bm.verts.new(p2), bm.verts.new(p1)]
        f_bot = bm.faces.new(v_bot)
        for loop, uv in zip(f_bot.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
            loop[uv_layer].uv = uv

        # Outer riser
        v_outer = [bm.verts.new(p1), bm.verts.new(p2), bm.verts.new(p6), bm.verts.new(p5)]
        f_outer = bm.faces.new(v_outer)
        for loop, uv in zip(f_outer.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
            loop[uv_layer].uv = uv

        # Inner riser
        v_inner = [bm.verts.new(p3), bm.verts.new(p0), bm.verts.new(p4), bm.verts.new(p7)]
        f_inner = bm.faces.new(v_inner)
        for loop, uv in zip(f_inner.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
            loop[uv_layer].uv = uv

        # CW side (Start-angle face / back riser)
        v_cw = [bm.verts.new(p0), bm.verts.new(p1), bm.verts.new(p5), bm.verts.new(p4)]
        f_cw = bm.faces.new(v_cw)
        for loop, uv in zip(f_cw.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
            loop[uv_layer].uv = uv

        # CCW side (End-angle face / front riser)
        v_ccw = [bm.verts.new(p2), bm.verts.new(p3), bm.verts.new(p7), bm.verts.new(p6)]
        f_ccw = bm.faces.new(v_ccw)
        for loop, uv in zip(f_ccw.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
            loop[uv_layer].uv = uv


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
    for loop, uv in zip(f.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop[uv_layer].uv = uv

    # Bottom face
    v = [bm.verts.new(p7), bm.verts.new(p6), bm.verts.new(p5), bm.verts.new(p4)]
    f = bm.faces.new(v)
    for loop, uv in zip(f.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop[uv_layer].uv = uv

    # Left side panel (solid wedge at x = -t2)
    p_back_bottom_L = T_base @ Vector((-t2, -t2, z_offset))
    p_front_bottom_L = T_base @ Vector((-t2, t2, z_offset))
    p_front_top_L = T_base @ Vector((-t2, t2, z_offset + hw))
    v = [bm.verts.new(p_back_bottom_L), bm.verts.new(p_front_top_L), bm.verts.new(p_front_bottom_L)]
    f = bm.faces.new(v)
    for loop, uv in zip(f.loops, [(0, 0), (1, 1), (0, 1)]):
        loop[uv_layer].uv = uv

    # Right side panel (solid wedge at x = t2)
    p_back_bottom_R = T_base @ Vector((t2, -t2, z_offset))
    p_front_bottom_R = T_base @ Vector((t2, t2, z_offset))
    p_front_top_R = T_base @ Vector((t2, t2, z_offset + hw))
    v = [bm.verts.new(p_back_bottom_R), bm.verts.new(p_front_bottom_R), bm.verts.new(p_front_top_R)]
    f = bm.faces.new(v)
    for loop, uv in zip(f.loops, [(0, 0), (1, 0), (1, 1)]):
        loop[uv_layer].uv = uv

    # Front face (vertical wall at y = t2)
    v_back = [bm.verts.new(p_front_bottom_R), bm.verts.new(p_front_bottom_L), bm.verts.new(p_front_top_L), bm.verts.new(p_front_top_R)]
    f_back = bm.faces.new(v_back)
    for loop, uv in zip(f_back.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop[uv_layer].uv = uv
