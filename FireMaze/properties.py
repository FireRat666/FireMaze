import bpy


class FireMazeProperties(bpy.types.PropertyGroup):
    width: bpy.props.IntProperty(
        name="Width",
        description="Number of cells along X",
        default=10,
        min=3,
        max=200,
    )
    depth: bpy.props.IntProperty(
        name="Depth",
        description="Number of cells along Y",
        default=10,
        min=3,
        max=200,
    )
    wall_height: bpy.props.FloatProperty(
        name="Wall Height",
        description="Height of walls",
        default=1.0,
        min=0.1,
        max=100.0,
        unit='LENGTH',
    )
    wall_thickness: bpy.props.FloatProperty(
        name="Wall Thickness",
        description="Thickness of wall segments",
        default=0.1,
        min=0.01,
        max=10.0,
        unit='LENGTH',
    )
    tile_size: bpy.props.FloatProperty(
        name="Tile Size",
        description="Size of each tile square",
        default=1.0,
        min=0.1,
        max=100.0,
        unit='LENGTH',
    )
    wall_mode: bpy.props.EnumProperty(
        name="Wall Mode",
        description="Construction style for wall segments",
        default='thin',
        items=[
            ('thin', 'Thin',
             'Walls are thin boxes with configurable thickness sitting on grid lines'),
            ('cube', 'Cube',
             'Walls are full tile-sized cubes centered on grid lines, floor tiles remain full size'),
        ],
    )
    mode: bpy.props.EnumProperty(
        name="Mode",
        description="Maze completion goal",
        default='center',
        items=[
            ('center', 'Find Center',
             'Reach the center of the maze from an edge entrance'),
            ('exit', 'Find Exit',
             'Traverse from one side of the maze to the other'),
        ],
    )
    emergency_exits: bpy.props.BoolProperty(
        name="Emergency Exits",
        description="Add extra openings on outer edges that can lead out of the maze (Center mode only)",
        default=False,
    )
    seed: bpy.props.IntProperty(
        name="Seed",
        description="Random seed (0 = random each time)",
        default=0,
        min=0,
        max=999999,
    )
    tiles_centered: bpy.props.BoolProperty(
        name="Tiles Centered",
        description="Custom tiles have their center at the origin (e.g. Blender primitives). "
                    "Disable if tiles have their bottom-left corner at the origin",
        default=True,
    )
    custom_floor_mesh: bpy.props.PointerProperty(
        name="Floor Mesh",
        description="Optional custom mesh for floor tiles",
        type=bpy.types.Mesh,
    )
    custom_wall_north: bpy.props.PointerProperty(
        name="Wall Mesh (+Y)",
        description="Optional custom mesh for north-facing (+Y) wall faces",
        type=bpy.types.Mesh,
    )
    custom_wall_south: bpy.props.PointerProperty(
        name="Wall Mesh (-Y)",
        description="Optional custom mesh for south-facing (-Y) wall faces",
        type=bpy.types.Mesh,
    )
    custom_wall_east: bpy.props.PointerProperty(
        name="Wall Mesh (+X)",
        description="Optional custom mesh for east-facing (+X) wall faces",
        type=bpy.types.Mesh,
    )
    custom_wall_west: bpy.props.PointerProperty(
        name="Wall Mesh (-X)",
        description="Optional custom mesh for west-facing (-X) wall faces",
        type=bpy.types.Mesh,
    )
    custom_roof_mesh: bpy.props.PointerProperty(
        name="Roof Mesh",
        description="Optional custom mesh for roof tiles",
        type=bpy.types.Mesh,
    )


classes = (FireMazeProperties,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.fire_maze = bpy.props.PointerProperty(
        type=FireMazeProperties)


def unregister():
    del bpy.types.Scene.fire_maze
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
