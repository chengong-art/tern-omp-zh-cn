#!/bin/sh
# Rebuild the localised binary: build.sh ORIGINAL_BINARY OUTPUT_BINARY   (work files go to .scratch/)
set -eu
here=$(cd "$(dirname "$0")/.." && pwd); w="$here/.scratch"; t="$here/tools"; tr="$here/translations"
mkdir -p "$w"
python3 "$t/analyze.py" "$1" "$w/refs.pkl"
python3 "$t/extract.py" "$1" "$w/refs.pkl" "$w/strings.pkl"
python3 "$t/dump.py" "$1" "$w/strings.pkl" "$w/all_strings.json"
python3 "$t/inline.py" "$1" "$w/refs.pkl" "$w/inline.json"
python3 "$t/templates.py" "$1" "$w/refs.pkl" "$w/templates.json"
python3 "$t/mkplan.py" "$1" "$w/strings.pkl" "$w/all_strings.json" "$tr/zh-CN.json" "$w/plan.json" \
        "$w/inline.json" "$tr/zh-CN.short.json" "$w/templates.json" "$tr/zh-CN.templates.json"
rm -f "$2"
python3 "$t/patch.py" "$1" "$2" "$w/plan.json"
chmod +x "$2"
