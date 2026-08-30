from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from geometry_tokens import GeometryDocument, PrimitiveToken, load_geometry_tokens


def _script_args() -> list[str]:
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1 :]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile Geometry Tokens into a Blender Geometry Nodes asset"
    )
    parser.add_argument("spec", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=SCRIPT_DIR / "output" / "iridescent_starling.blend",
    )
    parser.add_argument(
        "--render",
        type=Path,
        default=SCRIPT_DIR / "output" / "iridescent_starling.png",
    )
    parser.add_argument(
        "--export",
        type=Path,
        default=SCRIPT_DIR / "output" / "iridescent_starling.glb",
    )
    return parser.parse_args(_script_args())


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def _socket(node, names: tuple[str, ...]):
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            return socket
    available = ", ".join(socket.name for socket in node.inputs)
    raise RuntimeError(
        f"{node.bl_idname}: expected one of {names!r}; available inputs: {available}"
    )


def _output(node, names: tuple[str, ...]):
    for name in names:
        socket = node.outputs.get(name)
        if socket is not None:
            return socket
    available = ", ".join(socket.name for socket in node.outputs)
    raise RuntimeError(
        f"{node.bl_idname}: expected one of {names!r}; available outputs: {available}"
    )


def _set(node, names: tuple[str, ...], value) -> None:
    _socket(node, names).default_value = value


def make_iridescent_material(name: str, metallic: float, roughness: float):
    material = bpy.data.materials.new(name=name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (520, 0)
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.location = (220, 0)
    layer = nodes.new("ShaderNodeLayerWeight")
    layer.location = (-520, 40)
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.location = (-220, 40)

    ramp.color_ramp.elements.remove(ramp.color_ramp.elements[1])
    stops = [
        (0.00, (0.008, 0.012, 0.026, 1.0)),
        (0.20, (0.025, 0.085, 0.170, 1.0)),
        (0.42, (0.035, 0.260, 0.220, 1.0)),
        (0.63, (0.220, 0.055, 0.320, 1.0)),
        (0.82, (0.055, 0.120, 0.360, 1.0)),
        (1.00, (0.018, 0.025, 0.055, 1.0)),
    ]
    first = ramp.color_ramp.elements[0]
    first.position = stops[0][0]
    first.color = stops[0][1]
    for position, color in stops[1:]:
        element = ramp.color_ramp.elements.new(position)
        element.color = color

    _set(shader, ("Metallic",), metallic)
    _set(shader, ("Roughness",), roughness)
    coat = shader.inputs.get("Coat Weight") or shader.inputs.get("Coat")
    if coat is not None:
        coat.default_value = 0.34

    links.new(_output(layer, ("Facing",)), _socket(ramp, ("Fac",)))
    links.new(_output(ramp, ("Color",)), _socket(shader, ("Base Color",)))
    links.new(_output(shader, ("BSDF",)), _socket(output, ("Surface",)))
    return material


def make_pbr_material(name: str, params: tuple[float, ...]):
    r, g, b, metallic, roughness = params
    material = bpy.data.materials.new(name=name)
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    if shader is None:
        raise RuntimeError("Blender Principled BSDF node unavailable")
    _set(shader, ("Base Color",), (r, g, b, 1.0))
    _set(shader, ("Metallic",), metallic)
    _set(shader, ("Roughness",), roughness)
    return material


def build_materials(document: GeometryDocument) -> dict[str, object]:
    result = {}
    for token in document.materials:
        if token.kind == "iridescent":
            result[token.name] = make_iridescent_material(token.name, *token.parameters)
        elif token.kind == "pbr":
            result[token.name] = make_pbr_material(token.name, token.parameters)
        else:
            raise RuntimeError(f"unsupported material kind: {token.kind}")
    return result


def _transform_node(nodes, token: PrimitiveToken, x: float, y: float):
    transform = nodes.new("GeometryNodeTransform")
    transform.label = token.name
    transform.location = (x, y)
    _set(transform, ("Translation",), token.location)
    _set(
        transform,
        ("Rotation",),
        tuple(math.radians(v) for v in token.rotation_deg),
    )
    return transform


def _primitive_node(nodes, token: PrimitiveToken, x: float, y: float):
    if token.kind == "ellipsoid":
        primitive = nodes.new("GeometryNodeMeshIcoSphere")
        primitive.location = (x, y)
        primitive.label = token.name
        _set(primitive, ("Radius",), 1.0)
        _set(primitive, ("Subdivisions",), 4)
        return primitive, token.parameters

    if token.kind == "cone":
        primitive = nodes.new("GeometryNodeMeshCone")
        primitive.location = (x, y)
        primitive.label = token.name
        radius_bottom, radius_top, depth = token.parameters
        _set(primitive, ("Vertices",), 48)
        _set(primitive, ("Side Segments",), 1)
        _set(primitive, ("Fill Segments",), 1)
        _set(primitive, ("Radius Bottom",), radius_bottom)
        _set(primitive, ("Radius Top",), radius_top)
        _set(primitive, ("Depth",), depth)
        return primitive, (1.0, 1.0, 1.0)

    raise RuntimeError(f"unsupported primitive kind: {token.kind}")


def build_geometry_nodes(
    document: GeometryDocument,
    materials: dict[str, object],
):
    mesh = bpy.data.meshes.new(f"{document.asset}.source")
    obj = bpy.data.objects.new(document.asset, mesh)
    bpy.context.collection.objects.link(obj)

    group = bpy.data.node_groups.new(
        f"GTOK::{document.asset}",
        "GeometryNodeTree",
    )
    group.interface.new_socket(
        name="Geometry",
        in_out="OUTPUT",
        socket_type="NodeSocketGeometry",
    )
    nodes = group.nodes
    links = group.links

    output = nodes.new("NodeGroupOutput")
    output.location = (720, 0)
    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (460, 0)

    for index, token in enumerate(document.primitives):
        row = -index * 150
        primitive, scale = _primitive_node(nodes, token, -760, row)
        transform = _transform_node(nodes, token, -500, row)
        _set(transform, ("Scale",), scale)

        material_node = nodes.new("GeometryNodeSetMaterial")
        material_node.location = (-200, row)
        material_node.label = token.material
        _set(material_node, ("Material",), materials[token.material])

        links.new(
            _output(primitive, ("Mesh", "Geometry")),
            _socket(transform, ("Geometry",)),
        )
        links.new(
            _output(transform, ("Geometry",)),
            _socket(material_node, ("Geometry",)),
        )
        links.new(
            _output(material_node, ("Geometry",)),
            _socket(join, ("Geometry",)),
        )

    links.new(
        _output(join, ("Geometry",)),
        _socket(output, ("Geometry",)),
    )

    modifier = obj.modifiers.new(name="Geometry Tokens", type="NODES")
    modifier.node_group = group
    return obj, modifier


def apply_geometry(obj, modifier) -> None:
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    for polygon in obj.data.polygons:
        polygon.use_smooth = True


def add_ground() -> None:
    bpy.ops.mesh.primitive_plane_add(size=18, location=(0, 0, -1.02))
    ground = bpy.context.object
    ground.name = "Studio Ground"

    material = bpy.data.materials.new(name="Studio Ground")
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    _set(shader, ("Base Color",), (0.012, 0.014, 0.020, 1.0))
    _set(shader, ("Roughness",), 0.34)
    ground.data.materials.append(material)


def point_at(obj, target: tuple[float, float, float]) -> None:
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def add_camera() -> None:
    bpy.ops.object.camera_add(location=(3.65, -5.8, 2.55))
    camera = bpy.context.object
    camera.name = "Studio Camera"
    camera.data.lens = 62
    point_at(camera, (0.0, 0.0, 0.12))
    bpy.context.scene.camera = camera


def add_area(
    name: str,
    location,
    energy: float,
    size: float,
    color,
    target=(0.0, 0.0, 0.15),
) -> None:
    bpy.ops.object.light_add(type="AREA", location=location)
    light = bpy.context.object
    light.name = name
    light.data.energy = energy
    light.data.shape = "DISK"
    light.data.size = size
    light.data.color = color
    point_at(light, target)


def add_lighting() -> None:
    add_area("Key", (-3.6, -4.0, 5.0), 1050, 4.0, (0.80, 0.90, 1.00))
    add_area("Fill", (4.5, -1.5, 2.4), 720, 3.0, (0.78, 0.58, 1.00))
    add_area("Rim", (0.8, 4.4, 3.8), 1250, 2.5, (0.40, 0.88, 1.00))


def configure_render(render_path: Path) -> None:
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"

    scene.render.resolution_x = 900
    scene.render.resolution_y = 900
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(render_path)
    scene.render.film_transparent = False
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    try:
        scene.view_settings.look = "AgX - Medium High Contrast"
    except TypeError:
        pass

    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (0.006, 0.008, 0.014, 1.0)
    background.inputs["Strength"].default_value = 0.16


def export_glb(obj, path: Path) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
    )


def main() -> None:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.render.parent.mkdir(parents=True, exist_ok=True)
    args.export.parent.mkdir(parents=True, exist_ok=True)

    document = load_geometry_tokens(args.spec)

    clear_scene()
    materials = build_materials(document)
    obj, modifier = build_geometry_nodes(document, materials)
    apply_geometry(obj, modifier)

    add_ground()
    add_camera()
    add_lighting()
    configure_render(args.render)

    bpy.context.scene.render.filepath = str(args.render)
    bpy.ops.render.render(write_still=True)
    export_glb(obj, args.export)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output))

    print(f"GTOK asset: {document.asset}")
    print(f"GTOK primitives: {len(document.primitives)}")
    print(f"GTOK render: {args.render}")
    print(f"GTOK GLB: {args.export}")
    print(f"GTOK blend: {args.output}")


if __name__ == "__main__":
    main()
