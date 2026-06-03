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
        description="Height of walls (used when Tiled Height is off)",
        default=1.0,
        min=0.1,
        max=100.0,
        unit='LENGTH',
    )
    wall_height_tiled: bpy.props.BoolProperty(
        name="Tiled Height",
        description="Stack walls in tile-height increments for consistent UV",
        default=False,
    )
    wall_height_tiles: bpy.props.IntProperty(
        name="Tiles High",
        description="Number of tile-height segments to stack",
        default=1,
        min=1,
        max=100,
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
    grid_type: bpy.props.EnumProperty(
        name="Grid Type",
        description="Layout shape of the maze",
        default='rect',
        items=[
            ('rect', 'Rectangular', 'Standard rectangular grid maze'),
            ('polar', 'Polar (Circular)', 'Circular maze generated in concentric rings'),
        ],
    )
    polar_rings: bpy.props.IntProperty(
        name="Rings",
        description="Number of concentric rings in the polar maze",
        default=5,
        min=2,
        max=100,
    )
    polar_custom_alignment: bpy.props.EnumProperty(
        name="Polar Custom Alignment",
        description="How custom tiles are aligned to the wedge-shaped polar cells",
        default='bend',
        items=[
            ('procedural', 'Procedural Only', 'Use procedurally generated curved meshes, ignoring custom tiles'),
            ('trapezoid', 'Trapezoidal Scaling', 'Scale/stretch custom tiles to fit cells (straight walls, segmented look)'),
            ('bend', 'Polar Bending (Warp)', 'Dynamically bend and warp custom tile vertices along the circular arcs'),
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
    custom_wall_mesh: bpy.props.PointerProperty(
        name="Wall Mesh",
        description="Optional custom mesh for wall segments",
        type=bpy.types.Mesh,
    )
    thin_wall_double_sided: bpy.props.BoolProperty(
        name="Double-Sided Thin Walls",
        description="Duplicate single-sided custom wall tiles on both sides of grid lines to create thickness. "
                    "Disable to use a single centered custom tile with built-in thickness",
        default=True,
    )
    custom_roof_mesh: bpy.props.PointerProperty(
        name="Roof Mesh",
        description="Optional custom mesh for roof tiles",
        type=bpy.types.Mesh,
    )

    # Algorithm
    algorithm: bpy.props.EnumProperty(
        name="Algorithm",
        description="Maze generation algorithm",
        default='dfs',
        items=[
            ('dfs', 'Depth-First Search', 'Randomized depth-first search (recursive backtracker)'),
            ('kruskal', "Kruskal's", 'Randomized Kruskal\'s algorithm'),
            ('eller', "Eller's", 'Eller\'s algorithm (row by row)'),
            ('binary_tree', 'Binary Tree', 'Binary Tree algorithm (simple and fast)'),
            ('prims', "Prim's", 'Randomized Prim\'s algorithm (balanced, organic paths)'),
            ('hunt_and_kill', 'Hunt-and-Kill', 'Hunt-and-Kill algorithm (long corridors)'),
            ('sidewinder', 'Sidewinder', 'Sidewinder algorithm (strong horizontal bias)'),
            ('wilsons', "Wilson's", 'Wilson\'s algorithm (loop-erased random walk)'),
            ('recursive_division', 'Recursive Division', 'Recursive Division (nested rooms/chambers)'),
            ('growing_tree', 'Growing Tree', 'Growing Tree algorithm (highly customizable hybrid)'),
        ],
    )

    # Rooms
    rooms_enable: bpy.props.BoolProperty(
        name="Enable Rooms",
        description="Randomly generate interior rooms",
        default=False,
    )
    rooms_count: bpy.props.IntProperty(
        name="Room Count",
        description="Number of rooms to generate",
        default=3,
        min=1,
        max=50,
    )
    min_room_size: bpy.props.IntProperty(
        name="Min Room Size",
        description="Minimum room size (in cells)",
        default=2,
        min=2,
        max=20,
    )
    max_room_size: bpy.props.IntProperty(
        name="Max Room Size",
        description="Maximum room size (in cells)",
        default=4,
        min=2,
        max=20,
    )

    # Loops & Layout
    loop_probability: bpy.props.FloatProperty(
        name="Loop Probability",
        description="Probability of removing walls to create loops (0 = perfect maze)",
        default=0.0,
        min=0.0,
        max=1.0,
    )
    isolated_wall_prob: bpy.props.FloatProperty(
        name="Isolated Wall Prob",
        description="Probability of placing random isolated wall segments/pillars",
        default=0.0,
        min=0.0,
        max=1.0,
    )

    # Entrances & Exits
    entrance_side: bpy.props.EnumProperty(
        name="Entrance Side",
        description="Side to place entrances",
        default='ANY',
        items=[
            ('ANY', 'Random/Any', 'Randomly choose border side'),
            ('N', 'North (+Y)', 'North border'),
            ('S', 'South (-Y)', 'South border'),
            ('E', 'East (+X)', 'East border'),
            ('W', 'West (-X)', 'West border'),
        ],
    )
    exit_side: bpy.props.EnumProperty(
        name="Exit Side",
        description="Side to place exits",
        default='ANY',
        items=[
            ('ANY', 'Random/Any', 'Randomly choose border side'),
            ('N', 'North (+Y)', 'North border'),
            ('S', 'South (-Y)', 'South border'),
            ('E', 'East (+X)', 'East border'),
            ('W', 'West (-X)', 'West border'),
        ],
    )
    num_entrances: bpy.props.IntProperty(
        name="Entrances",
        description="Number of entrances to generate",
        default=1,
        min=1,
        max=10,
    )
    num_exits: bpy.props.IntProperty(
        name="Exits",
        description="Number of exits to generate",
        default=1,
        min=0,
        max=10,
    )

    # Custom Wall Collection
    custom_wall_collection: bpy.props.PointerProperty(
        name="Wall Collection",
        description="Optional collection of meshes to randomly distribute as walls. For best results, meshes must be centered horizontally (local X=0, Y=0) with the bottom aligned at local Z=0. If origin is at center, it will sink by half its height",
        type=bpy.types.Collection,
    )

    # Custom Floor Collection
    custom_floor_collection: bpy.props.PointerProperty(
        name="Floor Collection",
        description="Optional collection of meshes to randomly distribute as floors. Meshes must be centered horizontally (local X=0, Y=0). If origin is at center, it will sink by half its height",
        type=bpy.types.Collection,
    )

    # Custom Roof Collection
    custom_roof_collection: bpy.props.PointerProperty(
        name="Roof Collection",
        description="Optional collection of meshes to randomly distribute as roofs. Meshes must be centered horizontally (local X=0, Y=0). If origin is at center, it will sink by half its height",
        type=bpy.types.Collection,
    )

    # Cube Mode Pillar
    cube_mode_pillar: bpy.props.BoolProperty(
        name="Instanced Pillars",
        description="In Cube Mode, use whole meshes from the wall collection as single pillars/cubes. Meshes must be centered horizontally (local X=0, Y=0) with the bottom aligned at local Z=0 to prevent sinking",
        default=False,
    )

    # Guide Settings
    generate_guide: bpy.props.BoolProperty(
        name="Generate Guide",
        description="Generate the quickest route through the maze",
        default=False,
    )
    guide_type: bpy.props.EnumProperty(
        name="Guide Type",
        description="Visual style of the guide path",
        default='curve',
        items=[
            ('curve', 'Curve', 'A simple curve object'),
            ('tube', 'Tube (Mesh)', 'A 3D tube mesh around the path'),
            ('ribbon', 'Ribbon', 'A flat ribbon path'),
        ],
    )
    guide_width: bpy.props.FloatProperty(
        name="Guide Width",
        description="Thickness of the guide tube/ribbon",
        default=0.1,
        min=0.01,
        max=10.0,
        unit='LENGTH',
    )
    guide_height_offset: bpy.props.FloatProperty(
        name="Height Offset",
        description="Z-offset of the guide path above the floor",
        default=0.1,
        min=-10.0,
        max=10.0,
        unit='LENGTH',
    )
    guide_wave_amplitude: bpy.props.FloatProperty(
        name="Wave Amplitude",
        description="Amplitude of sine wave added to the guide height",
        default=0.0,
        min=0.0,
        max=5.0,
        unit='LENGTH',
    )
    guide_wave_frequency: bpy.props.FloatProperty(
        name="Wave Frequency",
        description="Frequency of sine wave added to the guide height",
        default=1.0,
        min=0.1,
        max=20.0,
    )

    # Detailed Customization (Transforms)
    wall_translate: bpy.props.FloatVectorProperty(
        name="Wall Translate",
        description="Translation offset for wall instances",
        default=(0.0, 0.0, 0.0),
        subtype='TRANSLATION',
        unit='LENGTH',
    )
    wall_rotate: bpy.props.FloatVectorProperty(
        name="Wall Rotate",
        description="Euler rotation offset for wall instances (in degrees, around its own center)",
        default=(0.0, 0.0, 0.0),
        subtype='EULER',
        unit='ROTATION',
    )
    wall_scale: bpy.props.FloatVectorProperty(
        name="Wall Scale",
        description="Scale multiplier for wall instances",
        default=(1.0, 1.0, 1.0),
    )
    floor_translate: bpy.props.FloatVectorProperty(
        name="Floor Translate",
        description="Translation offset for floor instances",
        default=(0.0, 0.0, 0.0),
        subtype='TRANSLATION',
        unit='LENGTH',
    )
    floor_rotate: bpy.props.FloatVectorProperty(
        name="Floor Rotate",
        description="Euler rotation offset for floor instances (in degrees, around its own center)",
        default=(0.0, 0.0, 0.0),
        subtype='EULER',
        unit='ROTATION',
    )
    floor_scale: bpy.props.FloatVectorProperty(
        name="Floor Scale",
        description="Scale multiplier for floor instances",
        default=(1.0, 1.0, 1.0),
    )

    # Post-processing
    single_wall_object: bpy.props.BoolProperty(
        name="Single Wall Object",
        description="Combine all walls (and caps) into a single object",
        default=True,
    )
    merge_objects: bpy.props.BoolProperty(
        name="Merge Objects",
        description="Merge floor, walls, and roof into one single object",
        default=False,
    )
    remove_doubles: bpy.props.BoolProperty(
        name="Remove Doubles",
        description="Remove double vertices when finished building maze",
        default=False,
    )
    generate_lightmap: bpy.props.BoolProperty(
        name="Generate Lightmap UVs",
        description="Generate a second UV map named 'Lightmap' and perform an unwrap on all final maze mesh objects",
        default=False,
    )
    lightmap_method: bpy.props.EnumProperty(
        name="Lightmap Method",
        description="Method used to unwrap lightmap UVs",
        default='smart',
        items=[
            ('smart', "Smart UV Project", "Group co-planar faces into continuous islands (reduces seams)"),
            ('pack', "Lightmap Pack", "Project and pack each face individually (zero distortion, maximum packing density)"),
        ],
    )
    generate_colliders: bpy.props.BoolProperty(
        name="Generate Colliders",
        description="Generate simple, flat mesh objects for collision (Floor, Walls, and Roof colliders)",
        default=False,
    )
    merge_colliders: bpy.props.BoolProperty(
        name="Merge Colliders",
        description="Merge all generated collider meshes into a single object named FireMaze_Collider",
        default=False,
    )
    optimize_colliders_coplanar: bpy.props.BoolProperty(
        name="Optimize Colliders",
        description="Simplify collider geometry by dissolving coplanar faces to reduce polygon count",
        default=False,
    )
    optimize_coplanar: bpy.props.BoolProperty(
        name="Optimize Geometry (Dissolve Planar)",
        description="Simplify geometry by dissolving coplanar faces (may stretch tiled textures)",
        default=False,
    )
    vertex_paint_enable: bpy.props.BoolProperty(
        name="Enable Vertex Painting",
        description="Procedurally paint vertex colors for shading or blending",
        default=False,
    )
    vertex_paint_mode: bpy.props.EnumProperty(
        name="Vertex Paint Mode",
        description="How vertex colors are assigned",
        default='ao',
        items=[
            ('ao', "Ambient Occlusion", "Procedural shadows in corners and seams"),
            ('blend', "Texture Blend Weights", "R=Moss, G=Cracks, B=Wetness, A=Soot"),
            ('path', "Path Highlight", "Highlight the correct path in green"),
            ('distance', "Distance Gradient", "Black-to-white gradient from entrance to exit"),
        ],
    )
    vertex_paint_intensity: bpy.props.FloatProperty(
        name="Paint Intensity",
        description="Intensity/opacity scale of the vertex paint effect",
        default=1.0,
        min=0.0,
        max=1.0,
    )
    # Prop spawner settings
    prop_torch_mesh: bpy.props.PointerProperty(
        name="Torch Object",
        description="Optional object (mesh, light, or group) to spawn as a wall torch",
        type=bpy.types.Object,
    )
    prop_chest_mesh: bpy.props.PointerProperty(
        name="Chest Object",
        description="Optional object to spawn in dead-ends/corners",
        type=bpy.types.Object,
    )
    prop_door_mesh: bpy.props.PointerProperty(
        name="Door Object",
        description="Optional object to spawn at room transitions or exits",
        type=bpy.types.Object,
    )
    prop_torch_density: bpy.props.FloatProperty(
        name="Torch Density",
        description="Probability of placing a torch on any valid wall face",
        default=0.2,
        min=0.0,
        max=1.0,
    )
    prop_chest_density: bpy.props.FloatProperty(
        name="Chest Density",
        description="Probability of placing a chest in any dead-end cell",
        default=0.5,
        min=0.0,
        max=1.0,
    )
    # Image masking settings
    mask_image: bpy.props.PointerProperty(
        name="Mask Image",
        description="Black-and-white image to mask the maze shape (White = Walkable, Black = Blocked/Wall)",
        type=bpy.types.Image,
    )
    mask_invert: bpy.props.BoolProperty(
        name="Invert Mask",
        description="Invert mask colors (Black = Walkable, White = Blocked/Wall)",
        default=False,
    )
    is_editing: bpy.props.BoolProperty(
        name="Is Editing",
        description="Whether the interactive maze editor is active",
        default=False,
    )
    fire_maze_collection_name: bpy.props.StringProperty(
        name="Collection Name",
        description="The name of the collection containing the active maze",
        default="",
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
