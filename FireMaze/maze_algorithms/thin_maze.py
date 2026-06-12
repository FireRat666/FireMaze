"""Thin maze algorithm for FireMaze."""

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
    random = get_rng()

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
            rw = random.randint(min(min_room_size, width), min(max_room_size, width))
            rh = random.randint(min(min_room_size, depth), min(max_room_size, depth))
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
                if blocked and blocked[y][x]:
                    continue
                if x + 1 < width and not (blocked and blocked[y][x + 1]):
                    walls.append(('V', x, y))
                if y + 1 < depth and not (blocked and blocked[y + 1][x]):
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
                if blocked and (blocked[y][x] or blocked[y][x + 1]):
                    continue
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
                    if blocked and (blocked[y][x] or blocked[y - 1][x]):
                        continue
                    if cell_to_room.get((x, y)) is not None and cell_to_room.get((x, y)) == cell_to_room.get((x, y - 1)):
                        cells[y - 1][x][0] = False
                        cells[y][x][1] = False

            for x in range(width - 1):
                if blocked and (blocked[y][x] or blocked[y][x + 1]):
                    continue
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
                    if not (blocked and blocked[y][x]):
                        set_groups.setdefault(s, []).append(x)

                for s, group in set_groups.items():
                    valid_group = [col for col in group if not (blocked and blocked[y + 1][col])]
                    room_up_cols = []
                    for col in valid_group:
                        if (cell_to_room.get((col, y)) is not None and 
                            cell_to_room.get((col, y)) == cell_to_room.get((col, y + 1))):
                            room_up_cols.append(col)

                    if room_up_cols:
                        for col in room_up_cols:
                            cells[y][col][0] = False
                            cells[y + 1][col][1] = False
                            next_row_sets[col] = s
                    elif valid_group:
                        random.shuffle(valid_group)
                        num_carves = random.randint(1, len(valid_group))
                        for i in range(num_carves):
                            col = valid_group[i]
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
            if blocked and (blocked[y][x] or blocked[y][x + 1]):
                continue
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
                if blocked and blocked[y][x]:
                    continue
                can_north = (y < depth - 1 and not (blocked and blocked[y + 1][x]))
                can_east = (x < width - 1 and not (blocked and blocked[y][x + 1]))
                
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
        visited = [[blocked[y][x] if blocked else False for x in range(width)] for y in range(depth)]
        frontier_walls = []

        def add_frontier_of(cx, cy):
            """Add unvisited neighbours of (cx, cy) to the Prim's frontier wall list (2D mode)."""
            for dname, (dx, dy) in [('N', (0, 1)), ('S', (0, -1)), ('E', (1, 0)), ('W', (-1, 0))]:
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < width and 0 <= ny < depth:
                    if not visited[ny][nx]:
                        frontier_walls.append((cx, cy, nx, ny, dname))

        sx, sy = _get_start_cell(blocked, width, depth)
        
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
        visited = [[blocked[y][x] if blocked else False for x in range(width)] for y in range(depth)]
        
        cx, cy = _get_start_cell(blocked, width, depth)
        
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
                            if 0 <= nx < width and 0 <= ny < depth and visited[ny][nx] and not (blocked and blocked[ny][nx]):
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
        run = []
        for y in range(depth):
            for x in range(width):
                if blocked and blocked[y][x]:
                    if run:
                        if y < depth - 1:
                            valid_run = [rx for rx in run if not (blocked and blocked[y + 1][rx])]
                            if valid_run:
                                member_x = random.choice(valid_run)
                                cells[y][member_x][0] = False
                                cells[y + 1][member_x][1] = False
                        run = []
                    continue
                
                run.append(x)
                
                in_same_room = False
                if x + 1 < width and not (blocked and blocked[y][x + 1]):
                    r1 = cell_to_room.get((x, y))
                    r2 = cell_to_room.get((x + 1, y))
                    if r1 is not None and r1 == r2:
                        in_same_room = True

                carve_east = (x < width - 1) and (not (blocked and blocked[y][x + 1])) and (in_same_room or random.random() < east_bias)
                
                if carve_east:
                    cells[y][x][2] = False
                    cells[y][x + 1][3] = False
                else:
                    if y < depth - 1:
                        valid_run = [rx for rx in run if not (blocked and blocked[y + 1][rx])]
                        if valid_run:
                            member_x = random.choice(valid_run)
                            cells[y][member_x][0] = False
                            cells[y + 1][member_x][1] = False
                    run = []

    elif algorithm == 'wilsons':
        visited = [[blocked[y][x] if blocked else False for x in range(width)] for y in range(depth)]
        unvisited_list = []
        
        def mark_visited(x, y):
            """Mark cell (x, y) as visited in 2D mode (Wilson's)."""
            r = cell_to_room.get((x, y))
            if r is not None:
                for rx, ry in rooms[r]:
                    visited[ry][rx] = True
            else:
                visited[y][x] = True

        sx, sy = _get_start_cell(blocked, width, depth)
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
                valid_dirs = [(dname, (dx, dy)) for dname, (dx, dy) in dirs if 0 <= cx + dx < width and 0 <= cy + dy < depth and not (blocked and blocked[cy + dy][cx + dx])]
                if not valid_dirs:
                    break
                dname, (dx, dy) = random.choice(valid_dirs)
                nx, ny = cx + dx, cy + dy
                if (nx, ny) in walk:
                    idx = walk.index((nx, ny))
                    walk = walk[:idx + 1]
                    walk_dirs = walk_dirs[:idx]
                else:
                    walk.append((nx, ny))
                    walk_dirs.append(dname)
                cx, cy = nx, ny
            
            reached_visited = visited[cy][cx]
            if reached_visited:
                for i in range(len(walk) - 1):
                    x1, y1 = walk[i]
                    dname = walk_dirs[i]
                    x2, y2 = walk[i+1]
                    cells[y1][x1][index[dname]] = False
                    cells[y2][x2][index[opposites[dname]]] = False
                    mark_visited(x1, y1)
                mark_visited(walk[-1][0], walk[-1][1])
            else:
                for wx, wy in walk:
                    visited[wy][wx] = True

    elif algorithm == 'recursive_division':
        for y in range(depth):
            for x in range(width):
                if not (blocked and blocked[y][x]):
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
                    if blocked and (blocked[wy_sub][x_sub] or blocked[wy_sub + 1][x_sub]):
                        cells[wy_sub][x_sub][0] = True
                        cells[wy_sub + 1][x_sub][1] = True
                        continue
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
                    if blocked and (blocked[y_sub][wx_sub] or blocked[y_sub][wx_sub + 1]):
                        cells[y_sub][wx_sub][2] = True
                        cells[y_sub][wx_sub + 1][3] = True
                        continue
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
        visited = [[blocked[y][x] if blocked else False for x in range(width)] for y in range(depth)]
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

        sx, sy = _get_start_cell(blocked, width, depth)
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

        if carved_count < count:
            raise ValueError(f"Failed to carve the requested count of {count} border openings on side {side}.")

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

    main_entrance = entrance_list[0] if entrance_list else None
    main_exits = exit_list
    center = (width // 2, depth // 2)

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
                    cells[z][ey][ex][index[ed]] = True
            if z != floors - 1:
                for ex, ey, ed in exit_list:
                    cells[z][ey][ex][index[ed]] = True
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
        maze_data.entrance = entrance_list[0] if entrance_list else None
        maze_data.exits = exit_list
                        
    maze_data.guide_path = find_shortest_path(maze_data, wall_mode='thin')
    return maze_data
