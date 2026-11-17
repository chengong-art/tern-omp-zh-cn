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
exe() { /usr/libexec/PlistBuddy -c "Print :CFBundleExecutable" "$1/Contents/Info.plist"; }
sign() { # original bundle, copied bundle: ad-hoc sign the copy with the original's entitlements
    rm -f "$w/ent.xml"
    codesign -d --entitlements "$w/ent.xml" --xml "$1" 2>/dev/null || true
    if [ -s "$w/ent.xml" ]; then codesign -f -s - --options runtime --entitlements "$w/ent.xml" "$2"
    else codesign -f -s - --options runtime "$2"; fi
}
# Helper bundles first (their name changes between releases), the app itself last.
for helper in "$src"/Contents/Helpers/*.app; do
    [ -d "$helper" ] || continue
    rel="Contents/Helpers/$(basename "$helper")"; bin="Contents/MacOS/$(exe "$helper")"
    "$here/tools/build.sh" "$helper/$bin" "$dst/$rel/$bin" "$w/helper"
    sign "$helper" "$dst/$rel"
done
bin="Contents/MacOS/$(exe "$src")"
"$here/tools/build.sh" "$src/$bin" "$dst/$bin"
sign "$src" "$dst"
codesign -v --deep --strict "$dst"
echo "built $dst"
