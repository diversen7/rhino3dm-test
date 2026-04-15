#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import re
import sys
import xml.etree.ElementTree as ET
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


def svg_safe_id(text: str) -> str:
    text = text.strip()
    if not text:
        return "layer"
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    if text[0].isdigit():
        text = f"layer_{text}"
    return text


def polyline_from_curve(curve, samples_per_span: int = 24) -> list[tuple[float, float]]:
    """
    Convert a Rhino curve-like object to a list of XY points.
    Uses exact polyline extraction when available; otherwise samples the NURBS form.
    """
    if curve is None:
        return []

    try:
        if curve.IsPolyline():
            ok, pl = curve.TryGetPolyline()
            if ok and pl and len(pl) >= 2:
                return [(pt.X, pt.Y) for pt in pl]
    except Exception:
        pass

    try:
        nc = curve.ToNurbsCurve()
        if nc is None:
            return []

        domain = nc.Domain
        t0 = domain.T0
        t1 = domain.T1
        if not math.isfinite(t0) or not math.isfinite(t1) or t0 == t1:
            return []

        span_count = max(1, getattr(nc, "SpanCount", 1))
        count = max(16, span_count * samples_per_span)

        pts: list[tuple[float, float]] = []
        for i in range(count + 1):
            t = t0 + (t1 - t0) * (i / count)
            p = nc.PointAt(t)
            pts.append((p.X, p.Y))

        cleaned: list[tuple[float, float]] = []
        for pt in pts:
            if not cleaned or (
                abs(pt[0] - cleaned[-1][0]) > 1e-9
                or abs(pt[1] - cleaned[-1][1]) > 1e-9
            ):
                cleaned.append(pt)

        return cleaned
    except Exception:
        return []


def extract_brep_edge_polylines(brep, samples_per_span: int = 24) -> list[list[tuple[float, float]]]:
    """
    Extract all Brep edges as sampled polylines in XY.
    """
    out: list[list[tuple[float, float]]] = []

    try:
        edges = brep.Edges
    except Exception:
        return out

    try:
        edge_count = len(edges)
    except Exception:
        return out

    for i in range(edge_count):
        try:
            edge = edges[i]
        except Exception:
            continue

        pts = polyline_from_curve(edge, samples_per_span=samples_per_span)
        if len(pts) >= 2:
            out.append(pts)

    return out


def bounding_box_2d(polylines: list[list[tuple[float, float]]]) -> tuple[float, float, float, float]:
    xs = []
    ys = []
    for pl in polylines:
        for x, y in pl:
            xs.append(x)
            ys.append(y)

    if not xs or not ys:
        raise ValueError("No 2D points found.")

    return min(xs), min(ys), max(xs), max(ys)


def transform_points(
    polylines: list[list[tuple[float, float]]],
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    width: int,
    height: int,
    margin: int,
    flip_y: bool = True,
) -> list[list[tuple[float, float]]]:
    dx = max_x - min_x
    dy = max_y - min_y

    if dx <= 0:
        dx = 1.0
    if dy <= 0:
        dy = 1.0

    avail_w = max(1, width - 2 * margin)
    avail_h = max(1, height - 2 * margin)

    scale = min(avail_w / dx, avail_h / dy)

    out: list[list[tuple[float, float]]] = []
    for poly in polylines:
        tpoly: list[tuple[float, float]] = []
        for x, y in poly:
            sx = margin + (x - min_x) * scale
            sy_model = margin + (y - min_y) * scale
            sy = height - sy_model if flip_y else sy_model
            tpoly.append((sx, sy))
        out.append(tpoly)

    return out


def polyline_to_path_d(points: list[tuple[float, float]], close: bool = False) -> str:
    if len(points) < 2:
        return ""

    parts = [f"M {points[0][0]:.3f},{points[0][1]:.3f}"]
    for x, y in points[1:]:
        parts.append(f"L {x:.3f},{y:.3f}")
    if close:
        parts.append("Z")
    return " ".join(parts)


def detect_closed(points: list[tuple[float, float]], tol: float = 1e-6) -> bool:
    if len(points) < 3:
        return False
    x0, y0 = points[0]
    x1, y1 = points[-1]
    return abs(x0 - x1) <= tol and abs(y0 - y1) <= tol


def write_svg(
    out_path: Path,
    layer_paths: dict[str, list[str]],
    width: int,
    height: int,
    title: str,
) -> None:
    svg = ET.Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "version": "1.1",
            "width": str(width),
            "height": str(height),
            "viewBox": f"0 0 {width} {height}",
        },
    )

    title_el = ET.SubElement(svg, "title")
    title_el.text = title

    desc_el = ET.SubElement(svg, "desc")
    desc_el.text = "Top-view SVG extracted from Rhino .3dm curve objects and Brep edges"

    ET.SubElement(
        svg,
        "rect",
        {
            "x": "0",
            "y": "0",
            "width": str(width),
            "height": str(height),
            "fill": "white",
        },
    )

    for layer_name, paths in layer_paths.items():
        g = ET.SubElement(
            svg,
            "g",
            {
                "id": svg_safe_id(layer_name),
                "fill": "none",
                "stroke": "black",
                "stroke-width": "1",
                "stroke-linecap": "round",
                "stroke-linejoin": "round",
            },
        )
        for d in paths:
            ET.SubElement(g, "path", {"d": d})

    tree = ET.ElementTree(svg)
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    tree.write(out_path, encoding="utf-8", xml_declaration=True)


def resolve_output_path(input_path: Path, output_path: Path | None) -> Path:
    resolved = output_path or input_path.with_name(f"{input_path.stem}_curves_breps.svg")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract top-view SVG from Rhino .3dm curve objects and Brep edges"
    )
    parser.add_argument("input_3dm", type=Path, help="Input .3dm file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .svg filename or path (default: same name as input with _curves_breps.svg)",
    )
    parser.add_argument("--width", type=int, default=1600, help="SVG width in px")
    parser.add_argument("--height", type=int, default=1200, help="SVG height in px")
    parser.add_argument("--margin", type=int, default=20, help="Margin in px")
    parser.add_argument(
        "--samples-per-span",
        type=int,
        default=24,
        help="Sampling density for non-polyline curves",
    )
    parser.add_argument(
        "--layer",
        action="append",
        default=[],
        help="Only include these layer names (can be repeated)",
    )
    parser.add_argument(
        "--no-curves",
        action="store_true",
        help="Exclude standalone curve objects",
    )
    parser.add_argument(
        "--no-brep-edges",
        action="store_true",
        help="Exclude Brep edges",
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

    extracted: list[tuple[str, list[tuple[float, float]], str]] = []
    curve_count = 0
    brep_edge_count = 0

    for obj in model.Objects:
        geom = obj.Geometry
        attrs = obj.Attributes
        obj_type = object_type_name(geom)
        layer_name = safe_layer_name(model, attrs.LayerIndex)

        if selected_layers is not None and layer_name not in selected_layers:
            continue

        if obj_type == "ObjectType.Curve" and not args.no_curves:
            pts = polyline_from_curve(geom, samples_per_span=args.samples_per_span)
            if len(pts) >= 2:
                extracted.append((layer_name, pts, "curve"))
                curve_count += 1

        elif obj_type == "ObjectType.Brep" and not args.no_brep_edges:
            edge_polylines = extract_brep_edge_polylines(
                geom, samples_per_span=args.samples_per_span
            )
            for pts in edge_polylines:
                if len(pts) >= 2:
                    extracted.append((layer_name, pts, "brep_edge"))
                    brep_edge_count += 1

    if not extracted:
        print("No curve objects or Brep edges found that could be converted.", file=sys.stderr)
        return 2

    all_polylines = [pts for _, pts, _ in extracted]
    min_x, min_y, max_x, max_y = bounding_box_2d(all_polylines)

    transformed_polylines = transform_points(
        all_polylines,
        min_x,
        min_y,
        max_x,
        max_y,
        width=args.width,
        height=args.height,
        margin=args.margin,
        flip_y=True,
    )

    layer_paths: dict[str, list[str]] = {}

    for (layer_name, _orig_pts, _kind), tpts in zip(extracted, transformed_polylines):
        d = polyline_to_path_d(tpts, close=detect_closed(tpts))
        if not d:
            continue
        layer_paths.setdefault(layer_name, []).append(d)

    write_svg(output_path, layer_paths, args.width, args.height, input_path.name)

    print(f"Wrote SVG: {output_path}")
    print(f"Standalone curves exported: {curve_count}")
    print(f"Brep edges exported: {brep_edge_count}")
    print(f"Total SVG paths: {sum(len(v) for v in layer_paths.values())}")
    print("Layers:")
    for layer_name in sorted(layer_paths):
        print(f"  - {layer_name}: {len(layer_paths[layer_name])}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
