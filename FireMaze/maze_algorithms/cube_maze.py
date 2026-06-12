"""Cube maze algorithm for FireMaze."""

from typing import List, Tuple
from ..maze_data import MazeData, UnionFind
from ..pathfinder import find_shortest_path
from ..utils import get_rng
from .common_helpers import (
    _biased_choice,
    _expand_cells_to_3d,
    _get_image_mask_data,
    _get_start_cell,
)

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
    random = get_rng()
    
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
                if blocked:
                    room_blocked = False
                    for y in range(ry, ry + rh):
                        for x in range(rx, rx + rw):
                            if blocked[y][x]:
                                room_blocked = True
                                break
                        if room_blocked:
                            break
                    if room_blocked:
                        continue

                room_cells = []
                r_idx = len(rooms)
                for y in range(ry, ry + rh):
                    for x in range(rx, rx + rw):
                        room_cells.append((x, y))
                        cell_to_room[(x, y)] = r_idx
                rooms.append(room_cells)

        # Carve room regions on the actual grid (make them completely floor/False)
        for _, room_cells in enumerate(rooms):
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
                        sub_x = (gx - 1) // 2
                        sub_y = (gy - 1) // 2
                        if blocked and 0 <= sub_x < sub_w and 0 <= sub_y < sub_h and blocked[sub_y][sub_x]:
                            continue
                        cells[gy][gx][0] = False

    # Run maze carving algorithms directly on the path cells
    if algorithm == 'dfs':
        visited = [[blocked[y][x] for x in range(sub_w)] for y in range(sub_h)]
        stack = []
        
        # Start cell
        start_cell = _get_start_cell(blocked, sub_w, sub_h)
        if start_cell is None:
            raise ValueError("All cells are blocked; cannot generate maze")
        sx, sy = start_cell

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
                if blocked[y][x]:
                    continue
                cells[2 * y + 1][2 * x + 1][0] = False  # Ensure cell center is floor
                if x + 1 < sub_w and not blocked[y][x + 1]:
                    walls.append(('V', x, y))
                if y + 1 < sub_h and not blocked[y + 1][x]:
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
            # Ensure all non-blocked row cell centers are floor
            for x in range(sub_w):
                if not blocked[y][x]:
                    cells[2 * y + 1][2 * x + 1][0] = False

            for x in range(sub_w - 1):
                if blocked[y][x] or blocked[y][x + 1]:
                    continue
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
                    if blocked[y][x] or blocked[y - 1][x]:
                        continue
                    if cell_to_room.get((x, y)) is not None and cell_to_room.get((x, y)) == cell_to_room.get((x, y - 1)):
                        cells[2 * y][2 * x + 1][0] = False

            for x in range(sub_w - 1):
                if blocked[y][x] or blocked[y][x + 1]:
                    continue
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
                    if not blocked[y][x]:
                        set_groups.setdefault(s, []).append(x)

                for s, group in set_groups.items():
                    valid_group = [col for col in group if not blocked[y + 1][col]]
                    room_up_cols = []
                    for col in valid_group:
                        if (cell_to_room.get((col, y)) is not None and 
                            cell_to_room.get((col, y)) == cell_to_room.get((col, y + 1))):
                            room_up_cols.append(col)

                    if room_up_cols:
                        for col in room_up_cols:
                            cells[2 * y + 2][2 * col + 1][0] = False
                            cells[2 * y + 3][2 * col + 1][0] = False
                            next_row_sets[col] = s
                    elif valid_group:
                        random.shuffle(valid_group)
                        num_carves = random.randint(1, len(valid_group))
                        for i in range(num_carves):
                            col = valid_group[i]
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
            if blocked[y][x] or blocked[y][x + 1]:
                continue
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
                if blocked[y][x]:
                    continue
                cells[2 * y + 1][2 * x + 1][0] = False
                can_north = (y < sub_h - 1 and not blocked[y + 1][x])
                can_east = (x < sub_w - 1 and not blocked[y][x + 1])
                
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
        visited = [[blocked[y][x] for x in range(sub_w)] for y in range(sub_h)]
        frontier_walls = []

        def add_frontier_of(cx, cy):
            """Add unvisited neighbours of (cx, cy) to the Prim's frontier wall list (cube mode)."""
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < sub_w and 0 <= ny < sub_h:
                    if not visited[ny][nx]:
                        frontier_walls.append((cx, cy, nx, ny))

        start_cell = _get_start_cell(blocked, sub_w, sub_h)
        if start_cell is None:
            raise ValueError("All cells are blocked; cannot generate maze")
        sx, sy = start_cell

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
        visited = [[blocked[y][x] for x in range(sub_w)] for y in range(sub_h)]
        
        start_cell = _get_start_cell(blocked, sub_w, sub_h)
        if start_cell is None:
            raise ValueError("All cells are blocked; cannot generate maze")
        cx, cy = start_cell

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
                            if 0 <= nx < sub_w and 0 <= ny < sub_h and visited[ny][nx] and not blocked[ny][nx]:
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
        run = []
        for y in range(sub_h):
            for x in range(sub_w):
                if blocked[y][x]:
                    if run:
                        if y < sub_h - 1:
                            valid_run = [rx for rx in run if not blocked[y + 1][rx]]
                            if valid_run:
                                member_x = random.choice(valid_run)
                                cells[2 * y + 2][2 * member_x + 1][0] = False
                                cells[2 * y + 3][2 * member_x + 1][0] = False
                        run = []
                    continue
                
                cells[2 * y + 1][2 * x + 1][0] = False
                run.append(x)
                
                in_same_room = False
                if x + 1 < sub_w and not blocked[y][x + 1]:
                    r1 = cell_to_room.get((x, y))
                    r2 = cell_to_room.get((x + 1, y))
                    if r1 is not None and r1 == r2:
                        in_same_room = True

                carve_east = (x < sub_w - 1) and (not blocked[y][x + 1]) and (in_same_room or random.random() < east_bias)
                
                if carve_east:
                    cells[2 * y + 1][2 * x + 2][0] = False
                else:
                    if y < sub_h - 1:
                        valid_run = [rx for rx in run if not blocked[y + 1][rx]]
                        if valid_run:
                            member_x = random.choice(valid_run)
                            cells[2 * y + 2][2 * member_x + 1][0] = False
                            cells[2 * y + 3][2 * member_x + 1][0] = False
                    run = []

    elif algorithm == 'wilsons':
        visited = [[blocked[y][x] for x in range(sub_w)] for y in range(sub_h)]
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

        start_cell = _get_start_cell(blocked, sub_w, sub_h)
        if start_cell is None:
            raise ValueError("All cells are blocked; cannot generate maze")
        sx, sy = start_cell
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
                valid_dirs = [(dx, dy) for dx, dy in dirs if 0 <= cx + dx < sub_w and 0 <= cy + dy < sub_h and not blocked[cy + dy][cx + dx]]
                if not valid_dirs:
                    break
                dx, dy = random.choice(valid_dirs)
                nx, ny = cx + dx, cy + dy
                if (nx, ny) in walk:
                    idx = walk.index((nx, ny))
                    walk = walk[:idx + 1]
                else:
                    walk.append((nx, ny))
                cx, cy = nx, ny
            
            reached_visited = visited[cy][cx]
            if reached_visited:
                for i in range(len(walk) - 1):
                    x1, y1 = walk[i]
                    x2, y2 = walk[i+1]
                    cells[y1 + y2 + 1][x1 + x2 + 1][0] = False
                    mark_visited(x1, y1)
                mark_visited(walk[-1][0], walk[-1][1])
            else:
                for wx, wy in walk:
                    visited[wy][wx] = True

    elif algorithm == 'recursive_division':
        def is_grid_blocked(x, y):
            """Check if the doubled-grid cell (x, y) falls on or adjacent to a blocked sub-cell."""
            if not blocked:
                return False
            if x % 2 == 0 and y % 2 == 0:
                return True
            if x % 2 == 1 and y % 2 == 1:
                return blocked[(y - 1) // 2][(x - 1) // 2]
            if x % 2 == 0:
                y_sub = (y - 1) // 2
                xl = (x // 2) - 1
                xr = x // 2
                l_blocked = blocked[y_sub][xl] if (0 <= xl < sub_w) else True
                r_blocked = blocked[y_sub][xr] if (0 <= xr < sub_w) else True
                return l_blocked or r_blocked
            if y % 2 == 0:
                x_sub = (x - 1) // 2
                yb = (y // 2) - 1
                ya = y // 2
                b_blocked = blocked[yb][x_sub] if (0 <= yb < sub_h) else True
                a_blocked = blocked[ya][x_sub] if (0 <= ya < sub_h) else True
                return b_blocked or a_blocked
            return False

        for y in range(depth):
            for x in range(width):
                if is_grid_blocked(x, y):
                    cells[y][x][0] = True
                else:
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
                # px_actual unused in python version but kept for context if needed
                
                for x_sub in range(rx, rx + rw):
                    x_actual = 2 * x_sub + 1
                    if blocked and (blocked[wy_sub][x_sub] or blocked[wy_sub + 1][x_sub]):
                        cells[wy_actual][x_actual][0] = True
                        cells[wy_actual][x_actual - 1][0] = True
                        cells[wy_actual][x_actual + 1][0] = True
                        continue
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
                # py_actual unused in python version but kept for context if needed
                
                for y_sub in range(ry, ry + rh):
                    y_actual = 2 * y_sub + 1
                    if blocked and (blocked[y_sub][wx_sub] or blocked[y_sub][wx_sub + 1]):
                        cells[y_actual][wx_actual][0] = True
                        cells[y_actual - 1][wx_actual][0] = True
                        cells[y_actual + 1][wx_actual][0] = True
                        continue
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
        visited = [[blocked[y][x] for x in range(sub_w)] for y in range(sub_h)]
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

        start_cell = _get_start_cell(blocked, sub_w, sub_h)
        if start_cell is None:
            raise ValueError("All cells are blocked; cannot generate maze")
        sx, sy = start_cell
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

    main_entrance = entrance_list[0] if entrance_list else None
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
        # Close cloned entrance/exit openings on non-applicable floors
        for z in range(floors):
            if z != 0:
                for ex, ey, ed in entrance_list:
                    cells[z][ey][ex][0] = True
                    if ed == 'N' and ey - 1 >= 0:
                        cells[z][ey - 1][ex][0] = True
                    elif ed == 'S' and ey + 1 < depth:
                        cells[z][ey + 1][ex][0] = True
                    elif ed == 'E' and ex - 1 >= 0:
                        cells[z][ey][ex - 1][0] = True
                    elif ed == 'W' and ex + 1 < width:
                        cells[z][ey][ex + 1][0] = True
            if z != floors - 1:
                for ex, ey, ed in exit_list:
                    cells[z][ey][ex][0] = True
                    if ed == 'N' and ey - 1 >= 0:
                        cells[z][ey - 1][ex][0] = True
                    elif ed == 'S' and ey + 1 < depth:
                        cells[z][ey + 1][ex][0] = True
                    elif ed == 'E' and ex - 1 >= 0:
                        cells[z][ey][ex - 1][0] = True
                    elif ed == 'W' and ex + 1 < width:
                        cells[z][ey][ex + 1][0] = True
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
        maze_data.entrance = entrance_list[0] if entrance_list else None
        maze_data.exits = exit_list
    
    maze_data.guide_path = find_shortest_path(maze_data, wall_mode='cube')
    return maze_data
