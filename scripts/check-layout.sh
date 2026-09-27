#!/bin/bash
# Renders every page of the app at the default (1180x700) and minimum (900x560) window sizes with
# the built-in screenshot harness (Demo Mode, no device discovery) and fails if any page is
# squeezed, overflows the window, is missing, or the harness times out.
#
#   scripts/check-layout.sh "<path to .app>/Contents/MacOS/iOS Developer Toolkit" <output-dir>
set -euo pipefail
app="$1"; root="$2"
for size in 1180x700 900x560; do
  out="$root/$size"
  "$app" -ui-testing YES -demo-mode YES -populate-demo YES -window-size "$size" -capture-screenshots "$out"
  cat "$out/window-geometry.txt"; echo
  if [[ -e "$out/TIMEOUT" ]]; then echo "::error::screenshot harness timed out at $size"; exit 1; fi
  pages="$(grep -c ': window (' "$out/window-geometry.txt" || true)"
  if (( pages < 14 )); then echo "::error::only $pages pages rendered at $size"; exit 1; fi
  if grep -E 'SQUEEZED|OVERFLOW' "$out/window-geometry.txt"; then echo "::error::layout problem at $size"; exit 1; fi
done
echo "layout OK"
