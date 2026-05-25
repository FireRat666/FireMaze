import bpy


class VIEW3D_PT_fire_maze(bpy.types.Panel):
    bl_label = "FireMaze"
    bl_idname = "VIEW3D_PT_fire_maze"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "FireRat"

    def draw(self, context):
        layout = self.layout
        props = context.scene.fire_maze

        box = layout.box()
        box.label(text="Maze Settings", icon='GRID')
        col = box.column(align=True)
        col.prop(props, "width")
        col.prop(props, "depth")
        col.separator(factor=0.5)
        col.prop(props, "wall_height_tiled")
        if props.wall_height_tiled:
            col.prop(props, "wall_height_tiles")
        else:
            col.prop(props, "wall_height")
        col.row().prop(props, "wall_mode", expand=True)
        if props.wall_mode == 'thin':
            col.prop(props, "wall_thickness")
        col.prop(props, "tile_size")

        box = layout.box()
        box.label(text="Mode", icon='PLAY')
        col = box.column(align=True)
        col.row().prop(props, "mode", expand=True)
        if props.mode == 'center':
            col.separator(factor=0.3)
            col.prop(props, "emergency_exits")

        box = layout.box()
        box.label(text="Randomization", icon='FILE_REFRESH')
        col = box.column(align=True)
        col.prop(props, "seed")

        box = layout.box()
        box.label(text="Custom Tiles (optional)", icon='MESH_DATA')
        col = box.column(align=True)
        col.prop(props, "tiles_centered")
        col.separator(factor=0.3)
        col.prop(props, "custom_floor_mesh", text="Floor")
        col.prop(props, "custom_wall_north", text="Wall +Y (North)")
        col.prop(props, "custom_wall_south", text="Wall -Y (South)")
        col.prop(props, "custom_wall_east", text="Wall +X (East)")
        col.prop(props, "custom_wall_west", text="Wall -X (West)")
        col.prop(props, "custom_roof_mesh", text="Roof")

        row = layout.row(align=True)
        row.scale_y = 1.8
        row.operator("fire_maze.generate", text="Generate Maze",
                     icon='MESH_GRID')
        layout.separator(factor=0.5)

        layout.separator(factor=0.5)
        layout.operator("fire_maze.clear", text="Clear Maze",
                        icon='TRASH')


classes = (VIEW3D_PT_fire_maze,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
