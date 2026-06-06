"""Maze generation algorithms and data structures for FireMaze.

Provides 10 maze generation algorithms (DFS, Kruskal, Eller, Binary Tree,
Prim, Hunt-and-Kill, Sidewinder, Wilson, Recursive Division, Growing Tree),
pathfinding (BFS), image mask support, and 3D multilevel expansion.
"""

import random as _real_random
random = _real_random.Random()
import math
import copy
import logging
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
from collections import deque
from .utils import _resolve_cells_3d, _get_stair_footprint_coords

logger = logging.getLogger(__name__)

@dataclass
class MazeData:
    """Container for all maze data: dimensions, cells, entrance/exits, guide path, grid type, stairs."""

    width: int
    depth: int
    cells: List  # cells[z][y][x] = [...] (3D for multilevel, 2D for single-floor back-compat)
    entrance: Tuple[int, int, str]
    exits: List[Tuple[int, int, str]] = field(default_factory=list)
    center: Tuple[int, int] = (0, 0)
    guide_path: List = field(default_factory=list)  # (z, y, x) 3-tuples or (y, x) 2-tuples back-compat
    grid_type: str = 'rect'
    polar_rings: int = 0
    ring_sectors: List[int] = field(default_factory=list)
    floors: int = 1
    stairs: List[dict] = field(default_factory=list)


def _biased_choice(length, bias):
    """Select a randomized index in range(length) biased by the bias parameter.

    bias = 0.5: uniform.
    bias < 0.5: pushed toward center.
    bias > 0.5: pushed toward edges.
    """
    if length <= 1:
        return 0
    u = random.random()
    if bias == 0.5:
        pass
    elif bias < 0.5:
        p = 0.5 / max(0.01, bias)
        diff = u - 0.5
        sign = 1.0 if diff >= 0 else -1.0
        u = 0.5 + sign * (abs(2.0 * diff) ** p) / 2.0
    else:
        p = 2.0 * (1.0 - bias)
        diff = u - 0.5
        sign = 1.0 if diff >= 0 else -1.0
        u = 0.5 + sign * (abs(2.0 * diff) ** p) / 2.0
    return max(0, min(length - 1, int(u * length)))


class UnionFind:
    """Disjoint-set data structure (union-find) with path compression."""

    def __init__(self, size):
        """Initialize parent array with each element pointing to itself."""
        self.parent = list(range(size))

    def find(self, i):
        path = []
        """Find the root representative of element i with path compression."""
        while self.parent[i] != i:
            path.append(i)
            i = self.parent[i]
        for node in path:
            self.parent[node] = i
        return i

    def union(self, i, j):
        """Union the sets containing i and j. Return True if a merge occurred."""
        root_i = self.find(i)
        root_j = self.find(j)
        if root_i != root_j:
            self.parent[root_i] = root_j
            return True
        return False


def _expand_cells_to_3d(cells_2d, width, depth, floors, wall_mode, stair_count=1, stair_footprint='1x1', stair_style='stair', stair_direction='random'):
    """Clone 2D cells to 3D [floors][depth][width] and carve stair footprints.

    Args:
        cells_2d: 2D cell list for a single floor.
        width: Number of cells along X.
        depth: Number of cells along Y.
        floors: Number of vertical levels.
        wall_mode: 'thin' or 'cube'.
        stair_count: Number of staircases per floor transition.
        stair_footprint: Footprint size ('1x1', '1x2', '2x2').
        stair_style: Stair style ('stair' or 'ramp').
        stair_direction: Stair direction.

    Returns:
        Tuple of (cells_3d, stairs_placed).
    """
    if floors <= 1:
        return [cells_2d], []
    cells_3d = [cells_2d]
    for _ in range(1, floors):
        cells_3d.append(copy.deepcopy(cells_2d))
    stairs_placed = _place_stairs(
        cells_3d, width, depth, floors, wall_mode,
        stair_count=stair_count, stair_footprint=stair_footprint,
        stair_style=stair_style, stair_direction=stair_direction
    )
    return cells_3d, stairs_placed


def _force_cell_open(cells, z, y, x, wall_mode):
    """Force a cell (and its shared wall neighbors) to be fully walkable.

    Args:
        cells: 3D cell list [z][y][x].
        z: Floor index.
        y: Row index.
        x: Column index.
        wall_mode: 'thin' or 'cube'.
    """
    depth = len(cells[z])
    width = len(cells[z][0])
    if 0 <= y < depth and 0 <= x < width:
        if wall_mode == 'cube':
            cells[z][y][x][0] = False
        else:
            for i in range(4):
                cells[z][y][x][i] = False
            # Open shared walls with neighbors
            if y + 1 < depth:
                cells[z][y + 1][x][1] = False
            if y - 1 >= 0:
                cells[z][y - 1][x][0] = False
            if x + 1 < width:
                cells[z][y][x + 1][3] = False
            if x - 1 >= 0:
                cells[z][y][x - 1][2] = False



def _place_stairs(cells_3d, width, depth, floors, wall_mode, stair_count=1, stair_footprint='1x1', stair_style='stair', stair_direction='random'):
    """Place stair footprints in 3D cells and return placed stair records.

    Args:
        cells_3d: 3D cell list [z][y][x].
        width: Number of cells along X.
        depth: Number of cells along Y.
        floors: Number of vertical levels.
        wall_mode: 'thin' or 'cube'.
        stair_count: Number of stairs per floor transition.
        stair_footprint: Footprint size ('1x1', '1x2', '2x2').
        stair_style: Stair style ('stair' or 'ramp').
        stair_direction: Stair direction.

    Returns:
        List of stair dicts with keys z, x, y, type, footprint, orientation.
    """
    placed = []
    for z in range(floors - 1):
        candidates = []
        for y in range(1, depth - 1):
            for x in range(1, width - 1):
                src_ok = not cells_3d[z][y][x][0] if wall_mode == 'cube' else True
                dst_ok = not cells_3d[z + 1][y][x][0] if wall_mode == 'cube' else True
                if src_ok and dst_ok:
                    candidates.append((x, y))
        if not candidates:
            continue

        footprint = stair_footprint
        if stair_direction == 'random':
            orientation = random.choice(['N', 'S', 'E', 'W'])
        else:
            orientation = stair_direction
        num_stairs = max(1, min(stair_count, len(candidates)))

        random.shuffle(candidates)
        placed_cells = set()  # track footprint cells already used by stairs on this floor
        placed_count = 0
        for sx, sy in candidates:
            if placed_count >= num_stairs:
                break
            fp_coords = _get_stair_footprint_coords(sx, sy, footprint, orientation)
            # Skip if any footprint cell touches the outer boundary of the maze
            if any(c[0] <= 0 or c[0] >= width - 1 or c[1] <= 0 or c[1] >= depth - 1 for c in fp_coords):
                continue
            # Skip if any footprint cell overlaps with an already-placed stair on this floor
            if any(c in placed_cells for c in fp_coords):
                continue
            for c in fp_coords:
                placed_cells.add(c)
                _force_cell_open(cells_3d, z, c[1], c[0], wall_mode)
                _force_cell_open(cells_3d, z + 1, c[1], c[0], wall_mode)

            rec_type = 'ramp' if stair_style in ('ramp', 'rectangular') else 'stair'
            rec = {
                'z': z,
                'x': sx,
                'y': sy,
                'type': rec_type,
                'footprint': footprint,
                'orientation': orientation,
            }
            placed.append(rec)
            placed_count += 1
    return placed


def _get_image_mask_data(mask_image, invert, width, depth):
    """Sample a mask image at cell centres and return a 2D blocked boolean grid.

    Args:
        mask_image: Blender Image datablock or None.
        invert: If True, invert brightness threshold.
        width: Grid width in cells.
        depth: Grid depth in cells.

    Returns:
        2D list [y][x] of bool, True where the cell is blocked.
    """
    blocked = [[False] * width for _ in range(depth)]
    if not mask_image:
        return blocked

    img_w, img_h = mask_image.size[0], mask_image.size[1]
    pixels = list(mask_image.pixels)
    if not pixels:
        return blocked

    for y in range(depth):
        for x in range(width):
            px = int(((x + 0.5) / width) * img_w)
            py = int(((y + 0.5) / depth) * img_h)
            px = max(0, min(img_w - 1, px))
            py = max(0, min(img_h - 1, py))
            
            idx = (py * img_w + px) * 4
            if idx + 2 < len(pixels):
                r = pixels[idx]
                g = pixels[idx + 1]
                b = pixels[idx + 2]
                brightness = 0.299 * r + 0.587 * g + 0.114 * b
                if invert:
                    brightness = 1.0 - brightness
                if brightness < 0.5:
                    blocked[y][x] = True

    return blocked


def _get_start_cell(blocked, w, h):
    """Return a random (x, y) walkable cell from the blocked grid, falling back to a random cell."""
    walkable = [(x, y) for y in range(h) for x in range(w) if not blocked[y][x]]
    if walkable:
        return random.choice(walkable)
    return (random.randrange(w), random.randrange(h))



def _generate_cube_maze(
    width: int,
    depth: int,
    seed: int,
    mode: str,
    emergency_exits: bool,
    algorithm: str,
    rooms_enable: bool,
    rooms_count: int,
    min_room_size: int,
    max_room_size: int,
    loop_probability: float,
    isolated_wall_prob: float,
    entrance_side: str,
    exit_side: str,
    num_entrances: int,
    num_exits: int,
    num_wall_meshes: int,
    num_floor_meshes: int,
    num_roof_meshes: int,
    mask_image,
    mask_invert: bool,
    floors: int,
    stair_footprint: str,
    stair_style: str,
    stair_count: int,
    stair_direction: str = 'random',
    selection_bias: float = 0.5,
    straightness: float = 0.5,
    direction_bias: float = 0.5,
    east_bias: float = 0.5,
    orientation_bias: float = 0.5,
    passage_bias: float = 0.5,
    eller_merge_prob: float = 0.5,
    wall_mode: str = 'cube',
) -> MazeData:
    """Carve a rectangular maze in cube wall mode."""
    # Grid dimensions for path cells (must be odd coordinates)
    # We partition the grid into cell centers at odd positions
    sub_w = max(1, (width - 1) // 2)
    sub_h = max(1, (depth - 1) // 2)

    # Initialize all cells to walls (True)
    # cells[y][x] = [is_wall, wall_n_index, wall_s_index, wall_e_index, wall_w_index, floor_index, roof_index]
    cells = [[[True, -1, -1, -1, -1, -1, -1] for _ in range(width)] for _ in range(depth)]
    blocked = _get_image_mask_data(mask_image, mask_invert, sub_w, sub_h)

    # Determine Rooms on the W x H sub-grid
    rooms = []
    cell_to_room = {}
    if rooms_enable:
        for _ in range(rooms_count * 5):
            if len(rooms) >= rooms_count:
                break
            rw = random.randint(min(min_room_size, sub_w), min(max_room_size, sub_w))
            rh = random.randint(min(min_room_size, sub_h), min(max_room_size, sub_h))
            rx = random.randint(0, sub_w - rw)
            ry = random.randint(0, sub_h - rh)

            # Check overlap
            overlap = False
            for r in rooms:
                rx_min = min(c[0] for c in r)
                rx_max = max(c[0] for c in r)
                ry_min = min(c[1] for c in r)
                ry_max = max(c[1] for c in r)
                if not (rx + rw - 1 < rx_min - 1 or rx > rx_max + 1 or
                        ry + rh - 1 < ry_min - 1 or ry > ry_max + 1):
                    overlap = True
                    break

            if not overlap:
                room_cells = []
                r_idx = len(rooms)
                for y in range(ry, ry + rh):
                    for x in range(rx, rx + rw):
                        room_cells.append((x, y))
                        cell_to_room[(x, y)] = r_idx
                rooms.append(room_cells)

        # Carve room regions on the actual grid (make them completely floor/False)
        for r_idx, room_cells in enumerate(rooms):
            rx_coords = [c[0] for c in room_cells]
            ry_coords = [c[1] for c in room_cells]
            x_min, x_max = min(rx_coords), max(rx_coords)
            y_min, y_max = min(ry_coords), max(ry_coords)
            
            # Room bounds on the actual block grid
            gx_start = 2 * x_min + 1
            gx_end = 2 * x_max + 1
            gy_start = 2 * y_min + 1
            gy_end = 2 * y_max + 1
            
            for gy in range(gy_start, gy_end + 1):
                for gx in range(gx_start, gx_end + 1):
                    if gx < width and gy < depth:
                        cells[gy][gx][0] = False

    # Run maze carving algorithms directly on the path cells
    if algorithm == 'dfs':
        visited = [[blocked[y][x] for x in range(sub_w)] for y in range(sub_h)]
        stack = []
        
        # Start cell
        sx, sy = _get_start_cell(blocked, sub_w, sub_h)
        
        r_start = cell_to_room.get((sx, sy))
        if r_start is not None:
            for rx, ry in rooms[r_start]:
                visited[ry][rx] = True
                stack.append((rx, ry))
                # Carve cell center
                cells[2 * ry + 1][2 * rx + 1][0] = False
        else:
            visited[sy][sx] = True
            stack.append((sx, sy))
            cells[2 * sy + 1][2 * sx + 1][0] = False

        last_dir = None
        dirs = [(0, 1), (0, -1), (1, 0), (-1, 0)]
        while stack:
            x, y = stack[-1]
            chosen_dir = None
            if last_dir and last_dir in dirs:
                ldx, ldy = last_dir
                nx, ny = x + ldx, y + ldy
                if 0 <= nx < sub_w and 0 <= ny < sub_h and not visited[ny][nx]:
                    if random.random() < straightness:
                        chosen_dir = last_dir
            if chosen_dir:
                ordered_dirs = [chosen_dir] + [d for d in dirs if d != chosen_dir]
            else:
                random.shuffle(dirs)
                ordered_dirs = list(dirs)
            carved = False
            for dx, dy in ordered_dirs:
                nx, ny = x + dx, y + dy
                if 0 <= nx < sub_w and 0 <= ny < sub_h and not visited[ny][nx]:
                    # Carve neighbor and intermediate wall cell
                    cells[2 * y + 1 + dy][2 * x + 1 + dx][0] = False
                    cells[2 * ny + 1][2 * nx + 1][0] = False
                    
                    r_next = cell_to_room.get((nx, ny))
                    if r_next is not None:
                        for rx, ry in rooms[r_next]:
                            visited[ry][rx] = True
                            stack.append((rx, ry))
                            cells[2 * ry + 1][2 * rx + 1][0] = False
                    else:
                        visited[ny][nx] = True
                        stack.append((nx, ny))
                    last_dir = (dx, dy)
                    carved = True
                    break
            if not carved:
                stack.pop()
                last_dir = None

    elif algorithm == 'kruskal':
        uf = UnionFind(sub_w * sub_h)
        for room_cells in rooms:
            if len(room_cells) > 1:
                f_cell = room_cells[0]
                idx1 = f_cell[1] * sub_w + f_cell[0]
                for x, y in room_cells[1:]:
                    idx2 = y * sub_w + x
                    uf.union(idx1, idx2)

        walls = []
        for y in range(sub_h):
            for x in range(sub_w):
                cells[2 * y + 1][2 * x + 1][0] = False  # Ensure cell center is floor
                if x + 1 < sub_w:
                    walls.append(('V', x, y))
                if y + 1 < sub_h:
                    walls.append(('H', x, y))

        random.shuffle(walls)
        for wtype, wx, wy in walls:
            if wtype == 'V':
                idx1 = wy * sub_w + wx
                idx2 = wy * sub_w + (wx + 1)
                if uf.union(idx1, idx2):
                    cells[2 * wy + 1][2 * wx + 2][0] = False
            else:
                idx1 = wy * sub_w + wx
                idx2 = (wy + 1) * sub_w + wx
                if uf.union(idx1, idx2):
                    cells[2 * wy + 2][2 * wx + 1][0] = False

    elif algorithm == 'eller':
        row_sets = list(range(sub_w))
        next_set_id = sub_w

        for y in range(sub_h):
            # Ensure all row cell centers are floor
            for x in range(sub_w):
                cells[2 * y + 1][2 * x + 1][0] = False

            for x in range(sub_w - 1):
                if cell_to_room.get((x, y)) is not None and cell_to_room.get((x, y)) == cell_to_room.get((x + 1, y)):
                    s1 = row_sets[x]
                    s2 = row_sets[x + 1]
                    if s1 != s2:
                        for idx in range(sub_w):
                            if row_sets[idx] == s2:
                                row_sets[idx] = s1
                        cells[2 * y + 1][2 * x + 2][0] = False

            if y > 0:
                for x in range(sub_w):
                    if cell_to_room.get((x, y)) is not None and cell_to_room.get((x, y)) == cell_to_room.get((x, y - 1)):
                        cells[2 * y][2 * x + 1][0] = False

            for x in range(sub_w - 1):
                s1 = row_sets[x]
                s2 = row_sets[x + 1]
                if s1 != s2:
                    same_room = (cell_to_room.get((x, y)) is not None and 
                                 cell_to_room.get((x, y)) == cell_to_room.get((x + 1, y)))
                    if same_room or random.random() < eller_merge_prob:
                        for idx in range(sub_w):
                            if row_sets[idx] == s2:
                                row_sets[idx] = s1
                        cells[2 * y + 1][2 * x + 2][0] = False

            if y < sub_h - 1:
                next_row_sets = [None] * sub_w
                set_groups = {}
                for x, s in enumerate(row_sets):
                    set_groups.setdefault(s, []).append(x)

                for s, group in set_groups.items():
                    room_up_cols = []
                    for col in group:
                        if (cell_to_room.get((col, y)) is not None and 
                            cell_to_room.get((col, y)) == cell_to_room.get((col, y + 1))):
                            room_up_cols.append(col)

                    if room_up_cols:
                        for col in room_up_cols:
                            cells[2 * y + 2][2 * col + 1][0] = False
                            cells[2 * y + 3][2 * col + 1][0] = False
                            next_row_sets[col] = s
                    else:
                        random.shuffle(group)
                        num_carves = random.randint(1, len(group))
                        for i in range(num_carves):
                            col = group[i]
                            cells[2 * y + 2][2 * col + 1][0] = False
                            cells[2 * y + 3][2 * col + 1][0] = False
                            next_row_sets[col] = s

                for x in range(sub_w):
                    if next_row_sets[x] is None:
                        next_row_sets[x] = next_set_id
                        next_set_id += 1
                row_sets = next_row_sets

        y = sub_h - 1
        for x in range(sub_w - 1):
            s1 = row_sets[x]
            s2 = row_sets[x + 1]
            if s1 != s2:
                for idx in range(sub_w):
                    if row_sets[idx] == s2:
                        row_sets[idx] = s1
                cells[2 * y + 1][2 * x + 2][0] = False

    elif algorithm == 'binary_tree':
        for y in range(sub_h):
            for x in range(sub_w):
                cells[2 * y + 1][2 * x + 1][0] = False
                can_north = (y < sub_h - 1)
                can_east = (x < sub_w - 1)
                
                if can_north and can_east:
                    if random.random() >= direction_bias:
                        cells[2 * y + 2][2 * x + 1][0] = False
                        cells[2 * y + 3][2 * x + 1][0] = False
                    else:
                        cells[2 * y + 1][2 * x + 2][0] = False
                        cells[2 * y + 1][2 * x + 3][0] = False
                elif can_north:
                    cells[2 * y + 2][2 * x + 1][0] = False
                    cells[2 * y + 3][2 * x + 1][0] = False
                elif can_east:
                    cells[2 * y + 1][2 * x + 2][0] = False
                    cells[2 * y + 1][2 * x + 3][0] = False

    elif algorithm == 'prims':
        visited = [[False] * sub_w for _ in range(sub_h)]
        frontier_walls = []

        def add_frontier_of(cx, cy):
            """Add unvisited neighbours of (cx, cy) to the Prim's frontier wall list (cube mode)."""
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < sub_w and 0 <= ny < sub_h:
                    if not visited[ny][nx]:
                        frontier_walls.append((cx, cy, nx, ny))

        sx = random.randrange(sub_w)
        sy = random.randrange(sub_h)
        
        r_start = cell_to_room.get((sx, sy))
        if r_start is not None:
            for rx, ry in rooms[r_start]:
                visited[ry][rx] = True
                cells[2 * ry + 1][2 * rx + 1][0] = False
                add_frontier_of(rx, ry)
        else:
            visited[sy][sx] = True
            cells[2 * sy + 1][2 * sx + 1][0] = False
            add_frontier_of(sx, sy)

        while frontier_walls:
            wall_idx = random.randrange(len(frontier_walls))
            x1, y1, x2, y2 = frontier_walls.pop(wall_idx)

            if visited[y1][x1] != visited[y2][x2]:
                ux, uy = (x2, y2) if not visited[y2][x2] else (x1, y1)
                cells[y1 + y2 + 1][x1 + x2 + 1][0] = False
                cells[2 * uy + 1][2 * ux + 1][0] = False
                
                r_next = cell_to_room.get((ux, uy))
                if r_next is not None:
                    for rx, ry in rooms[r_next]:
                        if not visited[ry][rx]:
                            visited[ry][rx] = True
                            cells[2 * ry + 1][2 * rx + 1][0] = False
                            add_frontier_of(rx, ry)
                else:
                    visited[uy][ux] = True
                    add_frontier_of(ux, uy)

    elif algorithm == 'hunt_and_kill':
        visited = [[False] * sub_w for _ in range(sub_h)]
        
        cx = random.randrange(sub_w)
        cy = random.randrange(sub_h)
        
        def mark_visited(x, y):
            """Mark cell (x, y) as visited in cube mode, including room cells and clearing floor."""
            r = cell_to_room.get((x, y))
            if r is not None:
                for rx, ry in rooms[r]:
                    visited[ry][rx] = True
                    cells[2 * ry + 1][2 * rx + 1][0] = False
            else:
                visited[y][x] = True
                cells[2 * y + 1][2 * x + 1][0] = False

        mark_visited(cx, cy)
        
        while True:
            dirs = [(0, 1), (0, -1), (1, 0), (-1, 0)]
            walk_stuck = False
            last_dir = None
            while not walk_stuck:
                chosen_dir = None
                if last_dir and last_dir in dirs:
                    ldx, ldy = last_dir
                    nx, ny = cx + ldx, cy + ldy
                    if 0 <= nx < sub_w and 0 <= ny < sub_h and not visited[ny][nx]:
                        if random.random() < straightness:
                            chosen_dir = last_dir
                if chosen_dir:
                    ordered_dirs = [chosen_dir] + [d for d in dirs if d != chosen_dir]
                else:
                    random.shuffle(dirs)
                    ordered_dirs = list(dirs)
                moved = False
                for dx, dy in ordered_dirs:
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < sub_w and 0 <= ny < sub_h and not visited[ny][nx]:
                        cells[cy + ny + 1][cx + nx + 1][0] = False
                        mark_visited(nx, ny)
                        cx, cy = nx, ny
                        last_dir = (dx, dy)
                        moved = True
                        break
                if not moved:
                    walk_stuck = True
                    last_dir = None
            
            hunted = False
            for y in range(sub_h):
                for x in range(sub_w):
                    if not visited[y][x]:
                        for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                            nx, ny = x + dx, y + dy
                            if 0 <= nx < sub_w and 0 <= ny < sub_h and visited[ny][nx]:
                                cells[y + ny + 1][x + nx + 1][0] = False
                                mark_visited(x, y)
                                cx, cy = x, y
                                hunted = True
                                break
                    if hunted:
                        break
                if hunted:
                    break
            
            if not hunted:
                break

    elif algorithm == 'sidewinder':
        for y in range(sub_h):
            for x in range(sub_w):
                cells[2 * y + 1][2 * x + 1][0] = False
            
            run = []
            for x in range(sub_w):
                run.append(x)
                
                in_same_room = False
                if x + 1 < sub_w:
                    r1 = cell_to_room.get((x, y))
                    r2 = cell_to_room.get((x + 1, y))
                    if r1 is not None and r1 == r2:
                        in_same_room = True

                carve_east = (x < sub_w - 1) and (in_same_room or random.random() < east_bias)
                
                if carve_east:
                    cells[2 * y + 1][2 * x + 2][0] = False
                else:
                    if y < sub_h - 1:
                        member_x = random.choice(run)
                        cells[2 * y + 2][2 * member_x + 1][0] = False
                        cells[2 * y + 3][2 * member_x + 1][0] = False
                    run = []

    elif algorithm == 'wilsons':
        visited = [[False] * sub_w for _ in range(sub_h)]
        unvisited_list = []
        
        def mark_visited(x, y):
            """Mark cell (x, y) as visited in cube mode (Wilson's)."""
            r = cell_to_room.get((x, y))
            if r is not None:
                for rx, ry in rooms[r]:
                    visited[ry][rx] = True
                    cells[2 * ry + 1][2 * rx + 1][0] = False
            else:
                visited[y][x] = True
                cells[2 * y + 1][2 * x + 1][0] = False

        sx = random.randrange(sub_w)
        sy = random.randrange(sub_h)
        mark_visited(sx, sy)
        
        for y in range(sub_h):
            for x in range(sub_w):
                if not visited[y][x]:
                    unvisited_list.append((x, y))
        
        while unvisited_list:
            unvisited_list = [(x, y) for (x, y) in unvisited_list if not visited[y][x]]
            if not unvisited_list:
                break
            
            cx, cy = random.choice(unvisited_list)
            walk = [(cx, cy)]
            
            while not visited[cy][cx]:
                dirs = [(0, 1), (0, -1), (1, 0), (-1, 0)]
                dx, dy = random.choice(dirs)
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < sub_w and 0 <= ny < sub_h:
                    if (nx, ny) in walk:
                        idx = walk.index((nx, ny))
                        walk = walk[:idx + 1]
                    else:
                        walk.append((nx, ny))
                    cx, cy = nx, ny
            
            for i in range(len(walk) - 1):
                x1, y1 = walk[i]
                x2, y2 = walk[i+1]
                cells[y1 + y2 + 1][x1 + x2 + 1][0] = False
                mark_visited(x1, y1)
            mark_visited(walk[-1][0], walk[-1][1])

    elif algorithm == 'recursive_division':
        for y in range(depth):
            for x in range(width):
                cells[y][x][0] = False
        
        for x in range(width):
            cells[0][x][0] = True
            cells[depth - 1][x][0] = True
        for y in range(depth):
            cells[y][0][0] = True
            cells[y][width - 1][0] = True
            
        def divide(rx, ry, rw, rh, horizontal):
            """Recursively subdivide a rectangular region by placing walls (cube mode)."""
            if rw < 2 or rh < 2:
                return
            
            if horizontal:
                wy_sub = ry + random.randrange(rh - 1)
                wy_actual = 2 * wy_sub + 2
                px_sub = rx + _biased_choice(rw, passage_bias)
                px_actual = 2 * px_sub + 1
                
                for x_sub in range(rx, rx + rw):
                    x_actual = 2 * x_sub + 1
                    if cell_to_room.get((x_sub, wy_sub)) is None or cell_to_room.get((x_sub, wy_sub + 1)) is None:
                        if x_sub != px_sub:
                            cells[wy_actual][x_actual][0] = True
                            cells[wy_actual][x_actual - 1][0] = True
                            cells[wy_actual][x_actual + 1][0] = True
                
                divide(rx, ry, rw, wy_sub - ry + 1, choose_orientation(rw, wy_sub - ry + 1))
                divide(rx, wy_sub + 1, rw, ry + rh - wy_sub - 1, choose_orientation(rw, ry + rh - wy_sub - 1))
            else:
                wx_sub = rx + random.randrange(rw - 1)
                wx_actual = 2 * wx_sub + 2
                py_sub = ry + _biased_choice(rh, passage_bias)
                py_actual = 2 * py_sub + 1
                
                for y_sub in range(ry, ry + rh):
                    y_actual = 2 * y_sub + 1
                    if cell_to_room.get((wx_sub, y_sub)) is None or cell_to_room.get((wx_sub + 1, y_sub)) is None:
                        if y_sub != py_sub:
                            cells[y_actual][wx_actual][0] = True
                            cells[y_actual - 1][wx_actual][0] = True
                            cells[y_actual + 1][wx_actual][0] = True
                            
                divide(rx, ry, wx_sub - rx + 1, rh, choose_orientation(wx_sub - rx + 1, rh))
                divide(wx_sub + 1, ry, rx + rw - wx_sub - 1, rh, choose_orientation(rx + rw - wx_sub - 1, rh))

        def choose_orientation(rw, rh):
            """Pick horizontal (True) or vertical (False) subdivision for recursive division (cube mode)."""
            if rw < rh:
                return True
            elif rh < rw:
                return False
            else:
                return random.random() < orientation_bias

        divide(0, 0, sub_w, sub_h, choose_orientation(sub_w, sub_h))

    elif algorithm == 'growing_tree':
        visited = [[False] * sub_w for _ in range(sub_h)]
        active = []
        
        def add_to_active(x, y):
            """Mark cell as visited and add to the active list (cube mode)."""
            r = cell_to_room.get((x, y))
            if r is not None:
                for rx, ry in rooms[r]:
                    if not visited[ry][rx]:
                        visited[ry][rx] = True
                        cells[2 * ry + 1][2 * rx + 1][0] = False
                        active.append((rx, ry))
            else:
                visited[y][x] = True
                cells[2 * y + 1][2 * x + 1][0] = False
                active.append((x, y))

        sx = random.randrange(sub_w)
        sy = random.randrange(sub_h)
        add_to_active(sx, sy)
        
        while active:
            if random.random() < selection_bias:
                idx = random.randrange(len(active))
            else:
                idx = len(active) - 1
            
            cx, cy = active[idx]
            
            dirs = [(0, 1), (0, -1), (1, 0), (-1, 0)]
            random.shuffle(dirs)
            carved = False
            for dx, dy in dirs:
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < sub_w and 0 <= ny < sub_h and not visited[ny][nx]:
                    cells[cy + ny + 1][cx + nx + 1][0] = False
                    add_to_active(nx, ny)
                    carved = True
                    break
            if not carved:
                active.pop(idx)

    # Add loops directly by carving random standing wall cells in the block grid
    if loop_probability > 0:
        for y in range(1, depth - 1):
            for x in range(1, width - 1):
                # Check if it is a wall separating two floor cells
                if cells[y][x][0]:
                    is_horiz_divider = (not cells[y][x - 1][0] and not cells[y][x + 1][0])
                    is_vert_divider = (not cells[y - 1][x][0] and not cells[y + 1][x][0])
                    if is_horiz_divider or is_vert_divider:
                        if random.random() < loop_probability * 0.3:
                            cells[y][x][0] = False

    # Add isolated walls by placing wall cubes inside floor regions
    if isolated_wall_prob > 0:
        max_isolated = int((width * depth) * isolated_wall_prob * 0.1)
        placed = 0
        for _ in range(max_isolated * 5):
            if placed >= max_isolated:
                break
            wx = random.randint(1, width - 2)
            wy = random.randint(1, depth - 2)
            # Check if it is currently floor and surrounded by floors
            if not cells[wy][wx][0]:
                if (not cells[wy - 1][wx][0] and not cells[wy + 1][wx][0] and
                    not cells[wy][wx - 1][0] and not cells[wy][wx + 1][0]):
                    cells[wy][wx][0] = True
                    placed += 1

    # Carve entrances and exits on the border
    entrance_list = []
    exit_list = []

    def carve_cube_borders(count, side, is_entrance):
        """Carve entrances or exits along a side of a cube-mode maze border."""
        candidates = []
        
        # Align border carvings with actual sub-grid path coordinates
        path_cols = [2 * rx + 1 for rx in range(sub_w)]
        path_rows = [2 * ry + 1 for ry in range(sub_h)]
        
        if side == 'N' or side == 'ANY':
            for x in path_cols:
                if x < width:
                    candidates.append((x, depth - 1, 'N'))
        if side == 'S' or side == 'ANY':
            for x in path_cols:
                if x < width:
                    candidates.append((x, 0, 'S'))
        if side == 'E' or side == 'ANY':
            for y in path_rows:
                if y < depth:
                    candidates.append((width - 1, y, 'E'))
        if side == 'W' or side == 'ANY':
            for y in path_rows:
                if y < depth:
                    candidates.append((0, y, 'W'))

        random.shuffle(candidates)
        carved_count = 0
        for x, y, d in candidates:
            if carved_count >= count:
                break
            already_used = False
            for ex, ey, ed in entrance_list + exit_list:
                if ex == x and ey == y and ed == d:
                    already_used = True
                    break
            if already_used:
                continue

            # Set border cell to floor
            cells[y][x][0] = False
            
            # Make sure the entrance connects to the nearest sub-maze cell center
            if d == 'N' and y - 1 >= 0:
                cells[y - 1][x][0] = False
            elif d == 'S' and y + 1 < depth:
                cells[y + 1][x][0] = False
            elif d == 'E' and x - 1 >= 0:
                cells[y][x - 1][0] = False
            elif d == 'W' and x + 1 < width:
                cells[y][x + 1][0] = False

            if is_entrance:
                entrance_list.append((x, y, d))
            else:
                exit_list.append((x, y, d))
            carved_count += 1

        # If we still need more but ran out of unique cells, allow reuse/overlapping
        if carved_count < count:
            for x, y, d in candidates:
                if carved_count >= count:
                    break
                cells[y][x][0] = False
                if d == 'N' and y - 1 >= 0:
                    cells[y - 1][x][0] = False
                elif d == 'S' and y + 1 < depth:
                    cells[y + 1][x][0] = False
                elif d == 'E' and x - 1 >= 0:
                    cells[y][x - 1][0] = False
                elif d == 'W' and x + 1 < width:
                    cells[y][x + 1][0] = False

                if is_entrance:
                    entrance_list.append((x, y, d))
                else:
                    exit_list.append((x, y, d))
                carved_count += 1

    carve_cube_borders(num_entrances, entrance_side, is_entrance=True)
    if mode == 'exit':
        carve_cube_borders(num_exits, exit_side, is_entrance=False)
    else:
        if emergency_exits:
            num_ee = random.randint(1, min(3, (width + depth) // 4 + 1))
            carve_cube_borders(num_ee, 'ANY', is_entrance=False)

    # Assign random wall, floor, and roof collection indices
    for y in range(depth):
        for x in range(width):
            is_wall = cells[y][x][0]
            if is_wall:
                if num_wall_meshes > 0:
                    cells[y][x][1] = random.randrange(num_wall_meshes)
                    cells[y][x][2] = random.randrange(num_wall_meshes)
                    cells[y][x][3] = random.randrange(num_wall_meshes)
                    cells[y][x][4] = random.randrange(num_wall_meshes)
                if num_roof_meshes > 0:
                    cells[y][x][6] = random.randrange(num_roof_meshes)
            else:
                if num_floor_meshes > 0:
                    cells[y][x][5] = random.randrange(num_floor_meshes)

    main_entrance = entrance_list[0] if entrance_list else (1, 1, 'S')
    main_exits = exit_list
    center = (2 * (sub_w // 2) + 1, 2 * (sub_h // 2) + 1)
    if center[0] >= width:
        center = (width // 2, center[1])
    if center[1] >= depth:
        center = (center[0], depth // 2)

    # Expand to 3D for multilevel
    if floors > 1:
        cells, stairs_placed = _expand_cells_to_3d(
            cells, width, depth, floors, wall_mode,
            stair_count=stair_count, stair_footprint=stair_footprint,
            stair_style=stair_style, stair_direction=stair_direction
        )
    else:
        cells = [cells]
        stairs_placed = []

    maze_data = MazeData(width, depth, cells, main_entrance, main_exits, center)
    maze_data.floors = floors
    maze_data.stairs = stairs_placed
    if mask_image:
        blocked = _get_image_mask_data(mask_image, mask_invert, sub_w, sub_h)
        for y in range(sub_h):
            for x in range(sub_w):
                if blocked[y][x]:
                    # Center cell is a wall
                    cells[0][2 * y + 1][2 * x + 1][0] = True
                    for idx in range(1, 7):
                        cells[0][2 * y + 1][2 * x + 1][idx] = -1
                    # Neighbors
                    for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                        nx, ny = 2 * x + 1 + dx, 2 * y + 1 + dy
                        if 0 <= nx < width and 0 <= ny < depth:
                            cells[0][ny][nx][0] = True
                            for idx in range(1, 7):
                                cells[0][ny][nx][idx] = -1
        # Recompute entrance_list/exit_list and set against final cells state
        entrance_list = [item for item in entrance_list if not cells[0][item[1]][item[0]][0]]
        exit_list = [item for item in exit_list if not cells[0][item[1]][item[0]][0]]
        maze_data.entrance = entrance_list[0] if entrance_list else (1, 1, 'S')
        maze_data.exits = exit_list
    
    maze_data.guide_path = find_shortest_path(maze_data, wall_mode='cube')
    return maze_data



def _generate_thin_maze(
    width: int,
    depth: int,
    seed: int,
    mode: str,
    emergency_exits: bool,
    algorithm: str,
    rooms_enable: bool,
    rooms_count: int,
    min_room_size: int,
    max_room_size: int,
    loop_probability: float,
    isolated_wall_prob: float,
    entrance_side: str,
    exit_side: str,
    num_entrances: int,
    num_exits: int,
    num_wall_meshes: int,
    num_floor_meshes: int,
    num_roof_meshes: int,
    mask_image,
    mask_invert: bool,
    floors: int,
    stair_footprint: str,
    stair_style: str,
    stair_count: int,
    stair_direction: str = 'random',
    selection_bias: float = 0.5,
    straightness: float = 0.5,
    direction_bias: float = 0.5,
    east_bias: float = 0.5,
    orientation_bias: float = 0.5,
    passage_bias: float = 0.5,
    eller_merge_prob: float = 0.5,
    wall_mode: str = 'thin',
) -> MazeData:
    """Carve a rectangular maze in thin wall mode."""
    # Thin wall mode
    # cells[y][x] = [N, S, E, W, n_wall_idx, s_wall_idx, e_wall_idx, w_wall_idx, floor_mesh_index, roof_mesh_index]
    cells = [[[True, True, True, True, -1, -1, -1, -1, -1, -1] for _ in range(width)] for _ in range(depth)]
    blocked = _get_image_mask_data(mask_image, mask_invert, width, depth)
    index = {'N': 0, 'S': 1, 'E': 2, 'W': 3}
    opposites = {'N': 'S', 'S': 'N', 'E': 'W', 'W': 'E'}

    # Generate Rooms (pre-carved areas)
    rooms = []
    cell_to_room = {}
    if rooms_enable:
        for _ in range(rooms_count * 5):
            if len(rooms) >= rooms_count:
                break
            rw = random.randint(min_room_size, max_room_size)
            rh = random.randint(min_room_size, max_room_size)
            rx = random.randint(0, width - rw)
            ry = random.randint(0, depth - rh)

            overlap = False
            for r in rooms:
                rx_min = min(c[0] for c in r)
                rx_max = max(c[0] for c in r)
                ry_min = min(c[1] for c in r)
                ry_max = max(c[1] for c in r)
                if not (rx + rw - 1 < rx_min - 1 or rx > rx_max + 1 or
                        ry + rh - 1 < ry_min - 1 or ry > ry_max + 1):
                    overlap = True
                    break

            if not overlap:
                room_cells = []
                r_idx = len(rooms)
                for y in range(ry, ry + rh):
                    for x in range(rx, rx + rw):
                        room_cells.append((x, y))
                        cell_to_room[(x, y)] = r_idx
                rooms.append(room_cells)

        for y in range(depth):
            for x in range(width):
                r_idx = cell_to_room.get((x, y))
                if r_idx is not None:
                    if y + 1 < depth and cell_to_room.get((x, y + 1)) == r_idx:
                        cells[y][x][0] = False
                        cells[y + 1][x][1] = False
                    if y - 1 >= 0 and cell_to_room.get((x, y - 1)) == r_idx:
                        cells[y][x][1] = False
                        cells[y - 1][x][0] = False
                    if x + 1 < width and cell_to_room.get((x + 1, y)) == r_idx:
                        cells[y][x][2] = False
                        cells[y][x + 1][3] = False
                    if x - 1 >= 0 and cell_to_room.get((x - 1, y)) == r_idx:
                        cells[y][x][3] = False
                        cells[y][x - 1][2] = False

    # Generate Maze using selected Algorithm
    if algorithm == 'dfs':
        visited = [[blocked[y][x] for x in range(width)] for y in range(depth)]
        stack = []
        
        sx, sy = _get_start_cell(blocked, width, depth)
        
        r_start = cell_to_room.get((sx, sy))
        if r_start is not None:
            for rx, ry in rooms[r_start]:
                visited[ry][rx] = True
                stack.append((rx, ry))
        else:
            visited[sy][sx] = True
            stack.append((sx, sy))

        last_dir = None
        raw_dirs = [('N', (0, 1)), ('S', (0, -1)), ('E', (1, 0)), ('W', (-1, 0))]
        while stack:
            x, y = stack[-1]
            chosen_dir = None
            if last_dir and last_dir in raw_dirs:
                _ldname, (ldx, ldy) = last_dir
                nx, ny = x + ldx, y + ldy
                if 0 <= nx < width and 0 <= ny < depth and not visited[ny][nx]:
                    if random.random() < straightness:
                        chosen_dir = last_dir
            if chosen_dir:
                ordered_dirs = [chosen_dir] + [d for d in raw_dirs if d != chosen_dir]
            else:
                random.shuffle(raw_dirs)
                ordered_dirs = list(raw_dirs)
            carved = False
            for dname, (dx, dy) in ordered_dirs:
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < depth and not visited[ny][nx]:
                    cells[y][x][index[dname]] = False
                    cells[ny][nx][index[opposites[dname]]] = False
                    
                    r_next = cell_to_room.get((nx, ny))
                    if r_next is not None:
                        for rx, ry in rooms[r_next]:
                            visited[ry][rx] = True
                            stack.append((rx, ry))
                    else:
                        visited[ny][nx] = True
                        stack.append((nx, ny))
                    last_dir = (dname, (dx, dy))
                    carved = True
                    break
            if not carved:
                stack.pop()
                last_dir = None

    elif algorithm == 'kruskal':
        uf = UnionFind(width * depth)
        for room_cells in rooms:
            if len(room_cells) > 1:
                first_cell = room_cells[0]
                idx1 = first_cell[1] * width + first_cell[0]
                for x, y in room_cells[1:]:
                    idx2 = y * width + x
                    uf.union(idx1, idx2)

        walls = []
        for y in range(depth):
            for x in range(width):
                if x + 1 < width:
                    walls.append(('V', x, y))
                if y + 1 < depth:
                    walls.append(('H', x, y))

        random.shuffle(walls)

        for wtype, wx, wy in walls:
            if wtype == 'V':
                idx1 = wy * width + wx
                idx2 = wy * width + (wx + 1)
                if uf.union(idx1, idx2):
                    cells[wy][wx][2] = False
                    cells[wy][wx + 1][3] = False
            else:
                idx1 = wy * width + wx
                idx2 = (wy + 1) * width + wx
                if uf.union(idx1, idx2):
                    cells[wy][wx][0] = False
                    cells[wy + 1][wx][1] = False

    elif algorithm == 'eller':
        row_sets = list(range(width))
        next_set_id = width

        for y in range(depth):
            for x in range(width - 1):
                if cell_to_room.get((x, y)) is not None and cell_to_room.get((x, y)) == cell_to_room.get((x + 1, y)):
                    s1 = row_sets[x]
                    s2 = row_sets[x + 1]
                    if s1 != s2:
                        for idx in range(width):
                            if row_sets[idx] == s2:
                                row_sets[idx] = s1
                        cells[y][x][2] = False
                        cells[y][x + 1][3] = False

            if y > 0:
                for x in range(width):
                    if cell_to_room.get((x, y)) is not None and cell_to_room.get((x, y)) == cell_to_room.get((x, y - 1)):
                        cells[y - 1][x][0] = False
                        cells[y][x][1] = False

            for x in range(width - 1):
                s1 = row_sets[x]
                s2 = row_sets[x + 1]
                if s1 != s2:
                    same_room = (cell_to_room.get((x, y)) is not None and 
                                 cell_to_room.get((x, y)) == cell_to_room.get((x + 1, y)))
                    if same_room or random.random() < eller_merge_prob:
                        for idx in range(width):
                            if row_sets[idx] == s2:
                                row_sets[idx] = s1
                        cells[y][x][2] = False
                        cells[y][x + 1][3] = False

            if y < depth - 1:
                next_row_sets = [None] * width
                set_groups = {}
                for x, s in enumerate(row_sets):
                    set_groups.setdefault(s, []).append(x)

                for s, group in set_groups.items():
                    room_up_cols = []
                    for col in group:
                        if (cell_to_room.get((col, y)) is not None and 
                            cell_to_room.get((col, y)) == cell_to_room.get((col, y + 1))):
                            room_up_cols.append(col)

                    if room_up_cols:
                        for col in room_up_cols:
                            cells[y][col][0] = False
                            cells[y + 1][col][1] = False
                            next_row_sets[col] = s
                    else:
                        random.shuffle(group)
                        num_carves = random.randint(1, len(group))
                        for i in range(num_carves):
                            col = group[i]
                            cells[y][col][0] = False
                            cells[y + 1][col][1] = False
                            next_row_sets[col] = s

                for x in range(width):
                    if next_row_sets[x] is None:
                        next_row_sets[x] = next_set_id
                        next_set_id += 1
                row_sets = next_row_sets

        y = depth - 1
        for x in range(width - 1):
            s1 = row_sets[x]
            s2 = row_sets[x + 1]
            if s1 != s2:
                for idx in range(width):
                    if row_sets[idx] == s2:
                        row_sets[idx] = s1
                cells[y][x][2] = False
                cells[y][x + 1][3] = False

    elif algorithm == 'binary_tree':
        for y in range(depth):
            for x in range(width):
                can_north = (y < depth - 1)
                can_east = (x < width - 1)
                
                if can_north and can_east:
                    if random.random() >= direction_bias:
                        cells[y][x][0] = False
                        cells[y + 1][x][1] = False
                    else:
                        cells[y][x][2] = False
                        cells[y][x + 1][3] = False
                elif can_north:
                    cells[y][x][0] = False
                    cells[y + 1][x][1] = False
                elif can_east:
                    cells[y][x][2] = False
                    cells[y][x + 1][3] = False



    elif algorithm == 'prims':
        visited = [[False] * width for _ in range(depth)]
        frontier_walls = []

        def add_frontier_of(cx, cy):
            """Add unvisited neighbours of (cx, cy) to the Prim's frontier wall list (2D mode)."""
            for dname, (dx, dy) in [('N', (0, 1)), ('S', (0, -1)), ('E', (1, 0)), ('W', (-1, 0))]:
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < width and 0 <= ny < depth:
                    if not visited[ny][nx]:
                        frontier_walls.append((cx, cy, nx, ny, dname))

        sx = random.randrange(width)
        sy = random.randrange(depth)
        
        r_start = cell_to_room.get((sx, sy))
        if r_start is not None:
            for rx, ry in rooms[r_start]:
                visited[ry][rx] = True
                add_frontier_of(rx, ry)
        else:
            visited[sy][sx] = True
            add_frontier_of(sx, sy)

        while frontier_walls:
            wall_idx = random.randrange(len(frontier_walls))
            x1, y1, x2, y2, dname = frontier_walls.pop(wall_idx)

            if visited[y1][x1] != visited[y2][x2]:
                ux, uy = (x2, y2) if not visited[y2][x2] else (x1, y1)
                
                cells[y1][x1][index[dname]] = False
                cells[y2][x2][index[opposites[dname]]] = False
                
                r_next = cell_to_room.get((ux, uy))
                if r_next is not None:
                    for rx, ry in rooms[r_next]:
                        if not visited[ry][rx]:
                            visited[ry][rx] = True
                            add_frontier_of(rx, ry)
                else:
                    visited[uy][ux] = True
                    add_frontier_of(ux, uy)

    elif algorithm == 'hunt_and_kill':
        visited = [[False] * width for _ in range(depth)]
        
        cx = random.randrange(width)
        cy = random.randrange(depth)
        
        def mark_visited(x, y):
            """Mark cell (x, y) as visited in 2D mode (Hunt-and-Kill)."""
            r = cell_to_room.get((x, y))
            if r is not None:
                for rx, ry in rooms[r]:
                    visited[ry][rx] = True
            else:
                visited[y][x] = True

        mark_visited(cx, cy)
        
        while True:
            walk_stuck = False
            last_dir = None
            while not walk_stuck:
                raw_dirs = [('N', (0, 1)), ('S', (0, -1)), ('E', (1, 0)), ('W', (-1, 0))]
                chosen_dir = None
                if last_dir and last_dir in raw_dirs:
                    _ldname, (ldx, ldy) = last_dir
                    nx, ny = cx + ldx, cy + ldy
                    if 0 <= nx < width and 0 <= ny < depth and not visited[ny][nx]:
                        if random.random() < straightness:
                            chosen_dir = last_dir
                if chosen_dir:
                    ordered_dirs = [chosen_dir] + [d for d in raw_dirs if d != chosen_dir]
                else:
                    random.shuffle(raw_dirs)
                    ordered_dirs = list(raw_dirs)
                moved = False
                for dname, (dx, dy) in ordered_dirs:
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < width and 0 <= ny < depth and not visited[ny][nx]:
                        cells[cy][cx][index[dname]] = False
                        cells[ny][nx][index[opposites[dname]]] = False
                        mark_visited(nx, ny)
                        cx, cy = nx, ny
                        last_dir = (dname, (dx, dy))
                        moved = True
                        break
                if not moved:
                    walk_stuck = True
            
            hunted = False
            for y in range(depth):
                for x in range(width):
                    if not visited[y][x]:
                        for dname, (dx, dy) in [('N', (0, 1)), ('S', (0, -1)), ('E', (1, 0)), ('W', (-1, 0))]:
                            nx, ny = x + dx, y + dy
                            if 0 <= nx < width and 0 <= ny < depth and visited[ny][nx]:
                                cells[y][x][index[dname]] = False
                                cells[ny][nx][index[opposites[dname]]] = False
                                mark_visited(x, y)
                                cx, cy = x, y
                                hunted = True
                                break
                    if hunted:
                        break
                if hunted:
                    break
            
            if not hunted:
                break

    elif algorithm == 'sidewinder':
        for y in range(depth):
            run = []
            for x in range(width):
                run.append(x)
                
                in_same_room = False
                if x + 1 < width:
                    r1 = cell_to_room.get((x, y))
                    r2 = cell_to_room.get((x + 1, y))
                    if r1 is not None and r1 == r2:
                        in_same_room = True

                carve_east = (x < width - 1) and (in_same_room or random.random() < east_bias)
                
                if carve_east:
                    cells[y][x][2] = False
                    cells[y][x + 1][3] = False
                else:
                    if y < depth - 1:
                        member_x = random.choice(run)
                        cells[y][member_x][0] = False
                        cells[y + 1][member_x][1] = False
                    run = []

    elif algorithm == 'wilsons':
        visited = [[False] * width for _ in range(depth)]
        unvisited_list = []
        
        def mark_visited(x, y):
            """Mark cell (x, y) as visited in 2D mode (Wilson's)."""
            r = cell_to_room.get((x, y))
            if r is not None:
                for rx, ry in rooms[r]:
                    visited[ry][rx] = True
            else:
                visited[y][x] = True

        sx = random.randrange(width)
        sy = random.randrange(depth)
        mark_visited(sx, sy)
        
        for y in range(depth):
            for x in range(width):
                if not visited[y][x]:
                    unvisited_list.append((x, y))
        
        while unvisited_list:
            unvisited_list = [(x, y) for (x, y) in unvisited_list if not visited[y][x]]
            if not unvisited_list:
                break
            
            cx, cy = random.choice(unvisited_list)
            walk = [(cx, cy)]
            walk_dirs = []
            
            while not visited[cy][cx]:
                dirs = [('N', (0, 1)), ('S', (0, -1)), ('E', (1, 0)), ('W', (-1, 0))]
                dname, (dx, dy) = random.choice(dirs)
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < width and 0 <= ny < depth:
                    if (nx, ny) in walk:
                        idx = walk.index((nx, ny))
                        walk = walk[:idx + 1]
                        walk_dirs = walk_dirs[:idx]
                    else:
                        walk.append((nx, ny))
                        walk_dirs.append(dname)
                    cx, cy = nx, ny
            
            for i in range(len(walk) - 1):
                x1, y1 = walk[i]
                dname = walk_dirs[i]
                x2, y2 = walk[i+1]
                cells[y1][x1][index[dname]] = False
                cells[y2][x2][index[opposites[dname]]] = False
                mark_visited(x1, y1)
            mark_visited(walk[-1][0], walk[-1][1])

    elif algorithm == 'recursive_division':
        for y in range(depth):
            for x in range(width):
                cells[y][x][0] = False
                cells[y][x][1] = False
                cells[y][x][2] = False
                cells[y][x][3] = False
        
        for x in range(width):
            cells[0][x][1] = True
            cells[depth - 1][x][0] = True
        for y in range(depth):
            cells[y][0][3] = True
            cells[y][width - 1][2] = True

        def divide(rx, ry, rw, rh, horizontal):
            """Recursively subdivide a rectangular region by placing walls (2D mode)."""
            if rw < 2 or rh < 2:
                return
            
            if horizontal:
                wy_sub = ry + random.randrange(rh - 1)
                px_sub = rx + _biased_choice(rw, passage_bias)
                
                for x_sub in range(rx, rx + rw):
                    if cell_to_room.get((x_sub, wy_sub)) is None or cell_to_room.get((x_sub, wy_sub + 1)) is None:
                        if x_sub != px_sub:
                            cells[wy_sub][x_sub][0] = True
                            cells[wy_sub + 1][x_sub][1] = True
                
                divide(rx, ry, rw, wy_sub - ry + 1, choose_orientation(rw, wy_sub - ry + 1))
                divide(rx, wy_sub + 1, rw, ry + rh - wy_sub - 1, choose_orientation(rw, ry + rh - wy_sub - 1))
            else:
                wx_sub = rx + random.randrange(rw - 1)
                py_sub = ry + _biased_choice(rh, passage_bias)
                
                for y_sub in range(ry, ry + rh):
                    if cell_to_room.get((wx_sub, y_sub)) is None or cell_to_room.get((wx_sub + 1, y_sub)) is None:
                        if y_sub != py_sub:
                            cells[y_sub][wx_sub][2] = True
                            cells[y_sub][wx_sub + 1][3] = True
                
                divide(rx, ry, wx_sub - rx + 1, rh, choose_orientation(wx_sub - rx + 1, rh))
                divide(wx_sub + 1, ry, rx + rw - wx_sub - 1, rh, choose_orientation(rx + rw - wx_sub - 1, rh))

        def choose_orientation(rw, rh):
            """Pick horizontal (True) or vertical (False) subdivision for recursive division (2D mode)."""
            if rw < rh:
                return True
            elif rh < rw:
                return False
            else:
                return random.random() < orientation_bias

        divide(0, 0, width, depth, choose_orientation(width, depth))

    elif algorithm == 'growing_tree':
        visited = [[False] * width for _ in range(depth)]
        active = []
        
        def add_to_active(x, y):
            """Mark cell as visited and add to the active list (2D mode)."""
            r = cell_to_room.get((x, y))
            if r is not None:
                for rx, ry in rooms[r]:
                    if not visited[ry][rx]:
                        visited[ry][rx] = True
                        active.append((rx, ry))
            else:
                visited[y][x] = True
                active.append((x, y))

        sx = random.randrange(width)
        sy = random.randrange(depth)
        add_to_active(sx, sy)
        
        while active:
            if random.random() < selection_bias:
                idx = random.randrange(len(active))
            else:
                idx = len(active) - 1
            
            cx, cy = active[idx]
            
            dirs = [('N', (0, 1)), ('S', (0, -1)), ('E', (1, 0)), ('W', (-1, 0))]
            random.shuffle(dirs)
            carved = False
            for dname, (dx, dy) in dirs:
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < width and 0 <= ny < depth and not visited[ny][nx]:
                    cells[cy][cx][index[dname]] = False
                    cells[ny][nx][index[opposites[dname]]] = False
                    add_to_active(nx, ny)
                    carved = True
                    break
            if not carved:
                active.pop(idx)

    if loop_probability > 0:
        for y in range(depth):
            for x in range(width):
                if y + 1 < depth and cells[y][x][0]:
                    if random.random() < loop_probability * 0.3:
                        cells[y][x][0] = False
                        cells[y + 1][x][1] = False
                if x + 1 < width and cells[y][x][2]:
                    if random.random() < loop_probability * 0.3:
                        cells[y][x][2] = False
                        cells[y][x + 1][3] = False

    if isolated_wall_prob > 0:
        max_isolated = int((width * depth) * isolated_wall_prob * 0.1)
        placed = 0
        for _ in range(max_isolated * 3):
            if placed >= max_isolated:
                break
            wx = random.randint(1, width - 2)
            wy = random.randint(1, depth - 2)
            is_horiz = random.random() < 0.5
            
            if is_horiz:
                cells[wy][wx][0] = True
                cells[wy + 1][wx][1] = True
                cells[wy][wx - 1][0] = False
                cells[wy + 1][wx - 1][1] = False
                cells[wy][wx + 1][0] = False
                cells[wy + 1][wx + 1][1] = False
                cells[wy][wx][2] = False
                cells[wy][wx + 1][3] = False
                cells[wy + 1][wx][2] = False
                cells[wy + 1][wx + 1][3] = False
                cells[wy][wx - 1][2] = False
                cells[wy][wx][3] = False
                cells[wy + 1][wx - 1][2] = False
                cells[wy + 1][wx][3] = False
            else:
                cells[wy][wx][2] = True
                cells[wy][wx + 1][3] = True
                cells[wy - 1][wx][2] = False
                cells[wy - 1][wx + 1][3] = False
                cells[wy + 1][wx][2] = False
                cells[wy + 1][wx + 1][3] = False
                cells[wy][wx][0] = False
                cells[wy + 1][wx][1] = False
                cells[wy][wx + 1][0] = False
                cells[wy + 1][wx + 1][1] = False
                cells[wy - 1][wx][0] = False
                cells[wy][wx][1] = False
                cells[wy - 1][wx + 1][0] = False
                cells[wy][wx + 1][1] = False
            placed += 1

    entrance_list = []
    exit_list = []

    def carve_borders(count, side, is_entrance):
        """Carve the specified number of entrance or exit openings along a maze border."""
        candidates = []
        if side == 'N' or side == 'ANY':
            for x in range(width):
                candidates.append((x, depth - 1, 'N'))
        if side == 'S' or side == 'ANY':
            for x in range(width):
                candidates.append((x, 0, 'S'))
        if side == 'E' or side == 'ANY':
            for y in range(depth):
                candidates.append((width - 1, y, 'E'))
        if side == 'W' or side == 'ANY':
            for y in range(depth):
                candidates.append((0, y, 'W'))

        random.shuffle(candidates)
        carved_count = 0
        for x, y, d in candidates:
            if blocked and blocked[y][x]:
                continue
            if carved_count >= count:
                break
            already_used = False
            for ex, ey, ed in entrance_list + exit_list:
                if ex == x and ey == y and ed == d:
                    already_used = True
                    break
            if already_used:
                continue

            cells[y][x][index[d]] = False
            if is_entrance:
                entrance_list.append((x, y, d))
            else:
                exit_list.append((x, y, d))
            carved_count += 1

        # If we still need more but ran out of unique cells, allow reuse/overlapping
        if carved_count < count:
            for x, y, d in candidates:
                if blocked and blocked[y][x]:
                    continue
                if carved_count >= count:
                    break
                cells[y][x][index[d]] = False
                if is_entrance:
                    entrance_list.append((x, y, d))
                else:
                    exit_list.append((x, y, d))
                carved_count += 1

    carve_borders(num_entrances, entrance_side, is_entrance=True)
    if mode == 'exit':
        carve_borders(num_exits, exit_side, is_entrance=False)
    else:
        if emergency_exits:
            num_ee = random.randint(1, min(3, (width + depth) // 4 + 1))
            carve_borders(num_ee, 'ANY', is_entrance=False)

    # Assign random floor, roof, and wall collection indices for thin mode
    for y in range(depth):
        for x in range(width):
            if num_floor_meshes > 0:
                cells[y][x][8] = random.randrange(num_floor_meshes)
            if num_roof_meshes > 0:
                cells[y][x][9] = random.randrange(num_roof_meshes)

    for y in range(depth):
        for x in range(width):
            if num_wall_meshes > 0:
                # North wall (index 4)
                if cells[y][x][4] == -1:
                    idx = random.randrange(num_wall_meshes)
                    cells[y][x][4] = idx
                    if y + 1 < depth:
                        cells[y + 1][x][5] = idx
                # South wall (index 5)
                if cells[y][x][5] == -1:
                    idx = random.randrange(num_wall_meshes)
                    cells[y][x][5] = idx
                    if y - 1 >= 0:
                        cells[y - 1][x][4] = idx
                # East wall (index 6)
                if cells[y][x][6] == -1:
                    idx = random.randrange(num_wall_meshes)
                    cells[y][x][6] = idx
                    if x + 1 < width:
                        cells[y][x + 1][7] = idx
                # West wall (index 7)
                if cells[y][x][7] == -1:
                    idx = random.randrange(num_wall_meshes)
                    cells[y][x][7] = idx
                    if x - 1 >= 0:
                        cells[y][x - 1][6] = idx

    main_entrance = entrance_list[0] if entrance_list else (0, 0, 'S')
    main_exits = exit_list
    center = (width // 2, depth // 2)

    # Expand to 3D for multilevel
    if floors > 1:
        cells, stairs_placed = _expand_cells_to_3d(
            cells, width, depth, floors, wall_mode,
            stair_count=stair_count, stair_footprint=stair_footprint,
            stair_style=stair_style, stair_direction=stair_direction
        )
    else:
        cells = [cells]
        stairs_placed = []

    maze_data = MazeData(width, depth, cells, main_entrance, main_exits, center)
    maze_data.floors = floors
    maze_data.stairs = stairs_placed
    if mask_image:
        blocked = _get_image_mask_data(mask_image, mask_invert, width, depth)
        for y in range(depth):
            for x in range(width):
                if blocked[y][x]:
                    cells[0][y][x][8] = -2
                    cells[0][y][x][9] = -2
                    cells[0][y][x][0] = cells[0][y][x][1] = cells[0][y][x][2] = cells[0][y][x][3] = False
                    
                    if y + 1 < depth and not blocked[y+1][x]:
                        cells[0][y+1][x][1] = True
                    if y - 1 >= 0 and not blocked[y-1][x]:
                        cells[0][y-1][x][0] = True
                    if x + 1 < width and not blocked[y][x+1]:
                        cells[0][y][x+1][3] = True
                    if x - 1 >= 0 and not blocked[y][x-1]:
                        cells[0][y][x-1][2] = True
        # Filter entrances and exits to exclude masked cells
        entrance_list = [item for item in entrance_list if not blocked[item[1]][item[0]]]
        exit_list = [item for item in exit_list if not blocked[item[1]][item[0]]]
        maze_data.entrance = entrance_list[0] if entrance_list else (0, 0, 'S')
        maze_data.exits = exit_list
                        
    maze_data.guide_path = find_shortest_path(maze_data, wall_mode='thin')
    return maze_data




def generate_maze(
    width: int,
    depth: int,
    seed: int = 0,
    mode: str = 'center',
    emergency_exits: bool = False,
    algorithm: str = 'dfs',
    rooms_enable: bool = False,
    rooms_count: int = 3,
    min_room_size: int = 2,
    max_room_size: int = 4,
    loop_probability: float = 0.0,
    isolated_wall_prob: float = 0.0,
    entrance_side: str = 'ANY',
    exit_side: str = 'ANY',
    num_entrances: int = 1,
    num_exits: int = 1,
    wall_mode: str = 'thin',
    num_wall_meshes: int = 0,
    num_floor_meshes: int = 0,
    num_roof_meshes: int = 0,
    mask_image = None,
    mask_invert: bool = False,
    grid_type: str = 'rect',
    polar_rings: int = 5,
    floors: int = 1,
    stair_footprint: str = '1x1',
    stair_style: str = 'stair',
    stair_count: int = 1,
    stair_direction: str = 'random',
    selection_bias: float = 0.5,
    straightness: float = 0.5,
    direction_bias: float = 0.5,
    east_bias: float = 0.5,
    orientation_bias: float = 0.5,
    passage_bias: float = 0.5,
    eller_merge_prob: float = 0.5,
    radial_bias: float = 0.5,
) -> MazeData:
    """Generate a complete rectangular or polar maze with the selected algorithm."""
    # Disable mask for multilevel mazes
    if floors > 1 and mask_image is not None:
        mask_image = None

    if grid_type == 'polar':
        return generate_polar_maze(
            rings=polar_rings,
            seed=seed,
            algorithm=algorithm,
            mode=mode,
            wall_mode=wall_mode,
            num_wall_meshes=num_wall_meshes,
            num_floor_meshes=num_floor_meshes,
            num_roof_meshes=num_roof_meshes,
            floors=floors,
            stair_footprint=stair_footprint,
            stair_style=stair_style,
            stair_count=stair_count,
            stair_direction=stair_direction,
            radial_bias=radial_bias,
        )

    if seed:
        random.seed(seed)

    if wall_mode == 'cube':
        return _generate_cube_maze(
            width=width, depth=depth, seed=seed, mode=mode,
            emergency_exits=emergency_exits, algorithm=algorithm,
            rooms_enable=rooms_enable, rooms_count=rooms_count,
            min_room_size=min_room_size, max_room_size=max_room_size,
            loop_probability=loop_probability, isolated_wall_prob=isolated_wall_prob,
            entrance_side=entrance_side, exit_side=exit_side,
            num_entrances=num_entrances, num_exits=num_exits,
            num_wall_meshes=num_wall_meshes, num_floor_meshes=num_floor_meshes,
            num_roof_meshes=num_roof_meshes, mask_image=mask_image,
            mask_invert=mask_invert, floors=floors, stair_footprint=stair_footprint,
            stair_style=stair_style, stair_count=stair_count, stair_direction=stair_direction,
            selection_bias=selection_bias, straightness=straightness,
            direction_bias=direction_bias, east_bias=east_bias,
            orientation_bias=orientation_bias, passage_bias=passage_bias,
            eller_merge_prob=eller_merge_prob
        )
    else:
        return _generate_thin_maze(
            width=width, depth=depth, seed=seed, mode=mode,
            emergency_exits=emergency_exits, algorithm=algorithm,
            rooms_enable=rooms_enable, rooms_count=rooms_count,
            min_room_size=min_room_size, max_room_size=max_room_size,
            loop_probability=loop_probability, isolated_wall_prob=isolated_wall_prob,
            entrance_side=entrance_side, exit_side=exit_side,
            num_entrances=num_entrances, num_exits=num_exits,
            num_wall_meshes=num_wall_meshes, num_floor_meshes=num_floor_meshes,
            num_roof_meshes=num_roof_meshes, mask_image=mask_image,
            mask_invert=mask_invert, floors=floors, stair_footprint=stair_footprint,
            stair_style=stair_style, stair_count=stair_count, stair_direction=stair_direction,
            selection_bias=selection_bias, straightness=straightness,
            direction_bias=direction_bias, east_bias=east_bias,
            orientation_bias=orientation_bias, passage_bias=passage_bias,
            eller_merge_prob=eller_merge_prob
        )


def _get_polar_neighbors(r, theta, rings, ring_sectors):
    """Return the (r, theta) neighbours of a polar cell on a circular grid."""
    neighbors = []
    Nr = ring_sectors[r]
    if r >= 1:
        neighbors.append((r, (theta + 1) % Nr))
        neighbors.append((r, (theta - 1) % Nr))
    if r > 0:
        N_in = ring_sectors[r - 1]
        if N_in == Nr:
            neighbors.append((r - 1, theta))
        elif N_in == 1:
            neighbors.append((r - 1, 0))
        else:
            neighbors.append((r - 1, theta // 2))
    if r < rings - 1:
        N_out = ring_sectors[r + 1]
        if N_out == Nr:
            neighbors.append((r + 1, theta))
        elif Nr == 1:
            for t in range(N_out):
                neighbors.append((r + 1, t))
        else:
            neighbors.append((r + 1, 2 * theta))
            neighbors.append((r + 1, 2 * theta + 1))
    return neighbors


def _find_shortest_path_polar_3d(maze_data, wall_mode, cells_3d):
    """3D BFS for polar grid over (z, r, theta) with vertical stair edges.

    Args:
        maze_data: MazeData instance.
        wall_mode: 'thin' or 'cube'.
        cells_3d: 3D cell list [z][r][theta].

    Returns:
        List of (z, r, theta) tuples representing the shortest path, or [].
    """
    start_r = maze_data.entrance[0]
    start_theta = maze_data.entrance[1]
    start_z = 0
    floors = maze_data.floors
    rings = maze_data.polar_rings
    ring_sectors = maze_data.ring_sectors

    # Build stair edge map: (z, r, theta) -> (z+1, r, theta)
    stair_up = {}
    stair_down = {}
    for s in maze_data.stairs:
        sz = s['z']
        sx, sy = s['x'], s['y']
        stheta, sr = sx, sy
        if sy >= rings and sx < rings:
            stheta, sr = sy, sx
        key_up = (sz, sr, stheta)
        key_down = (sz + 1, sr, stheta)
        stair_up[key_up] = (sz + 1, sr, stheta)
        stair_down[key_down] = (sz, sr, stheta)

    # Goal: exits on top floor, or center of top floor
    if maze_data.exits:
        targets = [(floors - 1, ex[0], ex[1]) for ex in maze_data.exits]
    else:
        cr, ctheta = maze_data.center
        targets = [(floors - 1, cr, ctheta)]

    queue = deque([(start_z, start_r, start_theta)])
    parent = {(start_z, start_r, start_theta): None}
    while queue:
        cz, cr, ctheta = queue.popleft()
        node = (cz, cr, ctheta)
        if node in targets:
            path = []
            curr = node
            while curr is not None:
                path.append(curr)
                curr = parent[curr]
            path.reverse()
            return path

        Nr = ring_sectors[cr]
        accessible = []
        if wall_mode == 'cube':
            for nr, ntheta in _get_polar_neighbors(cr, ctheta, rings, ring_sectors):
                if not cells_3d[cz][nr][ntheta][0]:
                    accessible.append((nr, ntheta))
        else:
            if cr >= 1 and not cells_3d[cz][cr][ctheta][0]:
                accessible.append((cr, (ctheta + 1) % Nr))
            if cr >= 1 and not cells_3d[cz][cr][(ctheta - 1) % Nr][0]:
                accessible.append((cr, (ctheta - 1) % Nr))
            if cr > 0 and not cells_3d[cz][cr][ctheta][1]:
                N_in = ring_sectors[cr - 1]
                if N_in == Nr:
                    accessible.append((cr - 1, ctheta))
                elif N_in == 1:
                    accessible.append((cr - 1, 0))
                else:
                    accessible.append((cr - 1, ctheta // 2))
            if cr < rings - 1:
                N_out = ring_sectors[cr + 1]
                if N_out == Nr:
                    if not cells_3d[cz][cr + 1][ctheta][1]:
                        accessible.append((cr + 1, ctheta))
                elif Nr == 1:
                    for t in range(N_out):
                        if not cells_3d[cz][cr + 1][t][1]:
                            accessible.append((cr + 1, t))
                else:
                    if not cells_3d[cz][cr + 1][2 * ctheta][1]:
                        accessible.append((cr + 1, 2 * ctheta))
                    if not cells_3d[cz][cr + 1][2 * ctheta + 1][1]:
                        accessible.append((cr + 1, 2 * ctheta + 1))

        for nr, ntheta in accessible:
            nn = (cz, nr, ntheta)
            if nn not in parent:
                parent[nn] = node
                queue.append(nn)

        # Vertical stair neighbors
        if node in stair_up:
            nn = stair_up[node]
            if nn not in parent:
                parent[nn] = node
                queue.append(nn)
        if node in stair_down:
            nn = stair_down[node]
            if nn not in parent:
                parent[nn] = node
                queue.append(nn)

    return []



def find_shortest_path(maze_data: MazeData, wall_mode: str) -> List:
    """Find the shortest path from entrance to exit/center using BFS.

    Dispatches to the appropriate 2D/3D and rect/polar pathfinder based on
    maze_data.grid_type and floor count.

    Args:
        maze_data: MazeData instance.
        wall_mode: 'thin' or 'cube'.

    Returns:
        List of coordinate tuples (2D or 3D) representing the path, or [].
    """
    if not maze_data.entrance:
        return []

    cells_3d, floors = _resolve_cells_3d(maze_data)
    is_3d = (floors > 1)

    if maze_data.grid_type == 'polar':
        if is_3d:
            return _find_shortest_path_polar_3d(maze_data, wall_mode, cells_3d)
        return _find_shortest_path_polar_2d(maze_data, wall_mode)

    if is_3d:
        return _find_shortest_path_3d(maze_data, wall_mode, cells_3d)

    # 2D rect BFS (back-compat with old 2D cells layout)
    return _find_shortest_path_2d(maze_data, wall_mode, cells_3d[0])


def _find_shortest_path_2d(maze_data, wall_mode, cells_2d):
    """2D BFS for rectangular grid (y, x) with wall-aware neighbour traversal.

    Args:
        maze_data: MazeData instance.
        wall_mode: 'thin' or 'cube'.
        cells_2d: 2D cell list [y][x].

    Returns:
        List of (x, y) tuples, or [].
    """
    start_x, start_y, _ = maze_data.entrance
    targets = []
    if maze_data.exits:
        for ex, ey, _ in maze_data.exits:
            targets.append((ex, ey))
    else:
        targets.append(maze_data.center)
    if not targets:
        return []

    queue = deque([(start_x, start_y)])
    parent = {(start_x, start_y): None}
    dirs = [('N', 0, 1, 0), ('S', 0, -1, 1), ('E', 1, 0, 2), ('W', -1, 0, 3)]

    while queue:
        cx, cy = queue.popleft()
        if (cx, cy) in targets:
            path = []
            curr = (cx, cy)
            while curr is not None:
                path.append(curr)
                curr = parent[curr]
            path.reverse()
            return path
        for dname, dx, dy, wall_idx in dirs:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < maze_data.width and 0 <= ny < maze_data.depth:
                nn = (nx, ny)
                if nn not in parent:
                    if wall_mode == 'cube':
                        if not cells_2d[ny][nx][0]:
                            parent[nn] = (cx, cy)
                            queue.append(nn)
                    else:
                        if not cells_2d[cy][cx][wall_idx]:
                            parent[nn] = (cx, cy)
                            queue.append(nn)
    return []


def _find_shortest_path_3d(maze_data, wall_mode, cells_3d):
    """3D BFS over (z, y, x) with vertical stair edges.

    Args:
        maze_data: MazeData instance.
        wall_mode: 'thin' or 'cube'.
        cells_3d: 3D cell list [z][y][x].

    Returns:
        List of (z, y, x) tuples, or [].
    """
    start_x, start_y, _ = maze_data.entrance
    start_z = 0
    floors = maze_data.floors

    # Build stair edge map: (z, y, x) -> (z+1, y, x)
    stair_up = {}
    stair_down = {}
    for s in maze_data.stairs:
        sz = s['z']
        sx, sy = s['x'], s['y']
        fp = s.get('footprint', '1x1')
        orient = s.get('orientation', 'N')
        coords = _get_stair_footprint_coords(sx, sy, fp, orient)
        for cx, cy in coords:
            if 0 <= cy < maze_data.depth and 0 <= cx < maze_data.width:
                key_up = (sz, cy, cx)
                key_down = (sz + 1, cy, cx)
                stair_up[key_up] = (sz + 1, cy, cx)
                stair_down[key_down] = (sz, cy, cx)

    # Goal: center on top floor
    if maze_data.exits:
        targets = [(floors - 1, ey, ex) for ex, ey, _ in maze_data.exits]
    else:
        cx, cy = maze_data.center
        targets = [(floors - 1, cy, cx)]

    queue = deque([(start_z, start_y, start_x)])
    parent = {(start_z, start_y, start_x): None}
    dirs = [('N', 1, 0, 0), ('S', -1, 0, 1), ('E', 0, 1, 2), ('W', 0, -1, 3)]

    while queue:
        cz, cy, cx = queue.popleft()
        node = (cz, cy, cx)
        if node in targets:
            path = []
            curr = node
            while curr is not None:
                path.append(curr)
                curr = parent[curr]
            path.reverse()
            return path

        # Cardinal neighbors in current floor
        for dname, dy, dx, wall_idx in dirs:
            ny, nx = cy + dy, cx + dx
            if 0 <= nx < maze_data.width and 0 <= ny < maze_data.depth:
                nn = (cz, ny, nx)
                if nn not in parent:
                    if wall_mode == 'cube':
                        if not cells_3d[cz][ny][nx][0]:
                            parent[nn] = node
                            queue.append(nn)
                    else:
                        if not cells_3d[cz][cy][cx][wall_idx]:
                            parent[nn] = node
                            queue.append(nn)

        # Vertical stair neighbors
        if node in stair_up:
            nn = stair_up[node]
            if nn not in parent:
                parent[nn] = node
                queue.append(nn)
        if node in stair_down:
            nn = stair_down[node]
            if nn not in parent:
                parent[nn] = node
                queue.append(nn)

    return []



def _find_shortest_path_polar_2d(maze_data, wall_mode):
    """2D BFS for polar grid (r, theta) with ring-aware neighbour traversal.

    Args:
        maze_data: MazeData instance.
        wall_mode: 'thin' or 'cube'.

    Returns:
        List of (r, theta) tuples, or [].
    """
    start = maze_data.entrance[0:2]
    targets = []
    if maze_data.exits:
        for ex_r, ex_theta, _ in maze_data.exits:
            targets.append((ex_r, ex_theta))
    else:
        targets.append(maze_data.center)
    if not targets:
        return []
    queue = deque([start])
    parent = {start: None}
    rings = maze_data.polar_rings
    ring_sectors = maze_data.ring_sectors

    cells_3d, _ = _resolve_cells_3d(maze_data)
    cells_2d = cells_3d[0]

    while queue:
        node = queue.popleft()
        if node in targets:
            path = []
            curr = node
            while curr is not None:
                path.append(curr)
                curr = parent[curr]
            path.reverse()
            return path
        r, theta = node
        Nr = ring_sectors[r]
        accessible = []
        if wall_mode == 'cube':
            for nr, ntheta in _get_polar_neighbors(r, theta, rings, ring_sectors):
                if not cells_2d[nr][ntheta][0]:
                    accessible.append((nr, ntheta))
        else:
            if r >= 1 and not cells_2d[r][theta][0]:
                accessible.append((r, (theta + 1) % Nr))
            if r >= 1 and not cells_2d[r][(theta - 1) % Nr][0]:
                accessible.append((r, (theta - 1) % Nr))
            if r > 0 and not cells_2d[r][theta][1]:
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
                    if not cells_2d[r + 1][theta][1]:
                        accessible.append((r + 1, theta))
                elif Nr == 1:
                    for t in range(N_out):
                        if not cells_2d[r + 1][t][1]:
                            accessible.append((r + 1, t))
                else:
                    if not cells_2d[r + 1][2 * theta][1]:
                        accessible.append((r + 1, 2 * theta))
                    if not cells_2d[r + 1][2 * theta + 1][1]:
                        accessible.append((r + 1, 2 * theta + 1))
        for neighbor in accessible:
            if neighbor not in parent:
                parent[neighbor] = node
                queue.append(neighbor)
    return []



def generate_polar_maze(
    rings: int,
    seed: int = 0,
    algorithm: str = 'dfs',
    mode: str = 'center',
    wall_mode: str = 'thin',
    num_wall_meshes: int = 0,
    num_floor_meshes: int = 0,
    num_roof_meshes: int = 0,
    floors: int = 1,
    stair_footprint: str = '1x1',
    stair_style: str = 'stair',
    stair_count: int = 1,
    stair_direction: str = 'random',
    radial_bias: float = 0.5,
) -> MazeData:
    """Generate a polar (circular) maze.

    Only DFS has a native polar implementation; other algorithms fall back
    to a random spanning tree (Kruskal-style) for equivalent results.

    Args:
        rings: Number of concentric rings.
        seed: Random seed (0 for time-based).
        algorithm: Algorithm name ('dfs' or other).
        mode: 'center' or 'exit'.
        wall_mode: 'thin' or 'cube'.
        num_wall_meshes: Count for wall collection index assignment.
        num_floor_meshes: Count for floor collection index assignment.
        num_roof_meshes: Count for roof collection index assignment.
        floors: Number of vertical levels.
        stair_footprint: Stair footprint (polar supports only '1x1').
        stair_style: 'stair' or 'ramp'.
        stair_count: Number of stairs per floor transition.
        radial_bias: 0 = prefer tangential, 1 = prefer radial movements.

    Returns:
        A fully populated MazeData instance for a polar grid.
    """
    if seed:
        random.seed(seed)

    # Only DFS has a dedicated polar implementation; all other algorithms use a
    # random spanning tree (Kruskal-style) which produces similar perfect-maze results.
    _POLAR_NATIVE_ALGORITHMS = {'dfs'}
    if algorithm not in _POLAR_NATIVE_ALGORITHMS:
        logger.info(f"Algorithm '{algorithm}' is not natively implemented for polar "
                    f"grids — using random spanning tree (similar to Kruskal) instead.")

    if wall_mode == 'cube' and rings % 2 == 1:
        rings += 1

    # Compute sector counts Nr for each ring r
    ring_sectors = [1] # r=0 has 1 center cell
    for r in range(1, rings):
        power = int(math.log2(r))
        sectors = 6 * (2 ** power)
        ring_sectors.append(sectors)

    # Setup cells
    cells = []
    if wall_mode == 'cube':
        for r in range(rings):
            row = []
            for theta in range(ring_sectors[r]):
                if r == 0:
                    is_wall = False
                elif r % 2 == 1:
                    is_wall = True
                else:
                    is_wall = (theta % 2 == 0)
                row.append([is_wall, False, -1, -1, -1, -1, -1, -1, -1])
            cells.append(row)
    else:
        for r in range(rings):
            row = []
            for theta in range(ring_sectors[r]):
                if r == 0:
                    row.append([False, False, -1, -1, -1, -1, -1])
                else:
                    row.append([True, True, -1, -1, -1, -1, -1])
            cells.append(row)


    if wall_mode == 'cube':
        # 1. Build Passage Cells and Bidirectional graph
        passage_cells = []
        for r in range(0, rings, 2):
            if r == 0:
                passage_cells.append((0, 0))
            else:
                for theta in range(1, ring_sectors[r], 2):
                    passage_cells.append((r, theta))

        graph = {cell: [] for cell in passage_cells}

        for r, theta in passage_cells:
            # Angular neighbors
            if r >= 2:
                Nr = ring_sectors[r]
                for dt in [2, -2]:
                    ntheta = (theta + dt) % Nr
                    int_theta = (theta + (dt // 2)) % Nr
                    neighbor = (r, ntheta)
                    int_cell = (r, int_theta)
                    if neighbor in graph:
                        graph[(r, theta)].append((neighbor, int_cell))

            # Inward radial neighbors
            if r == 2:
                neighbor = (0, 0)
                int_cell = (1, theta // 2)
                if neighbor in graph:
                    graph[(r, theta)].append((neighbor, int_cell))
                    graph[neighbor].append(((r, theta), int_cell))
            elif r > 2:
                N_in = ring_sectors[r - 2]
                if N_in == ring_sectors[r]:
                    neighbor = (r - 2, theta)
                    int_cell = (r - 1, theta)
                    if neighbor in graph:
                        graph[(r, theta)].append((neighbor, int_cell))
                        graph[neighbor].append(((r, theta), int_cell))
                elif N_in == ring_sectors[r] // 2:
                    t = theta // 2
                    if t % 2 == 1:
                        neighbor = (r - 2, t)
                        int_cell = (r - 1, t)
                        if neighbor in graph:
                            graph[(r, theta)].append((neighbor, int_cell))
                            graph[neighbor].append(((r, theta), int_cell))

        # 2. Carve Spanning Tree on the Passage Graph
        if algorithm == 'dfs':
            visited = {cell: False for cell in passage_cells}
            start_cell = (rings - 2, 1) if rings > 2 else (0, 0)
            visited[start_cell] = True
            stack = [start_cell]
            while stack:
                cell = stack[-1]
                unvisited = [edge for edge in graph[cell] if not visited[edge[0]]]
                if unvisited:
                    radial = [edge for edge in unvisited if edge[0][0] != cell[0]]
                    tangential = [edge for edge in unvisited if edge[0][0] == cell[0]]
                    if radial and tangential:
                        if random.random() < radial_bias:
                            neighbor, int_cell = random.choice(radial)
                        else:
                            neighbor, int_cell = random.choice(tangential)
                    elif radial:
                        neighbor, int_cell = random.choice(radial)
                    else:
                        neighbor, int_cell = random.choice(tangential)
                    ir, itheta = int_cell
                    cells[ir][itheta][0] = False
                    visited[neighbor] = True
                    stack.append(neighbor)
                else:
                    stack.pop()
        else:
            edges = []
            seen_edges = set()
            for u in graph:
                for v, int_cell in graph[u]:
                    edge_key = tuple(sorted([u, v]))
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        edges.append((u, v, int_cell))
            random.shuffle(edges)
            uf = UnionFind(len(passage_cells))
            cell_to_idx = {cell: idx for idx, cell in enumerate(passage_cells)}
            for u, v, int_cell in edges:
                id1 = cell_to_idx[u]
                id2 = cell_to_idx[v]
                if uf.union(id1, id2):
                    ir, itheta = int_cell
                    cells[ir][itheta][0] = False

    else:
        total_cells = sum(ring_sectors)
        if algorithm == 'dfs':
            visited = [[False] * ring_sectors[r] for r in range(rings)]
            visited[0][0] = True
            stack = [(0, 0)]
            while stack:
                r, theta = stack[-1]
                neighbors = _get_polar_neighbors(r, theta, rings, ring_sectors)
                unvisited = [n for n in neighbors if not visited[n[0]][n[1]]]
                if unvisited:
                    radial = [n for n in unvisited if n[0] != r]
                    angular = [n for n in unvisited if n[0] == r]
                    if radial and angular:
                        if random.random() < radial_bias:
                            nr, ntheta = random.choice(radial)
                        else:
                            nr, ntheta = random.choice(angular)
                    elif radial:
                        nr, ntheta = random.choice(radial)
                    else:
                        nr, ntheta = random.choice(angular)
                    if nr == r:
                        Nr = ring_sectors[r]
                        if ntheta == (theta + 1) % Nr:
                            cells[r][theta][0] = False
                        else:
                            cells[r][ntheta][0] = False
                    elif nr == r - 1:
                        cells[r][theta][1] = False
                    elif nr == r + 1:
                        cells[nr][ntheta][1] = False
                    visited[nr][ntheta] = True
                    stack.append((nr, ntheta))
                else:
                    stack.pop()

        elif algorithm in ('kruskal', 'prims', 'eller', 'binary_tree', 'hunt_and_kill',
                           'sidewinder', 'wilsons', 'recursive_division', 'growing_tree'):
            def get_id(r, theta):
                """Return a unique cell ID for a polar (r, theta) coordinate."""
                return sum(ring_sectors[:r]) + theta
            uf = UnionFind(total_cells)
            walls = []
            for r in range(1, rings):
                for theta in range(ring_sectors[r]):
                    walls.append((r, theta, 'CW'))
                    walls.append((r, theta, 'IN'))
            random.shuffle(walls)
            for r, theta, wtype in walls:
                Nr = ring_sectors[r]
                if wtype == 'CW':
                    r2, theta2 = r, (theta + 1) % Nr
                else:
                    r2 = r - 1
                    N_in = ring_sectors[r2]
                    if N_in == Nr:
                        theta2 = theta
                    elif N_in == 1:
                        theta2 = 0
                    else:
                        theta2 = theta // 2
                id1 = get_id(r, theta)
                id2 = get_id(r2, theta2)
                if uf.union(id1, id2):
                    if wtype == 'CW':
                        cells[r][theta][0] = False
                    else:
                        cells[r][theta][1] = False

    if wall_mode == 'cube':
        entrance = (rings - 1, 1, 'OUT')
        if mode == 'center':
            exits = [(0, 0, 'CENTER')]
        else:
            ex_theta = (ring_sectors[rings - 1] // 2) | 1
            exits = [(rings - 1, ex_theta, 'OUT')]
    else:
        entrance = (rings - 1, 0, 'OUT')
        if mode == 'center':
            exits = [(0, 0, 'CENTER')]
        else:
            exits = [(rings - 1, ring_sectors[rings - 1] // 2, 'OUT')]

    if wall_mode == 'cube':
        cells[entrance[0]][entrance[1]][0] = False
        for ex in exits:
            cells[ex[0]][ex[1]][0] = False

    guide_path = []

    for r in range(rings):
        for theta in range(ring_sectors[r]):
            if num_floor_meshes > 0:
                cells[r][theta][4] = random.randrange(num_floor_meshes)
            if num_roof_meshes > 0:
                cells[r][theta][5] = random.randrange(num_roof_meshes)
            if num_wall_meshes > 0:
                cells[r][theta][2] = random.randrange(num_wall_meshes)
                cells[r][theta][3] = random.randrange(num_wall_meshes)
                if len(cells[r][theta]) > 6:
                    cells[r][theta][6] = random.randrange(num_wall_meshes)
                if len(cells[r][theta]) > 7:
                    cells[r][theta][7] = random.randrange(num_wall_meshes)
                if len(cells[r][theta]) > 8:
                    cells[r][theta][8] = random.randrange(num_wall_meshes)

    # Expand to 3D for multilevel (polar supports only 1x1 footprints)
    if floors > 1:
        stair_defs = []
        for z in range(floors - 1):
            candidates = []
            for r in range(1, rings):
                for theta in range(ring_sectors[r]):
                    candidates.append((r, theta))
            random.shuffle(candidates)
            num_stairs = max(1, min(stair_count, len(candidates)))
            placed_cells_z = set()  # track (r, theta) cells placed as stairs on this floor
            placed_count = 0
            for r, theta in candidates:
                if placed_count >= num_stairs:
                    break
                if (r, theta) in placed_cells_z:
                    continue
                placed_cells_z.add((r, theta))
                polar_orient = (random.choice(['IN', 'OUT', 'CW', 'CCW']) if stair_direction == 'random'
                                else {'N': 'CCW', 'E': 'OUT', 'S': 'CW', 'W': 'IN'}.get(stair_direction, stair_direction))
                stair_defs.append({
                    'z': z, 'x': theta, 'y': r,
                    'type': stair_style,
                    'footprint': '1x1',
                    'orientation': polar_orient,
                })
                placed_count += 1
        # For polar, we can't use the rect _expand_cells_to_3d directly
        # since cell indexing is different. Instead we clone the 2D cells list.
        cells_3d = [cells]
        for _ in range(1, floors):
            cells_3d.append(copy.deepcopy(cells))
        # Force stair footprint cells open on both levels
        for s in stair_defs:
            sz = s['z']
            sr = s['y']  # ring
            st = s['x']  # theta
            if wall_mode == 'cube':
                for cz in (sz, sz + 1):
                    if 0 <= cz < floors and 0 <= sr < rings:
                        if 0 <= st < len(cells_3d[cz][sr]):
                            cells_3d[cz][sr][st][0] = False
            else:
                for cz in (sz, sz + 1):
                    if 0 <= cz < floors and 0 <= sr < rings:
                        if 0 <= st < len(cells_3d[cz][sr]):
                            cells_3d[cz][sr][st][0] = False  # CW wall
                            cells_3d[cz][sr][st][1] = False  # IN wall
        cells = cells_3d
        stairs_placed = stair_defs
    else:
        cells = [cells]
        stairs_placed = []

    maze_data = MazeData(
        width=rings,
        depth=rings,
        cells=cells,
        entrance=entrance,
        exits=exits,
        center=(0, 0),
        guide_path=guide_path,
        grid_type='polar',
        polar_rings=rings,
        ring_sectors=ring_sectors,
        floors=floors,
        stairs=stairs_placed,
    )
    maze_data.guide_path = find_shortest_path(maze_data, wall_mode=wall_mode)
    return maze_data
