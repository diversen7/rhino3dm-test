#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
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
            "mesh": mesh,
            "name": safe_name(name, "mesh"),
            "layer": safe_name(layer, "Default"),
            "source": source,
        }
    )


def try_get_meshes_from_brep(brep, base_name: str, layer_name: str):
    records = []
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
        return records

    for face_index in range(face_count):
        try:
            face = brep.Faces[face_index]
        except Exception:
            continue

        for mt in mesh_types:
            try:
                mesh = face.GetMesh(mt)
            except Exception:
                mesh = None

            if not mesh_has_geometry(mesh):
                continue

            key = (face_index, int(mt))
            if key in seen:
                continue
            seen.add(key)

            append_mesh_record(
                records,
                mesh,
                f"{base_name}_face_{face_index}_{mt.name.lower()}",
                layer_name,
                f"brep_face:{mt.name}",
            )
            break

    return records


def try_get_meshes_from_extrusion(extrusion, base_name: str, layer_name: str):
    records = []
    mesh_types = [
        rhino3dm.MeshType.Any,
        rhino3dm.MeshType.Render,
        rhino3dm.MeshType.Preview,
        rhino3dm.MeshType.Analysis,
    ]

    for mt in mesh_types:
        try:
            mesh = extrusion.GetMesh(mt)
        except Exception:
            mesh = None

        if mesh_has_geometry(mesh):
            append_mesh_record(
                records,
                mesh,
                f"{base_name}_{mt.name.lower()}",
                layer_name,
                f"extrusion:{mt.name}",
            )
            break

    return records


def write_obj(mesh_records, out_path: Path):
    def face_indices(face):
        """
        Return 0-based vertex indices as a list.
        Supports both tuple/list faces and object-style faces.
        """
        if isinstance(face, (tuple, list)):
            vals = list(face)
            if len(vals) == 3:
                return vals
            if len(vals) >= 4:
                a, b, c, d = vals[:4]
                # Some APIs repeat the last index for triangles
                if d == c:
                    return [a, b, c]
                return [a, b, c, d]
            raise ValueError(f"Unsupported face tuple length: {len(vals)}")

        # Object-style face
        a = face.A
        b = face.B
        c = face.C
        d = face.D
        if getattr(face, "IsQuad", False):
            return [a, b, c, d]
        return [a, b, c]

    lines = []
    vertex_offset = 0
    normal_offset = 0

    lines.append("# OBJ exported from Rhino .3dm via rhino3dm")
    lines.append("")

    for rec in mesh_records:
        mesh = rec["mesh"]
        obj_name = rec["name"]
        layer_name = rec["layer"]
        source = rec["source"]

        lines.append(f"o {obj_name}")
        lines.append(f"g {layer_name}")
        lines.append(f"# source: {source}")

        # vertices
        for v in mesh.Vertices:
            lines.append(f"v {v.X:.9f} {v.Y:.9f} {v.Z:.9f}")

        # normals, if present
        has_normals = False
        try:
            has_normals = len(mesh.Normals) == len(mesh.Vertices) and len(mesh.Normals) > 0
        except Exception:
            has_normals = False

        if has_normals:
            for n in mesh.Normals:
                lines.append(f"vn {n.X:.9f} {n.Y:.9f} {n.Z:.9f}")

        # faces
        for f in mesh.Faces:
            idx = face_indices(f)
            idx = [i + 1 + vertex_offset for i in idx]

            if has_normals:
                # Use the same index for v and vn because normals are per-vertex here
                idxn = [i + normal_offset for i in range(1, len(idx) + 1)]
                if len(idx) == 3:
                    lines.append(
                        f"f {idx[0]}//{idxn[0]} {idx[1]}//{idxn[1]} {idx[2]}//{idxn[2]}"
                    )
                elif len(idx) == 4:
                    lines.append(
                        f"f {idx[0]}//{idxn[0]} {idx[1]}//{idxn[1]} {idx[2]}//{idxn[2]} {idx[3]}//{idxn[3]}"
                    )
            else:
                if len(idx) == 3:
                    lines.append(f"f {idx[0]} {idx[1]} {idx[2]}")
                elif len(idx) == 4:
                    lines.append(f"f {idx[0]} {idx[1]} {idx[2]} {idx[3]}")

        lines.append("")
        vertex_offset += len(mesh.Vertices)
        if has_normals:
            normal_offset += len(mesh.Normals)

    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Try to extract a mesh-style 3D model from a Rhino .3dm file and export OBJ"
    )
    parser.add_argument("input_3dm", type=Path, help="Input .3dm file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .obj file (default: same name as input)",
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

    output_path = args.output or input_path.with_suffix(".obj")

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

    write_obj(mesh_records, output_path)

    total_vertices = sum(len(rec["mesh"].Vertices) for rec in mesh_records)
    total_faces = sum(len(rec["mesh"].Faces) for rec in mesh_records)

    print(f"Wrote OBJ: {output_path}")
    print(f"Mesh records exported: {len(mesh_records)}")
    print(f"Direct mesh objects: {direct_mesh_count}")
    print(f"Brep face meshes: {brep_mesh_count}")
    print(f"Extrusion meshes: {extrusion_mesh_count}")
    print(f"Total vertices: {total_vertices}")
    print(f"Total faces: {total_faces}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())