"""Polar maze algorithm for FireMaze."""

import math
import copy
import logging
from typing import List, Tuple
from ..maze_data import MazeData, UnionFind
from ..pathfinder import find_shortest_path, _get_polar_neighbors
from ..utils import get_rng, set_seed

logger = logging.getLogger(__name__)

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
    random = get_rng()
    set_seed(seed)

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
            for _ in range(ring_sectors[r]):
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
                    if wall_mode == 'cube' and cells[r][theta][0]:
                        continue
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

    if wall_mode == 'cube':
        cells[0][entrance[0]][entrance[1]][0] = False
        for ex in exits:
            cells[floors - 1][ex[0]][ex[1]][0] = False

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
