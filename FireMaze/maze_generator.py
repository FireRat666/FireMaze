import random
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


@dataclass
class MazeData:
    width: int
    depth: int
    cells: List[List[List[bool]]]
    entrance: Tuple[int, int, str]
    exits: List[Tuple[int, int, str]] = field(default_factory=list)
    center: Tuple[int, int] = (0, 0)


def generate_maze(
    width: int,
    depth: int,
    seed: int = 0,
    mode: str = 'center',
    emergency_exits: bool = False,
) -> MazeData:
    if seed:
        random.seed(seed)

    cells = [[[True, True, True, True] for _ in range(width)]
             for _ in range(depth)]
    index = {'N': 0, 'S': 1, 'E': 2, 'W': 3}

    visited = [[False] * width for _ in range(depth)]
    stack = []
    sx = random.randrange(width)
    sy = random.randrange(depth)
    visited[sy][sx] = True
    stack.append((sx, sy))

    dirs = [('N', (0, 1)), ('S', (0, -1)), ('E', (1, 0)), ('W', (-1, 0))]
    opposites = {'N': 'S', 'S': 'N', 'E': 'W', 'W': 'E'}

    while stack:
        x, y = stack[-1]
        random.shuffle(dirs)
        carved = False
        for dname, (dx, dy) in dirs:
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < depth and not visited[ny][nx]:
                visited[ny][nx] = True
                cells[y][x][index[dname]] = False
                cells[ny][nx][index[opposites[dname]]] = False
                stack.append((nx, ny))
                carved = True
                break
        if not carved:
            stack.pop()

    entrance = None
    exits = []
    center = (width // 2, depth // 2)

    if mode == 'exit':
        ey = random.randrange(depth)
        entrance = (0, ey, 'W')
        cells[ey][0][index['W']] = False
        ey2 = random.randrange(depth)
        while ey2 == ey:
            ey2 = random.randrange(depth)
        exits.append((width - 1, ey2, 'E'))
        cells[ey2][width - 1][index['E']] = False
    else:
        edge = random.randrange(4)
        if edge == 0:
            ex = random.randrange(width)
            entrance = (ex, 0, 'S')
            cells[0][ex][index['S']] = False
        elif edge == 1:
            ex = random.randrange(width)
            entrance = (ex, depth - 1, 'N')
            cells[depth - 1][ex][index['N']] = False
        elif edge == 2:
            ey = random.randrange(depth)
            entrance = (0, ey, 'W')
            cells[ey][0][index['W']] = False
        else:
            ey = random.randrange(depth)
            entrance = (width - 1, ey, 'E')
            cells[ey][width - 1][index['E']] = False

        if emergency_exits:
            num_exits = random.randint(1, min(3, (width + depth) // 4 + 1))
            candidates = []
            for x in range(width):
                if (x, 0, 'S') != entrance and cells[0][x][index['S']]:
                    candidates.append((x, 0, 'S'))
                if (x, depth - 1, 'N'
                        ) != entrance and cells[depth - 1][x][index['N']]:
                    candidates.append((x, depth - 1, 'N'))
            for y in range(depth):
                if (0, y, 'W') != entrance and cells[y][0][index['W']]:
                    candidates.append((0, y, 'W'))
                if (width - 1, y, 'E'
                        ) != entrance and cells[y][width - 1][index['E']]:
                    candidates.append((width - 1, y, 'E'))
            selected = random.sample(
                candidates, min(num_exits, len(candidates)))
            for ex, ey, d in selected:
                cells[ey][ex][index[d]] = False
                exits.append((ex, ey, d))

    return MazeData(width, depth, cells, entrance, exits, center)
