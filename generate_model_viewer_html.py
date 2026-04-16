#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import os
import sys
from pathlib import Path

MODEL_VIEWER_URL = "https://ajax.googleapis.com/ajax/libs/model-viewer/4.2.0/model-viewer.min.js"


def resolve_output_path(input_path: Path, output_path: Path | None) -> Path:
    default_name = f"{input_path.stem}.html"
    if output_path is None:
        resolved = input_path.with_suffix(".html")
    elif output_path.exists() and output_path.is_dir():
        resolved = output_path / default_name
    elif output_path.suffix == "":
        resolved = output_path / default_name
    else:
        resolved = output_path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def relative_model_path(model_path: Path, html_path: Path) -> str:
    return Path(os.path.relpath(model_path.resolve(), start=html_path.parent.resolve())).as_posix()


def build_html(title: str, model_src: str) -> str:
    escaped_title = html.escape(title)
    escaped_model_src = html.escape(model_src, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f3efe7;
      --panel: rgba(255, 255, 255, 0.78);
      --text: #1d2421;
      --muted: #5b625f;
      --shadow: rgba(29, 36, 33, 0.12);
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", Helvetica, Arial, sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top, rgba(180, 209, 197, 0.8), transparent 38%),
        linear-gradient(180deg, #f8f4ec 0%, var(--bg) 100%);
      display: grid;
      grid-template-rows: auto 1fr;
    }}

    header {{
      padding: 1rem 1.25rem 0;
    }}

    .panel {{
      max-width: 1100px;
      margin: 0 auto;
      width: 100%;
      padding: 1rem 1.25rem 1.25rem;
    }}

    .card {{
      background: var(--panel);
      border: 1px solid rgba(29, 36, 33, 0.08);
      border-radius: 18px;
      box-shadow: 0 18px 40px var(--shadow);
      backdrop-filter: blur(14px);
      overflow: hidden;
    }}

    .meta {{
      padding: 1rem 1.25rem;
    }}

    h1 {{
      margin: 0;
      font-size: clamp(1.2rem, 2vw, 1.8rem);
      line-height: 1.1;
    }}

    p {{
      margin: 0.5rem 0 0;
      color: var(--muted);
    }}

    model-viewer {{
      width: 100%;
      height: min(78vh, 900px);
      background:
        radial-gradient(circle at top, rgba(255, 255, 255, 0.92), rgba(233, 227, 217, 0.85)),
        linear-gradient(180deg, #fefcf9 0%, #ece4d7 100%);
    }}
  </style>
  <script type="module" src="{MODEL_VIEWER_URL}"></script>
</head>
<body>
  <header class="panel">
    <div class="card meta">
      <h1>{escaped_title}</h1>
      <p>Drag to orbit. Scroll to zoom. Shift-drag to pan.</p>
    </div>
  </header>
  <main class="panel">
    <div class="card">
      <model-viewer
        src="{escaped_model_src}"
        alt="{escaped_title}"
        camera-controls
        touch-action="pan-y"
        shadow-intensity="1"
        exposure="1">
      </model-viewer>
    </div>
  </main>
  <script>
    if (window.location.protocol === "file:") {{
      console.warn(
        "This viewer must be opened over http:// or https://. Browsers block model fetches from file:// for CORS reasons."
      );
    }}
  </script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a simple single-page HTML viewer for a .glb model using model-viewer."
    )
    parser.add_argument("input_glb", type=Path, help="Input .glb file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .html path, or directory for the default filename (default: next to the .glb)",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Optional page title (default: input filename stem)",
    )

    args = parser.parse_args()

    input_path = args.input_glb
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1
    if input_path.suffix.lower() != ".glb":
        print(f"Expected a .glb file, got: {input_path}", file=sys.stderr)
        return 1

    output_path = resolve_output_path(input_path, args.output)
    model_src = relative_model_path(input_path, output_path)
    title = args.title or input_path.stem

    html_text = build_html(title, model_src)
    output_path.write_text(html_text, encoding="utf-8")

    print(f"Wrote HTML viewer: {output_path}")
    print(f"Model source: {model_src}")
    print("Note: open the HTML over http:// or https://, not file://", file=sys.stderr)
    print(
        f"Example: cd {output_path.parent} && python -m http.server 8000",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
