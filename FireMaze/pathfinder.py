"""Pathfinding logic for FireMaze."""

from collections import deque
from typing import List, Tuple
from .maze_data import MazeData
from .utils import _resolve_cells_3d, _get_stair_footprint_coords

def _get_polar_neighbors(r: int, theta: int, rings: int, ring_sectors: List[int]) -> List[Tuple[int, int]]:
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
            if 2 * theta < N_out:
                neighbors.append((r + 1, 2 * theta))
            if 2 * theta + 1 < N_out:
                neighbors.append((r + 1, 2 * theta + 1))
    return neighbors


def _find_shortest_path_polar_3d(maze_data: MazeData, wall_mode: str, cells_3d: List) -> List:
    """3D BFS for polar grid over (z, r, theta) with vertical stair edges."""
    start_r = maze_data.entrance[0]
    start_theta = maze_data.entrance[1]
    start_z = 0
    floors = maze_data.floors
    rings = maze_data.polar_rings
    ring_sectors = maze_data.ring_sectors

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
            # CCW (ctheta + 1): separated by the CW wall of cell (ctheta + 1)
            if cr >= 1 and not cells_3d[cz][cr][(ctheta + 1) % Nr][0]:
                accessible.append((cr, (ctheta + 1) % Nr))
            # CW (ctheta - 1): separated by the CW wall of cell ctheta
            if cr >= 1 and not cells_3d[cz][cr][ctheta][0]:
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


def _find_shortest_path_2d(maze_data: MazeData, wall_mode: str, cells_2d: List) -> List:
    """2D BFS for rectangular grid (y, x) with wall-aware neighbour traversal."""
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
        for _, dx, dy, wall_idx in dirs:
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


def _find_shortest_path_3d(maze_data: MazeData, wall_mode: str, cells_3d: List) -> List:
    """3D BFS over (z, y, x) with vertical stair edges."""
    if not maze_data.entrance:
        return []
    start_x, start_y, _ = maze_data.entrance
    start_z = 0
    floors = maze_data.floors

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

        for _, dy, dx, wall_idx in dirs:
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


def _find_shortest_path_polar_2d(maze_data: MazeData, wall_mode: str) -> List:
    """2D BFS for polar grid (r, theta) with ring-aware neighbour traversal."""
    if not maze_data.entrance:
        return []
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
            # CCW (theta + 1): separated by the CW wall of cell (theta + 1)
            if r >= 1 and not cells_2d[r][(theta + 1) % Nr][0]:
                accessible.append((r, (theta + 1) % Nr))
            # CW (theta - 1): separated by the CW wall of cell theta
            if r >= 1 and not cells_2d[r][theta][0]:
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
                    if 2 * theta < N_out and not cells_2d[r + 1][2 * theta][1]:
                        accessible.append((r + 1, 2 * theta))
                    if 2 * theta + 1 < N_out and not cells_2d[r + 1][2 * theta + 1][1]:
                        accessible.append((r + 1, 2 * theta + 1))
        for neighbor in accessible:
            if neighbor not in parent:
                parent[neighbor] = node
                queue.append(neighbor)
    return []


def find_shortest_path(maze_data: MazeData, wall_mode: str) -> List:
    """Find the shortest path from entrance to exit/center using BFS."""
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

    return _find_shortest_path_2d(maze_data, wall_mode, cells_3d[0])
