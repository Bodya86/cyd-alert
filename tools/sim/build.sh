#!/usr/bin/env bash
# Host build of the real CYD Alert UI (src/ui.cpp + LVGL 8.3.11) into a screenshot tool.
# Needs: a C/C++ compiler (g++ or `python3 -m ziglang`), LVGL in .pio/libdeps/cyd/lvgl (run `pio pkg install` once).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
LVGL="${LVGL:-$ROOT/.pio/libdeps/cyd/lvgl}"
OUT="$HERE/build"; mkdir -p "$OUT/obj"
if command -v g++ >/dev/null; then CC="gcc"; CXX="g++"; else CC="python3 -m ziglang cc"; CXX="python3 -m ziglang c++"; fi
DEF="-DLV_CONF_INCLUDE_SIMPLE -DLV_LVGL_H_INCLUDE_SIMPLE -DFW_ENV=\"cyd\" -DBAT_ADC_PIN=35"
INC="-I$HERE/stubs -I$ROOT/include -I$LVGL -I$LVGL/src"
CFLAGS="-O1 -w $DEF $INC"
objs=()
for f in $(find "$LVGL/src" -name '*.c') "$ROOT"/src/fonts/*.c; do
  o="$OUT/obj/$(echo "$f" | md5sum | cut -c1-12).o"
  [ "$o" -nt "$f" ] || $CC $CFLAGS -c "$f" -o "$o" &
  objs+=("$o")
  while [ "$(jobs -r | wc -l)" -ge 16 ]; do sleep 0.05; done
done
wait
CXXF="-O1 -std=gnu++17 -w $DEF $INC"
for f in "$ROOT/src/settings.cpp" "$ROOT/src/outage_calc.cpp" "$ROOT/src/textutil.cpp" "$ROOT/src/analyzer.cpp" \
         "$ROOT/src/mapdata.cpp" "$HERE/sim_stubs.cpp" "$HERE/sim_main.cpp"; do
  o="$OUT/obj/$(basename "$f").o"
  $CXX $CXXF -c "$f" -o "$o" &
  objs+=("$o")
done
wait
$CXX -o "$OUT/cyd_sim" "${objs[@]}" -lm
echo "built $OUT/cyd_sim"
