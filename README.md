# rhino3dm-test

## Install

```
git clone https://github.com/diversen7/rhino3dm-test.git
cd rhino3dm-test
uv sync
```

## extract_3dm_curves_to_svg.py

Extracts curve objects from a Rhino `.3dm` file and writes a top-view SVG. Curves are grouped by layer and scaled to fit the output canvas. 

View output `svg` files in a browser.

```sh
extract_3dm_curves_to_svg.py source_3dm/Epicycles.3dm -o output/
```

## extract_3dm_curves_and_brep_edges_to_svg.py

Exports both curve objects and Brep edge outlines from a Rhino `.3dm` file to a top-view SVG. This is useful when the model contains surface geometry whose visible edges should be included.

View output `svg` files in a browser. 

```sh
extract_3dm_curves_and_brep_edges_to_svg.py source_3dm/Epicycles.3dm -o output/
```

## extract_3dm_to_obj_mesh.py

Attempts to extract mesh geometry from Breps and extrusions in a Rhino `.3dm` file and writes it as an OBJ. Output objects are named from their source object and layer where possible.

```sh
extract_3dm_to_obj_mesh.py source_3dm/Epicycles.3dm -o output/
```

View online. Upload the `.obj` file:

https://3dviewer.net/

## extract_3dm_to_gltf.py

Attempts to extract mesh geometry from a Rhino `.3dm` file and writes it as a glTF 2.0 scene (`.gltf` + `.bin`). The exporter keeps one node per Rhino object and maps object or layer color to simple glTF materials.

```sh
extract_3dm_to_gltf.py source_3dm/Epicycles.3dm -o output/
```

View online. Upload the `.gltf` and `.bin` files at the same time:

https://3dviewer.net/
