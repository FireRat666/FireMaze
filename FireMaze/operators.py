import bpy
import json
import random
import math
from bpy_extras import view3d_utils
from .maze_generator import generate_maze, find_shortest_path, MazeData
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

def delete_edit_helper():
    helper = bpy.data.objects.get("_FireMaze_Edit_Helper")
    if helper:
        data = helper.data
        bpy.data.objects.remove(helper, do_unlink=True)
        if data and data.users == 0:
            bpy.data.meshes.remove(data)

def rebuild_maze_from_collection(context, col):
    if "fire_maze_data" not in col:
        return
        
    data_dict = json.loads(col["fire_maze_data"])
    wall_mode = data_dict.get('wall_mode', context.scene.fire_maze.wall_mode)
    props = context.scene.fire_maze
    props.wall_mode = wall_mode

    # Determine number of meshes in collections
    num_wall_meshes = 0
    if props.custom_wall_collection:
        num_wall_meshes = len([o for o in props.custom_wall_collection.objects if o.type == 'MESH'])

    num_floor_meshes = 0
    if props.custom_floor_collection:
        num_floor_meshes = len([o for o in props.custom_floor_collection.objects if o.type == 'MESH'])

    num_roof_meshes = 0
    if props.custom_roof_collection:
        num_roof_meshes = len([o for o in props.custom_roof_collection.objects if o.type == 'MESH'])
    
    grid_type = data_dict.get('grid_type', 'rect')
    polar_rings = data_dict.get('polar_rings', 0)
    ring_sectors = data_dict.get('ring_sectors', [])

    maze_data = MazeData(
        width=data_dict['width'],
        depth=data_dict['depth'],
        cells=data_dict['cells'],
        entrance=tuple(data_dict['entrance']) if data_dict['entrance'] else None,
        exits=[tuple(e) for e in data_dict['exits']],
        center=tuple(data_dict['center']),
        guide_path=[tuple(gp) for gp in data_dict.get('guide_path', [])],
        grid_type=grid_type,
        polar_rings=polar_rings,
        ring_sectors=ring_sectors
    )

    # Recompute guide path
    maze_data.guide_path = find_shortest_path(maze_data, wall_mode=wall_mode)

    # Update stored JSON
    col["fire_maze_data"] = json.dumps({
        'width': maze_data.width,
        'depth': maze_data.depth,
        'cells': maze_data.cells,
        'entrance': maze_data.entrance,
        'exits': maze_data.exits,
        'center': maze_data.center,
        'guide_path': maze_data.guide_path,
        'wall_mode': wall_mode,
        'num_wall_meshes': num_wall_meshes,
        'num_floor_meshes': num_floor_meshes,
        'num_roof_meshes': num_roof_meshes,
        'grid_type': grid_type,
        'polar_rings': polar_rings,
        'ring_sectors': ring_sectors,
    })

    # Clear old maze objects from this collection
    for obj in list(col.objects):
        if obj.get("fire_maze"):
            data = obj.data
            obj_type = obj.type
            bpy.data.objects.remove(obj, do_unlink=True)
            if data and data.users == 0:
                if obj_type == 'MESH':
                    bpy.data.meshes.remove(data)
                elif obj_type == 'CURVE':
                    bpy.data.curves.remove(data)

    # Rebuild
    props = context.scene.fire_maze
    build_maze_objects(props, maze_data, context, collection=col)
    
    # If colliders are enabled, rebuild them too
    if props.generate_colliders:
        build_maze_objects(props, maze_data, context, collection=col, force_simple=True, name_suffix="_Collider")

    # If is_editing is True, rebuild the edit helper!
    if props.is_editing:
        build_maze_objects(props, maze_data, context, collection=col, force_simple=True, name_suffix="_EditHelper")

def _raycast_from_mouse(context, event):
    scene = context.scene
    region = context.region
    rv3d = context.region_data
    coord = event.mouse_region_x, event.mouse_region_y

    view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    
    depsgraph = context.evaluated_depsgraph_get()
    result, location, normal, index, object, matrix = scene.ray_cast(depsgraph, ray_origin, view_vector)
    
    if result:
        return object, location, normal
    return None, None, None

class MAZE_OT_generate(bpy.types.Operator):
    bl_idname = "fire_maze.generate"
    bl_label = "Generate Maze"
    bl_description = "Generate a new maze from current settings"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.fire_maze

        if props.grid_type == 'rect':
            if props.width < 3 or props.depth < 3:
                self.report({'ERROR'}, "Width and Depth must be at least 3")
                return {'CANCELLED'}
        else:
            if props.polar_rings < 2:
                self.report({'ERROR'}, "Rings must be at least 2")
                return {'CANCELLED'}

        # Determine number of meshes in collections
        num_wall_meshes = 0
        if props.custom_wall_collection:
            num_wall_meshes = len([o for o in props.custom_wall_collection.objects if o.type == 'MESH'])

        num_floor_meshes = 0
        if props.custom_floor_collection:
            num_floor_meshes = len([o for o in props.custom_floor_collection.objects if o.type == 'MESH'])

        num_roof_meshes = 0
        if props.custom_roof_collection:
            num_roof_meshes = len([o for o in props.custom_roof_collection.objects if o.type == 'MESH'])

        maze_data = generate_maze(
            width=props.width,
            depth=props.depth,
            seed=props.seed,
            mode=props.mode,
            emergency_exits=props.emergency_exits,
            algorithm=props.algorithm,
            rooms_enable=props.rooms_enable,
            rooms_count=props.rooms_count,
            min_room_size=props.min_room_size,
            max_room_size=props.max_room_size,
            loop_probability=props.loop_probability,
            isolated_wall_prob=props.isolated_wall_prob,
            entrance_side=props.entrance_side,
            exit_side=props.exit_side,
            num_entrances=props.num_entrances,
            num_exits=props.num_exits,
            num_wall_meshes=num_wall_meshes,
            num_floor_meshes=num_floor_meshes,
            num_roof_meshes=num_roof_meshes,
            wall_mode=props.wall_mode,
            mask_image=props.mask_image,
            mask_invert=props.mask_invert,
            grid_type=props.grid_type,
            polar_rings=props.polar_rings,
        )

        col = _find_or_create_maze_collection("FireMaze")
        
        # Serialize and store maze data on the collection
        col["fire_maze_data"] = json.dumps({
            'width': maze_data.width,
            'depth': maze_data.depth,
            'cells': maze_data.cells,
            'entrance': maze_data.entrance,
            'exits': maze_data.exits,
            'center': maze_data.center,
            'guide_path': maze_data.guide_path,
            'wall_mode': props.wall_mode,
            'num_wall_meshes': num_wall_meshes,
            'num_floor_meshes': num_floor_meshes,
            'num_roof_meshes': num_roof_meshes,
            'grid_type': maze_data.grid_type,
            'polar_rings': maze_data.polar_rings,
            'ring_sectors': maze_data.ring_sectors,
        })

        build_maze_objects(props, maze_data, context, collection=col)
        if props.generate_colliders:
            build_maze_objects(props, maze_data, context, collection=col, force_simple=True, name_suffix="_Collider")

        if props.grid_type == 'rect':
            self.report({'INFO'}, f"Maze generated ({props.width}x{props.depth})")
        else:
            self.report({'INFO'}, f"Polar maze generated ({props.polar_rings} rings)")
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
                data = obj.data
                obj_type = obj.type
                bpy.data.objects.remove(obj, do_unlink=True)
                if data and data.users == 0:
                    if obj_type == 'MESH':
                        bpy.data.meshes.remove(data)
                    elif obj_type == 'CURVE':
                        bpy.data.curves.remove(data)
                count += 1

        _remove_maze_collections()

        self.report({'INFO'}, f"Removed {count} maze object(s)")
        return {'FINISHED'}

class MAZE_OT_interactive_edit(bpy.types.Operator):
    bl_idname = "fire_maze.interactive_edit"
    bl_label = "Interactive Maze Editor"
    bl_description = "Left-click on walls in the 3D viewport to toggle them on/off"
    bl_options = {'REGISTER', 'UNDO'}

    def modal(self, context, event):
        if context.area:
            context.area.tag_redraw()

        props = context.scene.fire_maze
        # Clean exit if toggled off by another button click
        if not props.is_editing:
            context.workspace.status_text_set(None)
            self.report({'INFO'}, "Interactive Edit finished")
            return {'FINISHED'}

        # Check if the mouse is within the main 3D viewport WINDOW region coordinates.
        # If the mouse is outside (e.g., in the Menus, Header, or File/Edit menus),
        # we pass the event through so the user can interact with the Blender UI normally.
        if context.region:
            rx_min = context.region.x
            rx_max = rx_min + context.region.width
            ry_min = context.region.y
            ry_max = ry_min + context.region.height
            if not (rx_min <= event.mouse_x <= rx_max and ry_min <= event.mouse_y <= ry_max):
                return {'PASS_THROUGH'}

        # Check if the mouse is inside overlay regions within the viewport area
        # (e.g., the UI/Sidebar N-panel or the Tools shelf T-panel)
        if context.area:
            for region in context.area.regions:
                if region.type != 'WINDOW':
                    rx_min = region.x
                    rx_max = rx_min + region.width
                    ry_min = region.y
                    ry_max = ry_min + region.height
                    if rx_min <= event.mouse_x <= rx_max and ry_min <= event.mouse_y <= ry_max:
                        return {'PASS_THROUGH'}

        if event.type in {'RET', 'NUMPAD_ENTER', 'ESC'}:
            context.workspace.status_text_set(None)
            props.is_editing = False
            delete_edit_helper()
            self.report({'INFO'}, "Interactive Edit finished")
            return {'FINISHED'}

        if event.type == 'LEFTMOUSE':
            if event.value == 'PRESS':
                # Unhide the helper object temporarily
                helper = bpy.data.objects.get("_FireMaze_Edit_Helper")
                if helper:
                    helper.hide_viewport = False
                
                # Temporarily hide all non-helper maze objects to ensure we raycast against the simple helper mesh
                hidden_objs = []
                for o in list(bpy.data.objects):
                    if o.get("fire_maze") and o.name != "_FireMaze_Edit_Helper":
                        if not o.hide_viewport:
                            o.hide_viewport = True
                            hidden_objs.append(o)
                
                # Force view_layer update so the depsgraph evaluates the visibility changes before raycasting!
                context.view_layer.update()
                
                # Perform the raycast
                obj, loc, normal = _raycast_from_mouse(context, event)
                
                # Immediately restore visibility
                if helper:
                    helper.hide_viewport = True
                for o in hidden_objs:
                    o.hide_viewport = False
                
                # Force view_layer update to restore viewport state
                context.view_layer.update()
                
                if obj:
                    col = None
                    for c in bpy.data.collections:
                        if "fire_maze_data" in c:
                            if obj.name in c.objects:
                                col = c
                                break
                    
                    if not col:
                        col = context.collection
                        if "fire_maze_data" not in col:
                            col = None
                            
                    if col:
                        ts = props.tile_size
                        # Offset hit coordinates slightly inward along normal to prevent floating-point boundary issues
                        if normal:
                            offset_loc = loc - normal * 0.01
                        else:
                            offset_loc = loc
                        hit_x, hit_y = offset_loc.x, offset_loc.y
                        
                        data_dict = json.loads(col["fire_maze_data"])
                        width = data_dict['width']
                        depth = data_dict['depth']
                        cells = data_dict['cells']
                        wall_mode = data_dict.get('wall_mode', props.wall_mode)
                        
                        num_wall_meshes = data_dict.get('num_wall_meshes', 0)
                        num_floor_meshes = data_dict.get('num_floor_meshes', 0)
                        num_roof_meshes = data_dict.get('num_roof_meshes', 0)

                        if event.shift:
                            cx = math.floor(hit_x / ts)
                            cy = math.floor(hit_y / ts)
                            if -1 <= cx <= width and -1 <= cy <= depth:
                                # Determine clicked face direction
                                face_dir = 'WALL'
                                if normal:
                                    nx, ny, nz = abs(normal.x), abs(normal.y), abs(normal.z)
                                    if nz > nx and nz > ny:
                                        # Calculate wall height to distinguish floor vs roof
                                        tiled = props.wall_height_tiled
                                        tiles_high = props.wall_height_tiles if tiled else 1
                                        wh = ts * tiles_high if tiled else props.wall_height
                                        # If the hit point is higher than half the wall height, it is the roof
                                        face_dir = 'ROOF' if loc.z > (wh * 0.5) else 'FLOOR'
                                    elif ny > nx and ny > nz:
                                        face_dir = 'N' if normal.y > 0 else 'S'
                                    else:
                                        face_dir = 'E' if normal.x > 0 else 'W'

                                cx_clamped = max(0, min(cx, width - 1))
                                cy_clamped = max(0, min(cy, depth - 1))

                                if wall_mode == 'cube':
                                    is_wall = cells[cy_clamped][cx_clamped][0]
                                    modified = False
                                    rebuilt_text = ""
                                    
                                    # If cube_mode_pillar is enabled, treat all wall faces as a single unit
                                    is_pillar_mode = props.cube_mode_pillar
                                    
                                    if face_dir == 'ROOF':
                                        if is_pillar_mode and is_wall:
                                            if num_wall_meshes > 0:
                                                current_idx = cells[cy_clamped][cx_clamped][1] if isinstance(cells[cy_clamped][cx_clamped][1], int) else -1
                                                next_idx = (current_idx + 1) % num_wall_meshes
                                                cells[cy_clamped][cx_clamped][1] = next_idx
                                                cells[cy_clamped][cx_clamped][2] = next_idx
                                                cells[cy_clamped][cx_clamped][3] = next_idx
                                                cells[cy_clamped][cx_clamped][4] = next_idx
                                                modified = True
                                                rebuilt_text = "pillar"
                                        else:
                                            if num_roof_meshes > 0 and len(cells[cy_clamped][cx_clamped]) > 6:
                                                current_idx = cells[cy_clamped][cx_clamped][6] if isinstance(cells[cy_clamped][cx_clamped][6], int) else -1
                                                cells[cy_clamped][cx_clamped][6] = (current_idx + 1) % num_roof_meshes
                                                modified = True
                                                rebuilt_text = "roof"
                                    elif face_dir == 'FLOOR':
                                        if num_floor_meshes > 0 and len(cells[cy_clamped][cx_clamped]) > 5:
                                            current_idx = cells[cy_clamped][cx_clamped][5] if isinstance(cells[cy_clamped][cx_clamped][5], int) else -1
                                            cells[cy_clamped][cx_clamped][5] = (current_idx + 1) % num_floor_meshes
                                            modified = True
                                            rebuilt_text = "floor"
                                    else:
                                        # Clicked a wall face
                                        if is_wall and num_wall_meshes > 0:
                                            if is_pillar_mode:
                                                # Cycle all wall indices to keep them synchronized
                                                current_idx = cells[cy_clamped][cx_clamped][1] if isinstance(cells[cy_clamped][cx_clamped][1], int) else -1
                                                next_idx = (current_idx + 1) % num_wall_meshes
                                                cells[cy_clamped][cx_clamped][1] = next_idx
                                                cells[cy_clamped][cx_clamped][2] = next_idx
                                                cells[cy_clamped][cx_clamped][3] = next_idx
                                                cells[cy_clamped][cx_clamped][4] = next_idx
                                                modified = True
                                                rebuilt_text = "pillar"
                                            else:
                                                # Cycle face independently
                                                idx_map = {'N': 1, 'S': 2, 'E': 3, 'W': 4}
                                                face_idx = idx_map.get(face_dir, 1)
                                                current_idx = cells[cy_clamped][cx_clamped][face_idx] if isinstance(cells[cy_clamped][cx_clamped][face_idx], int) else -1
                                                cells[cy_clamped][cx_clamped][face_idx] = (current_idx + 1) % num_wall_meshes
                                                modified = True
                                                rebuilt_text = f"wall ({face_dir} face)"
                                                
                                    if modified:
                                        data_dict['cells'] = cells
                                        col["fire_maze_data"] = json.dumps(data_dict)
                                        rebuild_maze_from_collection(context, col)
                                        self.report({'INFO'}, f"Swapped {rebuilt_text} mesh at ({cx_clamped}, {cy_clamped})")
                                    else:
                                        if not is_wall:
                                            if num_floor_meshes > 0:
                                                current_idx = cells[cy_clamped][cx_clamped][5] if isinstance(cells[cy_clamped][cx_clamped][5], int) else -1
                                                cells[cy_clamped][cx_clamped][5] = (current_idx + 1) % num_floor_meshes
                                                data_dict['cells'] = cells
                                                col["fire_maze_data"] = json.dumps(data_dict)
                                                rebuild_maze_from_collection(context, col)
                                                self.report({'INFO'}, f"Swapped floor mesh at ({cx_clamped}, {cy_clamped})")
                                else:
                                    # Thin wall mode
                                    modified = False
                                    rebuilt_text = ""
                                    if face_dir == 'ROOF':
                                        if num_roof_meshes > 0:
                                            if len(cells[cy_clamped][cx_clamped]) > 8:
                                                idx_pos = 9
                                            elif len(cells[cy_clamped][cx_clamped]) > 7:
                                                idx_pos = 7
                                            else:
                                                idx_pos = None
                                            
                                            if idx_pos is not None:
                                                current_idx = cells[cy_clamped][cx_clamped][idx_pos] if isinstance(cells[cy_clamped][cx_clamped][idx_pos], int) else -1
                                                cells[cy_clamped][cx_clamped][idx_pos] = (current_idx + 1) % num_roof_meshes
                                                modified = True
                                                rebuilt_text = "roof"
                                    elif face_dir == 'FLOOR':
                                        if num_floor_meshes > 0:
                                            if len(cells[cy_clamped][cx_clamped]) > 8:
                                                idx_pos = 8
                                            elif len(cells[cy_clamped][cx_clamped]) > 6:
                                                idx_pos = 6
                                            else:
                                                idx_pos = None
                                                
                                            if idx_pos is not None:
                                                current_idx = cells[cy_clamped][cx_clamped][idx_pos] if isinstance(cells[cy_clamped][cx_clamped][idx_pos], int) else -1
                                                cells[cy_clamped][cx_clamped][idx_pos] = (current_idx + 1) % num_floor_meshes
                                                modified = True
                                                rebuilt_text = "floor"
                                    else:
                                        # Clicked a thin wall
                                        if num_wall_meshes > 0:
                                            # Use robust distance-to-edge calculation to locate the clicked wall segment
                                            d_N = abs(hit_y - (cy_clamped + 1) * ts)
                                            d_S = abs(hit_y - cy_clamped * ts)
                                            d_E = abs(hit_x - (cx_clamped + 1) * ts)
                                            d_W = abs(hit_x - cx_clamped * ts)
                                            
                                            min_d = min(d_N, d_S, d_E, d_W)
                                            
                                            if len(cells[cy_clamped][cx_clamped]) > 8:
                                                # 10-item format
                                                if min_d == d_N:
                                                     current_idx = cells[cy_clamped][cx_clamped][4] if isinstance(cells[cy_clamped][cx_clamped][4], int) else -1
                                                     next_idx = (current_idx + 1) % num_wall_meshes
                                                     cells[cy_clamped][cx_clamped][4] = next_idx
                                                     if cy_clamped + 1 < depth:
                                                         cells[cy_clamped + 1][cx_clamped][5] = next_idx
                                                     modified = True
                                                     rebuilt_text = "horizontal wall (North)"
                                                elif min_d == d_S:
                                                     current_idx = cells[cy_clamped][cx_clamped][5] if isinstance(cells[cy_clamped][cx_clamped][5], int) else -1
                                                     next_idx = (current_idx + 1) % num_wall_meshes
                                                     cells[cy_clamped][cx_clamped][5] = next_idx
                                                     if cy_clamped - 1 >= 0:
                                                         cells[cy_clamped - 1][cx_clamped][4] = next_idx
                                                     modified = True
                                                     rebuilt_text = "horizontal wall (South)"
                                                elif min_d == d_E:
                                                     current_idx = cells[cy_clamped][cx_clamped][6] if isinstance(cells[cy_clamped][cx_clamped][6], int) else -1
                                                     next_idx = (current_idx + 1) % num_wall_meshes
                                                     cells[cy_clamped][cx_clamped][6] = next_idx
                                                     if cx_clamped + 1 < width:
                                                         cells[cy_clamped][cx_clamped + 1][7] = next_idx
                                                     modified = True
                                                     rebuilt_text = "vertical wall (East)"
                                                else:  # min_d == d_W
                                                     current_idx = cells[cy_clamped][cx_clamped][7] if isinstance(cells[cy_clamped][cx_clamped][7], int) else -1
                                                     next_idx = (current_idx + 1) % num_wall_meshes
                                                     cells[cy_clamped][cx_clamped][7] = next_idx
                                                     if cx_clamped - 1 >= 0:
                                                         cells[cy_clamped][cx_clamped - 1][6] = next_idx
                                                     modified = True
                                                     rebuilt_text = "vertical wall (West)"
                                            else:
                                                # Old 8-item format
                                                if min_d == d_N or min_d == d_S:
                                                    target_y = cy_clamped + 1 if min_d == d_N else cy_clamped
                                                    target_y = min(target_y, depth - 1)
                                                    current_idx = cells[target_y][cx_clamped][4] if isinstance(cells[target_y][cx_clamped][4], int) else -1
                                                    cells[target_y][cx_clamped][4] = (current_idx + 1) % num_wall_meshes
                                                    modified = True
                                                    rebuilt_text = "horizontal wall"
                                                else:
                                                    target_x = cx_clamped + 1 if min_d == d_E else cx_clamped
                                                    target_x = min(target_x, width - 1)
                                                    current_idx = cells[cy_clamped][target_x][5] if isinstance(cells[cy_clamped][target_x][5], int) else -1
                                                    cells[cy_clamped][target_x][5] = (current_idx + 1) % num_wall_meshes
                                                    modified = True
                                                    rebuilt_text = "vertical wall"
                                            
                                    if modified:
                                        data_dict['cells'] = cells
                                        col["fire_maze_data"] = json.dumps(data_dict)
                                        rebuild_maze_from_collection(context, col)
                                        self.report({'INFO'}, f"Swapped {rebuilt_text} mesh at ({cx_clamped}, {cy_clamped})")
                        else:
                            if wall_mode == 'cube':
                                cx = math.floor(hit_x / ts)
                                cy = math.floor(hit_y / ts)
                                cx_clamped = max(0, min(cx, width - 1))
                                cy_clamped = max(0, min(cy, depth - 1))
                                if 0 <= cx < width and 0 <= cy < depth:
                                    cells[cy_clamped][cx_clamped][0] = not cells[cy_clamped][cx_clamped][0]
                                    if cells[cy_clamped][cx_clamped][0]:
                                        if num_wall_meshes > 0:
                                            cells[cy_clamped][cx_clamped][1] = random.randrange(num_wall_meshes)
                                            cells[cy_clamped][cx_clamped][2] = random.randrange(num_wall_meshes)
                                            cells[cy_clamped][cx_clamped][3] = random.randrange(num_wall_meshes)
                                            cells[cy_clamped][cx_clamped][4] = random.randrange(num_wall_meshes)
                                        if num_roof_meshes > 0:
                                            cells[cy_clamped][cx_clamped][6] = random.randrange(num_roof_meshes)
                                    else:
                                        if num_floor_meshes > 0:
                                            cells[cy_clamped][cx_clamped][5] = random.randrange(num_floor_meshes)
                                    data_dict['cells'] = cells
                                    col["fire_maze_data"] = json.dumps(data_dict)
                                    rebuild_maze_from_collection(context, col)
                                    self.report({'INFO'}, f"Toggled wall tile at ({cx_clamped}, {cy_clamped})")
                            else:
                                # Thin wall mode
                                cx = math.floor(hit_x / ts)
                                cy = math.floor(hit_y / ts)
                                if -1 <= cx <= width and -1 <= cy <= depth:
                                    cx_clamped = max(0, min(cx, width - 1))
                                    cy_clamped = max(0, min(cy, depth - 1))
                                    
                                    d_N = abs(hit_y - (cy_clamped + 1) * ts)
                                    d_S = abs(hit_y - cy_clamped * ts)
                                    d_E = abs(hit_x - (cx_clamped + 1) * ts)
                                    d_W = abs(hit_x - cx_clamped * ts)
                                    
                                    min_d = min(d_N, d_S, d_E, d_W)
                                    
                                    if min_d == d_N:
                                        cells[cy_clamped][cx_clamped][0] = not cells[cy_clamped][cx_clamped][0]
                                        if cy_clamped + 1 < depth:
                                            cells[cy_clamped + 1][cx_clamped][1] = cells[cy_clamped][cx_clamped][0]
                                    elif min_d == d_S:
                                        cells[cy_clamped][cx_clamped][1] = not cells[cy_clamped][cx_clamped][1]
                                        if cy_clamped - 1 >= 0:
                                            cells[cy_clamped - 1][cx_clamped][0] = cells[cy_clamped][cx_clamped][1]
                                    elif min_d == d_E:
                                        cells[cy_clamped][cx_clamped][2] = not cells[cy_clamped][cx_clamped][2]
                                        if cx_clamped + 1 < width:
                                            cells[cy_clamped][cx_clamped + 1][3] = cells[cy_clamped][cx_clamped][2]
                                    else:
                                        cells[cy_clamped][cx_clamped][3] = not cells[cy_clamped][cx_clamped][3]
                                        if cx_clamped - 1 >= 0:
                                            cells[cy_clamped][cx_clamped - 1][2] = cells[cy_clamped][cx_clamped][3]
                                            
                                    data_dict['cells'] = cells
                                    col["fire_maze_data"] = json.dumps(data_dict)
                                    rebuild_maze_from_collection(context, col)
                                    self.report({'INFO'}, f"Toggled wall at cell ({cx_clamped}, {cy_clamped})")
            return {'RUNNING_MODAL'}


        return {'PASS_THROUGH'}

    def invoke(self, context, event):
        props = context.scene.fire_maze
        
        # If already editing, act as a toggle off
        if props.is_editing:
            props.is_editing = False
            context.workspace.status_text_set(None)
            delete_edit_helper()
            self.report({'INFO'}, "Interactive Edit finished")
            return {'FINISHED'}

        col = None
        for c in bpy.data.collections:
            if "fire_maze_data" in c:
                col = c
                break
                
        if not col:
            self.report({'ERROR'}, "No generated maze found to edit. Please generate a maze first.")
            return {'CANCELLED'}
            
        props.is_editing = True
        rebuild_maze_from_collection(context, col)
        context.workspace.status_text_set("FireMaze Editor: Left-click walls to toggle. Shift+Left-click to cycle mesh. Enter/Esc to exit.")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


class MAZE_OT_save_as_image(bpy.types.Operator):
    bl_idname = "fire_maze.save_as_image"
    bl_label = "Save Maze as Image"
    bl_description = "Export the current maze layout as a black-and-white image in the Blender database"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        col = bpy.data.collections.get("FireMaze")
        if not col or "fire_maze_data" not in col:
            self.report({'ERROR'}, "No active maze layout found. Please generate a maze first.")
            return {'CANCELLED'}

        import json
        try:
            data = json.loads(col["fire_maze_data"])
            if data.get('grid_type', 'rect') == 'polar':
                self.report({'ERROR'}, "Save Maze as Image is currently only supported for Rectangular mazes.")
                return {'CANCELLED'}
            width = data["width"]
            depth = data["depth"]
            cells = data["cells"]
            wall_mode = data.get("wall_mode", "thin")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to read maze data: {e}")
            return {'CANCELLED'}

        img_name = "FireMaze_Layout"
        # Remove existing image if it exists to refresh
        old_img = bpy.data.images.get(img_name)
        if old_img:
            bpy.data.images.remove(old_img)

        # Create new image block
        if wall_mode == 'cube':
            img_w, img_h = width, depth
        else: # thin mode, upscale 3x
            img_w, img_h = width * 3, depth * 3

        img = bpy.data.images.new(name=img_name, width=img_w, height=img_h, alpha=False, float_buffer=False)

        # Buffer size is img_w * img_h * 4 (RGBA channels)
        pixels = [0.0] * (img_w * img_h * 4)

        if wall_mode == 'cube':
            for y in range(depth):
                for x in range(width):
                    is_wall = cells[y][x][0]
                    val = 0.0 if is_wall else 1.0
                    idx = (y * img_w + x) * 4
                    pixels[idx] = val
                    pixels[idx + 1] = val
                    pixels[idx + 2] = val
                    pixels[idx + 3] = 1.0
        else: # thin mode
            for y in range(depth):
                for x in range(width):
                    c = cells[y][x]
                    bx, by = x * 3, y * 3
                    
                    # Center is walkable
                    c_idx = ((by + 1) * img_w + (bx + 1)) * 4
                    pixels[c_idx] = pixels[c_idx+1] = pixels[c_idx+2] = 1.0
                    pixels[c_idx+3] = 1.0
                    
                    # North (no North wall)
                    if not c[0]:
                        n_idx = ((by + 2) * img_w + (bx + 1)) * 4
                        pixels[n_idx] = pixels[n_idx+1] = pixels[n_idx+2] = 1.0
                        pixels[n_idx+3] = 1.0
                        
                    # South (no South wall)
                    if not c[1]:
                        s_idx = (by * img_w + (bx + 1)) * 4
                        pixels[s_idx] = pixels[s_idx+1] = pixels[s_idx+2] = 1.0
                        pixels[s_idx+3] = 1.0
                        
                    # East (no East wall)
                    if not c[2]:
                        e_idx = ((by + 1) * img_w + (bx + 2)) * 4
                        pixels[e_idx] = pixels[e_idx+1] = pixels[e_idx+2] = 1.0
                        pixels[e_idx+3] = 1.0
                        
                    # West (no West wall)
                    if not c[3]:
                        w_idx = ((by + 1) * img_w + bx) * 4
                        pixels[w_idx] = pixels[w_idx+1] = pixels[w_idx+2] = 1.0
                        pixels[w_idx+3] = 1.0

        # Set pixel data
        img.pixels = pixels
        img.update()
        
        self.report({'INFO'}, f"Saved maze layout as image '{img_name}' (Size: {img_w}x{img_h})")
        return {'FINISHED'}


classes = (MAZE_OT_generate, MAZE_OT_clear, MAZE_OT_interactive_edit, MAZE_OT_save_as_image)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
