import random
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
from collections import deque

@dataclass
class MazeData:
    width: int
    depth: int
    cells: List[List[List[bool]]]  # cells[y][x] = [N, S, E, W] or [is_wall, True, True, True]
    entrance: Tuple[int, int, str]
    exits: List[Tuple[int, int, str]] = field(default_factory=list)
    center: Tuple[int, int] = (0, 0)
    guide_path: List[Tuple[int, int]] = field(default_factory=list)


class UnionFind:
    def __init__(self, size):
        self.parent = list(range(size))

    def find(self, i):
        path = []
        while self.parent[i] != i:
            path.append(i)
            i = self.parent[i]
        for node in path:
            self.parent[node] = i
        return i

    def union(self, i, j):
        root_i = self.find(i)
        root_j = self.find(j)
        if root_i != root_j:
            self.parent[root_i] = root_j
            return True
        return False


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
) -> MazeData:
    if seed:
        random.seed(seed)

    # If Cube Wall Mode, we generate directly on the width x depth grid of blocks
    if wall_mode == 'cube':
        # Grid dimensions for path cells (must be odd coordinates)
        # We partition the grid into cell centers at odd positions
        sub_w = max(1, (width - 1) // 2)
        sub_h = max(1, (depth - 1) // 2)

        # Initialize all cells to walls (True)
        # cells[y][x] = [is_wall, wall_n_index, wall_s_index, wall_e_index, wall_w_index, floor_index, roof_index]
        cells = [[[True, -1, -1, -1, -1, -1, -1] for _ in range(width)] for _ in range(depth)]

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
            visited = [[False] * sub_w for _ in range(sub_h)]
            stack = []
            
            # Start cell
            sx = random.randrange(sub_w)
            sy = random.randrange(sub_h)
            
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

            dirs = [(0, 1), (0, -1), (1, 0), (-1, 0)]
            while stack:
                x, y = stack[-1]
                random.shuffle(dirs)
                carved = False
                for dx, dy in dirs:
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
                        carved = True
                        break
                if not carved:
                    stack.pop()

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
                        cells[2 * wy + 1][2 * wx + 3][0] = False
                else:
                    idx1 = wy * sub_w + wx
                    idx2 = (wy + 1) * sub_w + wx
                    if uf.union(idx1, idx2):
                        cells[2 * wy + 2][2 * wx + 1][0] = False
                        cells[2 * wy + 3][2 * wx + 1][0] = False

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
                        if same_room or random.random() < 0.5:
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
                        if random.random() < 0.5:
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
                while not walk_stuck:
                    random.shuffle(dirs)
                    moved = False
                    for dx, dy in dirs:
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < sub_w and 0 <= ny < sub_h and not visited[ny][nx]:
                            cells[cy + ny + 1][cx + nx + 1][0] = False
                            mark_visited(nx, ny)
                            cx, cy = nx, ny
                            moved = True
                            break
                    if not moved:
                        walk_stuck = True
                
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

                    carve_east = (x < sub_w - 1) and (in_same_room or random.random() < 0.5)
                    
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
                if rw < 2 or rh < 2:
                    return
                
                if horizontal:
                    wy_sub = ry + random.randrange(rh - 1)
                    wy_actual = 2 * wy_sub + 2
                    px_sub = rx + random.randrange(rw)
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
                    py_sub = ry + random.randrange(rh)
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
                if rw < rh:
                    return True
                elif rh < rw:
                    return False
                else:
                    return random.random() < 0.5

            divide(0, 0, sub_w, sub_h, choose_orientation(sub_w, sub_h))

        elif algorithm == 'growing_tree':
            visited = [[False] * sub_w for _ in range(sub_h)]
            active = []
            
            def add_to_active(x, y):
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
                if random.random() < 0.5:
                    idx = len(active) - 1
                else:
                    idx = random.randrange(len(active))
                
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

        maze_data = MazeData(width, depth, cells, main_entrance, main_exits, center)
        maze_data.guide_path = find_shortest_path(maze_data, wall_mode='cube')
        return maze_data

    # Thin wall mode
    # cells[y][x] = [N, S, E, W, n_wall_idx, s_wall_idx, e_wall_idx, w_wall_idx, floor_mesh_index, roof_mesh_index]
    cells = [[[True, True, True, True, -1, -1, -1, -1, -1, -1] for _ in range(width)] for _ in range(depth)]
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
        visited = [[False] * width for _ in range(depth)]
        stack = []
        
        sx = random.randrange(width)
        sy = random.randrange(depth)
        
        r_start = cell_to_room.get((sx, sy))
        if r_start is not None:
            for rx, ry in rooms[r_start]:
                visited[ry][rx] = True
                stack.append((rx, ry))
        else:
            visited[sy][sx] = True
            stack.append((sx, sy))

        dirs = [('N', (0, 1)), ('S', (0, -1)), ('E', (1, 0)), ('W', (-1, 0))]
        while stack:
            x, y = stack[-1]
            random.shuffle(dirs)
            carved = False
            for dname, (dx, dy) in dirs:
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
                    carved = True
                    break
            if not carved:
                stack.pop()

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
                    if same_room or random.random() < 0.5:
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
                    if random.random() < 0.5:
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

        if rooms_enable:
            for y in range(depth):
                for x in range(width):
                    r_idx = cell_to_room.get((x, y))
                    if r_idx is not None:
                        if y + 1 < depth and cell_to_room.get((x, y + 1)) == r_idx:
                            cells[y][x][0] = False
                            cells[y + 1][x][1] = False
                        if x + 1 < width and cell_to_room.get((x + 1, y)) == r_idx:
                            cells[y][x][2] = False
                            cells[y][x + 1][3] = False

    elif algorithm == 'prims':
        visited = [[False] * width for _ in range(depth)]
        frontier_walls = []

        def add_frontier_of(cx, cy):
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
            r = cell_to_room.get((x, y))
            if r is not None:
                for rx, ry in rooms[r]:
                    visited[ry][rx] = True
            else:
                visited[y][x] = True

        mark_visited(cx, cy)
        
        while True:
            dirs = [('N', (0, 1)), ('S', (0, -1)), ('E', (1, 0)), ('W', (-1, 0))]
            walk_stuck = False
            while not walk_stuck:
                random.shuffle(dirs)
                moved = False
                for dname, (dx, dy) in dirs:
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < width and 0 <= ny < depth and not visited[ny][nx]:
                        cells[cy][cx][index[dname]] = False
                        cells[ny][nx][index[opposites[dname]]] = False
                        mark_visited(nx, ny)
                        cx, cy = nx, ny
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

                carve_east = (x < width - 1) and (in_same_room or random.random() < 0.5)
                
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
            if rw < 2 or rh < 2:
                return
            
            if horizontal:
                wy_sub = ry + random.randrange(rh - 1)
                px_sub = rx + random.randrange(rw)
                
                for x_sub in range(rx, rx + rw):
                    if cell_to_room.get((x_sub, wy_sub)) is None or cell_to_room.get((x_sub, wy_sub + 1)) is None:
                        if x_sub != px_sub:
                            cells[wy_sub][x_sub][0] = True
                            cells[wy_sub + 1][x_sub][1] = True
                
                divide(rx, ry, rw, wy_sub - ry + 1, choose_orientation(rw, wy_sub - ry + 1))
                divide(rx, wy_sub + 1, rw, ry + rh - wy_sub - 1, choose_orientation(rw, ry + rh - wy_sub - 1))
            else:
                wx_sub = rx + random.randrange(rw - 1)
                py_sub = ry + random.randrange(rh)
                
                for y_sub in range(ry, ry + rh):
                    if cell_to_room.get((wx_sub, y_sub)) is None or cell_to_room.get((wx_sub + 1, y_sub)) is None:
                        if y_sub != py_sub:
                            cells[y_sub][wx_sub][2] = True
                            cells[y_sub][wx_sub + 1][3] = True
                
                divide(rx, ry, wx_sub - rx + 1, rh, choose_orientation(wx_sub - rx + 1, rh))
                divide(wx_sub + 1, ry, rx + rw - wx_sub - 1, rh, choose_orientation(rx + rw - wx_sub - 1, rh))

        def choose_orientation(rw, rh):
            if rw < rh:
                return True
            elif rh < rw:
                return False
            else:
                return random.random() < 0.5

        divide(0, 0, width, depth, choose_orientation(width, depth))

    elif algorithm == 'growing_tree':
        visited = [[False] * width for _ in range(depth)]
        active = []
        
        def add_to_active(x, y):
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
            if random.random() < 0.5:
                idx = len(active) - 1
            else:
                idx = random.randrange(len(active))
            
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

    maze_data = MazeData(width, depth, cells, main_entrance, main_exits, center)
    maze_data.guide_path = find_shortest_path(maze_data, wall_mode='thin')
    return maze_data


def find_shortest_path(maze_data: MazeData, wall_mode: str) -> List[Tuple[int, int]]:
    if not maze_data.entrance:
        return []
    start_x, start_y, _ = maze_data.entrance

    targets = []
    if maze_data.exits:
        for ex, ey, _ in maze_data.exits:
            targets.append((ex, ey))
    else:
        targets.append(maze_data.center)

    if not targets:
        return []

    queue = deque([(start_x, start_y, [(start_x, start_y)])])
    visited = {(start_x, start_y)}

    dirs = [('N', 0, 1, 0), ('S', 0, -1, 1), ('E', 1, 0, 2), ('W', -1, 0, 3)]

    while queue:
        cx, cy, path = queue.popleft()

        if (cx, cy) in targets:
            return path

        for dname, dx, dy, wall_idx in dirs:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < maze_data.width and 0 <= ny < maze_data.depth:
                if (nx, ny) not in visited:
                    if wall_mode == 'cube':
                        if not maze_data.cells[ny][nx][0]:
                            visited.add((nx, ny))
                            queue.append((nx, ny, path + [(nx, ny)]))
                    else:
                        if not maze_data.cells[cy][cx][wall_idx]:
                            visited.add((nx, ny))
                            queue.append((nx, ny, path + [(nx, ny)]))
    return []
