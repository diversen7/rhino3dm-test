#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
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


def append_mesh_record(records, mesh, name: str, layer: str, source: str):
    if not mesh_has_geometry(mesh):
        return
    records.append(
        {
            "meshes": [mesh],
            "name": safe_name(name, "mesh"),
            "layer": safe_name(layer, "Default"),
            "source": source,
        }
    )


def try_get_meshes_from_brep(brep, base_name: str, layer_name: str):
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

    if not meshes:
        return []

    return [
        {
            "meshes": meshes,
            "name": safe_name(base_name, "mesh"),
            "layer": safe_name(layer_name, "Default"),
            "source": "brep_faces",
        }
    ]


def try_get_meshes_from_extrusion(extrusion, base_name: str, layer_name: str):
    records = []
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
            append_mesh_record(
                records,
                mesh,
                f"{base_name}_{mesh_type.name.lower()}",
                layer_name,
                f"extrusion:{mesh_type.name}",
            )
            break

    return records


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


def triangle_normal(v1, v2, v3) -> tuple[float, float, float]:
    ux = v2.X - v1.X
    uy = v2.Y - v1.Y
    uz = v2.Z - v1.Z
    vx = v3.X - v1.X
    vy = v3.Y - v1.Y
    vz = v3.Z - v1.Z

    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx

    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length <= 1e-12:
        return 0.0, 0.0, 0.0
    return nx / length, ny / length, nz / length


def triangle_records(mesh_records):
    for record in mesh_records:
        for mesh in record["meshes"]:
            vertices = mesh.Vertices
            for face in mesh.Faces:
                for a, b, c in triangulated_face_indices(face):
                    try:
                        v1 = vertices[a]
                        v2 = vertices[b]
                        v3 = vertices[c]
                    except Exception:
                        continue
                    yield v1, v2, v3


def write_binary_stl(mesh_records, out_path: Path, solid_name: str):
    triangles = list(triangle_records(mesh_records))

    header_text = f"Binary STL exported from Rhino .3dm via rhino3dm | {solid_name}"
    header = header_text.encode("ascii", errors="replace")[:80].ljust(80, b"\0")

    with out_path.open("wb") as handle:
        handle.write(header)
        handle.write(struct.pack("<I", len(triangles)))

        for v1, v2, v3 in triangles:
            nx, ny, nz = triangle_normal(v1, v2, v3)
            handle.write(
                struct.pack(
                    "<12fH",
                    nx,
                    ny,
                    nz,
                    float(v1.X),
                    float(v1.Y),
                    float(v1.Z),
                    float(v2.X),
                    float(v2.Y),
                    float(v2.Z),
                    float(v3.X),
                    float(v3.Y),
                    float(v3.Z),
                    0,
                )
            )

    return len(triangles)


def resolve_output_path(input_path: Path, output_path: Path | None) -> Path:
    default_name = f"{input_path.stem}.stl"
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
        description="Try to extract a mesh-style 3D model from a Rhino .3dm file and export binary STL"
    )
    parser.add_argument("input_3dm", type=Path, help="Input .3dm file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .stl path, or directory for the default filename (default: CWD/input-stem.stl)",
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

        if selected_layers is not None and layer_name not in selected_layers:
            continue

        if obj_type == "ObjectType.Mesh":
            append_mesh_record(
                mesh_records,
                geom,
                obj_name,
                layer_name,
                "mesh_object",
            )
            direct_mesh_count += 1

        elif obj_type == "ObjectType.Brep" and not args.no_brep_face_meshes:
            recs = try_get_meshes_from_brep(geom, obj_name, layer_name)
            mesh_records.extend(recs)
            brep_mesh_count += len(recs)

        elif obj_type == "ObjectType.Extrusion" and not args.no_extrusion_meshes:
            recs = try_get_meshes_from_extrusion(geom, obj_name, layer_name)
            mesh_records.extend(recs)
            extrusion_mesh_count += len(recs)

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

    triangle_count = write_binary_stl(mesh_records, output_path, safe_name(input_path.stem, "model"))

    total_vertices = sum(len(mesh.Vertices) for record in mesh_records for mesh in record["meshes"])
    total_faces = sum(len(mesh.Faces) for record in mesh_records for mesh in record["meshes"])

    print(f"Wrote STL: {output_path}")
    print(f"Mesh records exported: {len(mesh_records)}")
    print(f"Direct mesh objects: {direct_mesh_count}")
    print(f"Brep face meshes: {brep_mesh_count}")
    print(f"Extrusion meshes: {extrusion_mesh_count}")
    print(f"Total vertices: {total_vertices}")
    print(f"Total faces: {total_faces}")
    print(f"Total triangles: {triangle_count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
