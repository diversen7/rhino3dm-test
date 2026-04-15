#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path

import rhino3dm


def object_type_name(obj) -> str:
    return str(obj.ObjectType)


def safe_layer_name(model: rhino3dm.File3dm, layer_index: int) -> str:
    try:
        if 0 <= layer_index < len(model.Layers):
            return model.Layers[layer_index].Name or f"Layer_{layer_index}"
    except Exception:
        pass
    return "Default"


def safe_name(text: str, fallback: str) -> str:
    text = (text or "").strip()
    if not text:
        text = fallback
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text or fallback


def mesh_has_geometry(mesh) -> bool:
    if mesh is None:
        return False
    try:
        return len(mesh.Vertices) > 0 and len(mesh.Faces) > 0
    except Exception:
        return False


def color_tuple_from_rgba(color, default: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    if color is None:
        return default

    channels = []
    for channel in ("R", "G", "B", "A"):
        value = getattr(color, channel, None)
        if value is None:
            return default
        channels.append(max(0.0, min(1.0, float(value) / 255.0)))

    return channels[0], channels[1], channels[2], channels[3]


def best_effort_display_color(
    model: rhino3dm.File3dm,
    attrs,
    default: tuple[float, float, float, float] = (0.8, 0.8, 0.8, 1.0),
) -> tuple[float, float, float, float]:
    for getter_name in ("DrawColor", "ObjectColor", "PlotColor"):
        getter = getattr(attrs, getter_name, None)
        if callable(getter):
            try:
                color = getter(model)
                return color_tuple_from_rgba(color, default)
            except Exception:
                pass
        elif getter is not None:
            return color_tuple_from_rgba(getter, default)

    try:
        layer = model.Layers[attrs.LayerIndex]
        color = getattr(layer, "Color", None)
        return color_tuple_from_rgba(color, default)
    except Exception:
        return default


def append_mesh_record(records, meshes, name: str, layer: str, source: str, color):
    valid_meshes = [mesh for mesh in meshes if mesh_has_geometry(mesh)]
    if not valid_meshes:
        return

    records.append(
        {
            "meshes": valid_meshes,
            "name": safe_name(name, "mesh"),
            "layer": safe_name(layer, "Default"),
            "source": source,
            "color": color,
        }
    )


def try_get_meshes_from_brep(brep):
    meshes = []
    mesh_types = [
        rhino3dm.MeshType.Any,
        rhino3dm.MeshType.Render,
        rhino3dm.MeshType.Preview,
        rhino3dm.MeshType.Analysis,
    ]

    seen = set()

    try:
        face_count = len(brep.Faces)
    except Exception:
        return []

    for face_index in range(face_count):
        try:
            face = brep.Faces[face_index]
        except Exception:
            continue

        for mesh_type in mesh_types:
            try:
                mesh = face.GetMesh(mesh_type)
            except Exception:
                mesh = None

            if not mesh_has_geometry(mesh):
                continue

            key = (face_index, int(mesh_type))
            if key in seen:
                continue
            seen.add(key)
            meshes.append(mesh)
            break

    return meshes


def try_get_meshes_from_extrusion(extrusion):
    mesh_types = [
        rhino3dm.MeshType.Any,
        rhino3dm.MeshType.Render,
        rhino3dm.MeshType.Preview,
        rhino3dm.MeshType.Analysis,
    ]

    for mesh_type in mesh_types:
        try:
            mesh = extrusion.GetMesh(mesh_type)
        except Exception:
            mesh = None

        if mesh_has_geometry(mesh):
            return [mesh]

    return []


def triangulated_face_indices(face) -> list[tuple[int, int, int]]:
    if isinstance(face, (tuple, list)):
        values = list(face)
        if len(values) == 3:
            return [(values[0], values[1], values[2])]
        if len(values) >= 4:
            a, b, c, d = values[:4]
            if d == c:
                return [(a, b, c)]
            return [(a, b, c), (a, c, d)]
        return []

    a = face.A
    b = face.B
    c = face.C
    d = face.D
    if getattr(face, "IsQuad", False):
        return [(a, b, c), (a, c, d)]
    return [(a, b, c)]


def convert_point_to_gltf_space(point) -> tuple[float, float, float]:
    # Rhino models are typically Z-up. glTF is Y-up.
    return float(point.X), float(point.Z), float(-point.Y)


def pad4(data: bytearray) -> None:
    while len(data) % 4 != 0:
        data.append(0)


def add_buffer_view_and_accessor(
    binary_blob: bytearray,
    buffer_views: list[dict],
    accessors: list[dict],
    packed_data: bytes,
    target: int,
    component_type: int,
    count: int,
    accessor_type: str,
    min_values=None,
    max_values=None,
) -> int:
    pad4(binary_blob)
    byte_offset = len(binary_blob)
    binary_blob.extend(packed_data)

    buffer_view_index = len(buffer_views)
    buffer_views.append(
        {
            "buffer": 0,
            "byteOffset": byte_offset,
            "byteLength": len(packed_data),
            "target": target,
        }
    )

    accessor = {
        "bufferView": buffer_view_index,
        "componentType": component_type,
        "count": count,
        "type": accessor_type,
    }
    if min_values is not None:
        accessor["min"] = min_values
    if max_values is not None:
        accessor["max"] = max_values

    accessor_index = len(accessors)
    accessors.append(accessor)
    return accessor_index


def pack_mesh_data(mesh) -> tuple[bytes, bytes | None, bytes, list[float], list[float], int]:
    positions = []
    normals = []
    indices = []

    for vertex in mesh.Vertices:
        x, y, z = convert_point_to_gltf_space(vertex)
        positions.extend((x, y, z))

    has_normals = False
    try:
        has_normals = len(mesh.Normals) == len(mesh.Vertices) and len(mesh.Normals) > 0
    except Exception:
        has_normals = False

    if has_normals:
        for normal in mesh.Normals:
            x, y, z = convert_point_to_gltf_space(normal)
            normals.extend((x, y, z))

    for face in mesh.Faces:
        for triangle in triangulated_face_indices(face):
            indices.extend(triangle)

    if not positions or not indices:
        return b"", None, b"", [], [], 0

    xs = positions[0::3]
    ys = positions[1::3]
    zs = positions[2::3]
    min_values = [min(xs), min(ys), min(zs)]
    max_values = [max(xs), max(ys), max(zs)]

    position_bytes = struct.pack(f"<{len(positions)}f", *positions)
    normal_bytes = struct.pack(f"<{len(normals)}f", *normals) if has_normals else None

    max_index = max(indices)
    if max_index < 65535:
        index_component_type = 5123
        index_bytes = struct.pack(f"<{len(indices)}H", *indices)
    else:
        index_component_type = 5125
        index_bytes = struct.pack(f"<{len(indices)}I", *indices)

    return position_bytes, normal_bytes, index_bytes, min_values, max_values, index_component_type


def build_gltf(mesh_records):
    binary_blob = bytearray()
    buffer_views = []
    accessors = []
    materials = []
    material_map = {}
    meshes = []
    nodes = []

    for record in mesh_records:
        color = record["color"]
        material_key = tuple(round(channel, 6) for channel in color)
        if material_key not in material_map:
            alpha_mode = "BLEND" if color[3] < 0.999 else "OPAQUE"
            material_map[material_key] = len(materials)
            materials.append(
                {
                    "name": f"mat_{len(materials)}",
                    "pbrMetallicRoughness": {
                        "baseColorFactor": [color[0], color[1], color[2], color[3]],
                        "metallicFactor": 0.0,
                        "roughnessFactor": 1.0,
                    },
                    "doubleSided": True,
                    "alphaMode": alpha_mode,
                }
            )

        primitives = []
        for mesh in record["meshes"]:
            position_bytes, normal_bytes, index_bytes, min_values, max_values, index_component_type = pack_mesh_data(mesh)
            if not position_bytes or not index_bytes:
                continue

            position_accessor = add_buffer_view_and_accessor(
                binary_blob,
                buffer_views,
                accessors,
                position_bytes,
                target=34962,
                component_type=5126,
                count=len(position_bytes) // 12,
                accessor_type="VEC3",
                min_values=min_values,
                max_values=max_values,
            )

            normal_accessor = None
            if normal_bytes is not None:
                normal_accessor = add_buffer_view_and_accessor(
                    binary_blob,
                    buffer_views,
                    accessors,
                    normal_bytes,
                    target=34962,
                    component_type=5126,
                    count=len(normal_bytes) // 12,
                    accessor_type="VEC3",
                )

            index_accessor = add_buffer_view_and_accessor(
                binary_blob,
                buffer_views,
                accessors,
                index_bytes,
                target=34963,
                component_type=index_component_type,
                count=(len(index_bytes) // 2) if index_component_type == 5123 else (len(index_bytes) // 4),
                accessor_type="SCALAR",
            )

            attributes = {"POSITION": position_accessor}
            if normal_accessor is not None:
                attributes["NORMAL"] = normal_accessor

            primitives.append(
                {
                    "attributes": attributes,
                    "indices": index_accessor,
                    "material": material_map[material_key],
                    "mode": 4,
                }
            )

        if not primitives:
            continue

        mesh_index = len(meshes)
        meshes.append(
            {
                "name": record["name"],
                "primitives": primitives,
            }
        )
        nodes.append(
            {
                "name": record["name"],
                "mesh": mesh_index,
                "extras": {
                    "layer": record["layer"],
                    "source": record["source"],
                },
            }
        )

    gltf = {
        "asset": {
            "version": "2.0",
            "generator": "extract_3dm_to_gltf.py",
        },
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "buffers": [
            {
                "byteLength": len(binary_blob),
            }
        ],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }

    return gltf, bytes(binary_blob), len(nodes)


def resolve_output_path(input_path: Path, output_path: Path | None) -> Path:
    default_name = f"{input_path.stem}.gltf"
    if output_path is None:
        resolved = Path.cwd() / default_name
    elif output_path.exists() and output_path.is_dir():
        resolved = output_path / default_name
    elif output_path.suffix == "":
        resolved = output_path / default_name
    else:
        resolved = output_path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract mesh-style geometry from a Rhino .3dm file and export glTF 2.0"
    )
    parser.add_argument("input_3dm", type=Path, help="Input .3dm file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .gltf path, or directory for the default filename (default: CWD/input-stem.gltf)",
    )
    parser.add_argument(
        "--layer",
        action="append",
        default=[],
        help="Only include these layer names (can be repeated)",
    )
    parser.add_argument(
        "--no-brep-face-meshes",
        action="store_true",
        help="Do not try to extract cached meshes from Brep faces",
    )
    parser.add_argument(
        "--no-extrusion-meshes",
        action="store_true",
        help="Do not try to extract cached meshes from Extrusion objects",
    )

    args = parser.parse_args()

    input_path = args.input_3dm
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    output_path = resolve_output_path(input_path, args.output)
    bin_path = output_path.with_suffix(".bin")

    model = rhino3dm.File3dm.Read(str(input_path))
    if model is None:
        print(f"Could not read Rhino file: {input_path}", file=sys.stderr)
        return 1

    selected_layers = set(args.layer) if args.layer else None

    mesh_records = []
    direct_mesh_count = 0
    brep_mesh_count = 0
    extrusion_mesh_count = 0

    for index, obj in enumerate(model.Objects):
        geom = obj.Geometry
        attrs = obj.Attributes
        obj_type = object_type_name(geom)
        layer_name = safe_layer_name(model, attrs.LayerIndex)
        obj_name = attrs.Name or f"object_{index}"
        color = best_effort_display_color(model, attrs)

        if selected_layers is not None and layer_name not in selected_layers:
            continue

        if obj_type == "ObjectType.Mesh":
            append_mesh_record(
                mesh_records,
                [geom],
                obj_name,
                layer_name,
                "mesh_object",
                color,
            )
            direct_mesh_count += 1

        elif obj_type == "ObjectType.Brep" and not args.no_brep_face_meshes:
            meshes = try_get_meshes_from_brep(geom)
            append_mesh_record(
                mesh_records,
                meshes,
                obj_name,
                layer_name,
                "brep_faces",
                color,
            )
            brep_mesh_count += len(meshes)

        elif obj_type == "ObjectType.Extrusion" and not args.no_extrusion_meshes:
            meshes = try_get_meshes_from_extrusion(geom)
            append_mesh_record(
                mesh_records,
                meshes,
                obj_name,
                layer_name,
                "extrusion",
                color,
            )
            extrusion_mesh_count += len(meshes)

    if not mesh_records:
        print("No exportable mesh data found.", file=sys.stderr)
        print(
            "This usually means the file has no mesh objects and no cached/render meshes on Breps or extrusions.",
            file=sys.stderr,
        )
        print(
            "With rhino3dm alone, that can happen even when Brep geometry exists.",
            file=sys.stderr,
        )
        return 2

    gltf, binary_blob, node_count = build_gltf(mesh_records)
    if not node_count:
        print("No valid glTF primitives could be created from the extracted meshes.", file=sys.stderr)
        return 3

    gltf["buffers"][0]["uri"] = bin_path.name
    output_path.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    bin_path.write_bytes(binary_blob)

    total_vertices = sum(len(mesh.Vertices) for rec in mesh_records for mesh in rec["meshes"])
    total_faces = sum(len(mesh.Faces) for rec in mesh_records for mesh in rec["meshes"])

    print(f"Wrote glTF: {output_path}")
    print(f"Wrote BIN: {bin_path}")
    print(f"Scene nodes exported: {node_count}")
    print(f"Mesh records exported: {len(mesh_records)}")
    print(f"Direct mesh objects: {direct_mesh_count}")
    print(f"Brep face meshes: {brep_mesh_count}")
    print(f"Extrusion meshes: {extrusion_mesh_count}")
    print(f"Total vertices: {total_vertices}")
    print(f"Total faces: {total_faces}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
