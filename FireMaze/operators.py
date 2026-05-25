import bpy
from .maze_generator import generate_maze
from .mesh_builder import build_maze_objects


def _find_or_create_maze_collection(base_name):
    col = bpy.data.collections.get(base_name)
    if col:
        has_maze = any(obj.get("fire_maze") for obj in col.objects)
        if has_maze:
            i = 1
            while bpy.data.collections.get(f"{base_name}.{i:03d}"):
                i += 1
            col = bpy.data.collections.new(f"{base_name}.{i:03d}")
    else:
        col = bpy.data.collections.new(base_name)
    return col


def _remove_maze_collections():
    for col in list(bpy.data.collections):
        if col.name.startswith("FireMaze"):
            try:
                bpy.data.collections.remove(col)
            except RuntimeError:
                for parent in list(bpy.data.collections):
                    try:
                        parent.children.unlink(col)
                    except ValueError:
                        pass
                try:
                    bpy.data.collections.remove(col)
                except RuntimeError:
                    pass


class MAZE_OT_generate(bpy.types.Operator):
    bl_idname = "fire_maze.generate"
    bl_label = "Generate Maze"
    bl_description = "Generate a new maze from current settings"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.fire_maze

        if props.width < 3 or props.depth < 3:
            self.report({'ERROR'}, "Width and Depth must be at least 3")
            return {'CANCELLED'}

        maze_data = generate_maze(
            width=props.width,
            depth=props.depth,
            seed=props.seed,
            mode=props.mode,
            emergency_exits=props.emergency_exits,
        )

        col = _find_or_create_maze_collection("FireMaze")
        build_maze_objects(props, maze_data, context, collection=col)

        self.report({'INFO'}, f"Maze generated ({props.width}x{props.depth})")
        return {'FINISHED'}


class MAZE_OT_clear(bpy.types.Operator):
    bl_idname = "fire_maze.clear"
    bl_label = "Clear Maze"
    bl_description = "Delete all generated maze objects from the scene"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        count = 0
        for obj in list(bpy.data.objects):
            if obj.get("fire_maze"):
                mesh = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if mesh and mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
                count += 1

        _remove_maze_collections()

        self.report({'INFO'}, f"Removed {count} maze object(s)")
        return {'FINISHED'}


classes = (MAZE_OT_generate, MAZE_OT_clear)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
