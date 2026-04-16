#!/bin/sh

# get argument 1 as the path to the 3dm file
if [ -z "$1" ]; then
    echo "Usage: $0 <path_to_3dm_file>"
    exit 1
fi

MODEL_3DM_FILE="$1"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"

echo "Extracting data from $MODEL_3DM_FILE..."

"$SCRIPT_DIR"/extract_3dm_curves_to_svg.py "$MODEL_3DM_FILE" -o "$SCRIPT_DIR"/output/
"$SCRIPT_DIR"/extract_3dm_curves_and_brep_edges_to_svg.py "$MODEL_3DM_FILE" -o "$SCRIPT_DIR"/output/
"$SCRIPT_DIR"/extract_3dm_to_obj_mesh.py "$MODEL_3DM_FILE" -o "$SCRIPT_DIR"/output/
"$SCRIPT_DIR"/extract_3dm_to_gltf.py "$MODEL_3DM_FILE" -o "$SCRIPT_DIR"/output/
"$SCRIPT_DIR"/extract_3dm_to_stl.py "$MODEL_3DM_FILE" -o "$SCRIPT_DIR"/output/
