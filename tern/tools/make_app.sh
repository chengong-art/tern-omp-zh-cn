#!/bin/sh
# Produce a Simplified Chinese copy of Tern:  make_app.sh [/Applications/Tern.app] [dist/Tern.app]
# The source app is only read. The copy is ad-hoc signed (the original Developer ID
# signature cannot survive a change), keeping the original entitlements.
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
src=${1:-/Applications/Tern.app}; dst=${2:-"$here/dist/Tern.app"}; w="$here/.scratch"
mkdir -p "$w" "$(dirname "$dst")"
rm -rf "$dst"
ditto "$src" "$dst"
helper="Contents/Helpers/Tern Daemon.app"
for rel in "$helper/Contents/MacOS/tern" "Contents/MacOS/tern"; do
    "$here/tools/build.sh" "$src/$rel" "$dst/$rel"
done
codesign -d --entitlements "$w/helper.ent" --xml "$src/$helper" 2>/dev/null || true
codesign -d --entitlements "$w/app.ent" --xml "$src" 2>/dev/null || true
sign() { # bundle, entitlements file
    if [ -s "$2" ]; then codesign -f -s - --options runtime --entitlements "$2" "$1"
    else codesign -f -s - --options runtime "$1"; fi
}
sign "$dst/$helper" "$w/helper.ent"
sign "$dst" "$w/app.ent"
codesign -v --deep --strict "$dst"
echo "built $dst"
