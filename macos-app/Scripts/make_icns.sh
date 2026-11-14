#!/bin/bash
# make_icns.sh — build AppIcon.icns for the BattlePew .app.
#
# Usage:
#   Scripts/make_icns.sh [MASTER_PNG] [ICONSET_DIR] [OUTPUT_ICNS]
#
# Behavior:
#   - If MASTER_PNG (a 1024x1024 PNG) is given and exists, the 10 iconset
#     sizes are regenerated from it with `sips` (ships with macOS), then
#     `iconutil` builds the .icns. This is the path for replacing the
#     placeholder icon with real artwork: drop a 1024x1024 master at
#     Resources/AppIcon-1024.png and rebuild.
#   - If no master is given, the existing ICONSET_DIR is converted as-is.
#     The shipped iconset contains PLACEHOLDER artwork (see README.md).
#
# Requires macOS (sips + iconutil). Never run on Linux.
set -euo pipefail

MASTER_PNG="${1:-}"
ICONSET_DIR="${2:-Resources/AppIcon.iconset}"
OUTPUT_ICNS="${3:-Resources/AppIcon.icns}"

if [ -n "$MASTER_PNG" ]; then
    if [ ! -f "$MASTER_PNG" ]; then
        echo "error: master PNG not found: $MASTER_PNG" >&2
        exit 1
    fi
    rm -rf "$ICONSET_DIR"
    mkdir -p "$ICONSET_DIR"
    # iconutil naming: icon_<pt>x<pt>[@2x].png, pixel size = pt * scale.
    for spec in "16:16" "16:32" "32:32" "32:64" "128:128" "128:256" "256:256" "256:512" "512:512" "512:1024"; do
        pt="${spec%%:*}"
        px="${spec##*:}"
        if [ "$px" -eq "$((pt * 2))" ]; then
            name="icon_${pt}x${pt}@2x.png"
        else
            name="icon_${pt}x${pt}.png"
        fi
        sips -z "$px" "$px" "$MASTER_PNG" --out "$ICONSET_DIR/$name" >/dev/null
    done
    echo "Regenerated $ICONSET_DIR from $MASTER_PNG"
fi

if [ ! -d "$ICONSET_DIR" ]; then
    echo "error: iconset directory not found: $ICONSET_DIR" >&2
    exit 1
fi

iconutil -c icns "$ICONSET_DIR" -o "$OUTPUT_ICNS"
echo "Wrote $OUTPUT_ICNS"
