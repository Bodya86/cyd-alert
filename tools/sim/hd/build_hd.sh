#!/usr/bin/env bash
# HD build of the simulator: the same src/ui.cpp, rendered at ×K (default 12 → 3840x2880) with fonts and
# map re-generated at ×K, so screenshots are genuinely smooth (not upscaled pixels). Firmware is untouched.
# Usage: hd/build_hd.sh [K]   → build/hd/cyd_sim_hd <out_dir>
set -e
K="${1:-12}"
HERE="$(cd "$(dirname "$0")" && pwd)"; SIM="$(cd "$HERE/.." && pwd)"; ROOT="$(cd "$SIM/../.." && pwd)"
LVGL_SRC="${LVGL:-$ROOT/.pio/libdeps/cyd/lvgl}"
OUT="$SIM/build/hd"; mkdir -p "$OUT/obj" "$OUT/fonts"
if command -v g++ >/dev/null; then CC="gcc"; CXX="g++"; else CC="python3 -m ziglang cc"; CXX="python3 -m ziglang c++"; fi
# 1) копія LVGL: LV_DPX ×K (поле dpi лише 10 біт) і тема «малий дисплей» незалежно від роздільності
if [ ! -f "$OUT/lvgl/.patched2_$K" ]; then
  rm -rf "$OUT/lvgl"; cp -r "$LVGL_SRC" "$OUT/lvgl"; rm -rf "$OUT/lvgl/.git"
  sed -i 's|^#define _LV_DPX_CALC(dpi, n) .*|#define _LV_DPX_CALC(dpi, n) (SIM_K * ((n) == 0 ? 0 :LV_MAX((( (dpi) * (n) + 80) / 160), 1)))|' "$OUT/lvgl/src/hal/lv_hal.h"
  sed -i 's|if(LV_HOR_RES <= 320) disp_size = DISP_SMALL;|if(1) disp_size = DISP_SMALL;|' "$OUT/lvgl/src/extra/themes/default/lv_theme_default.c"
  # стандартні розміри віджетів (перемикач, повзунок…) задані через LV_DPI_DEF напряму
  find "$OUT/lvgl/src/widgets" "$OUT/lvgl/src/extra" "$OUT/lvgl/src/core" -name '*.c' -exec sed -i 's/\bLV_DPI_DEF\b/(SIM_K * LV_DPI_DEF)/g' {} +
  grep -q 'SIM_K \*' "$OUT/lvgl/src/hal/lv_hal.h" && grep -q 'if(1) disp_size = DISP_SMALL' "$OUT/lvgl/src/extra/themes/default/lv_theme_default.c" \
    || { echo "LVGL patch failed"; exit 1; }
  touch "$OUT/lvgl/.patched2_$K"
fi
LVGL="$OUT/lvgl"
# 2) шрифти й мапа ×K, патчена копія ui.cpp
[ -f "$OUT/fonts/font_ua_40_$K.c" ] && [ -f "$OUT/fonts/lv_font_montserrat_26_$K.c" ] || "$HERE/gen_fonts_hd.sh" "$K"
grep -q "HD_MAP_K $K\$" "$OUT/map_hd.h" 2>/dev/null || python3 "$HERE/gen_map_hd.py" "$K"
python3 "$HERE/make_ui_hd.py"
for f in "$OUT"/fonts/lv_font_montserrat_*_"$K".c; do   # власні Montserrat — без гарда LV_FONT_MONTSERRAT_N
  sed 's/^#if LV_FONT_MONTSERRAT_[0-9]*$/#if 1/' "$f" > "$OUT/fonts/m_$(basename "$f")"
done
DEF="-DSIM_K=$K -DSIM_HD -DLV_CONF_INCLUDE_SIMPLE -DLV_LVGL_H_INCLUDE_SIMPLE -DFW_ENV=\"cyd\" -DBAT_ADC_PIN=35"
INC="-I$HERE/conf -I$HERE -I$OUT -I$SIM/stubs -I$ROOT/include -I$LVGL -I$LVGL/src"
CFLAGS="-O2 -w $DEF $INC"
objs=()
for f in $(find "$LVGL/src" -name '*.c') "$OUT"/fonts/font_ua_*_"$K".c "$OUT"/fonts/m_lv_font_montserrat_*_"$K".c; do
  o="$OUT/obj/$(echo "$f" | md5sum | cut -c1-12).o"
  [ "$o" -nt "$f" ] && [ "$o" -nt "$HERE/conf/lv_conf.h" ] || $CC $CFLAGS -c "$f" -o "$o" &
  objs+=("$o")
  while [ "$(jobs -r | wc -l)" -ge 16 ]; do sleep 0.05; done
done
wait
CXXF="-O2 -std=gnu++17 -w $DEF $INC -DSIM_UI_FILE=\"$OUT/ui_hd.cpp\""
for f in "$ROOT/src/settings.cpp" "$ROOT/src/outage_calc.cpp" "$ROOT/src/textutil.cpp" "$ROOT/src/analyzer.cpp" \
         "$ROOT/src/mapdata.cpp" "$SIM/sim_stubs.cpp" "$SIM/sim_main.cpp"; do
  o="$OUT/obj/$(basename "$f").o"
  $CXX $CXXF -c "$f" -o "$o" &
  objs+=("$o")
done
wait
$CXX -o "$OUT/cyd_sim_hd" "${objs[@]}" -lm
echo "built $OUT/cyd_sim_hd (K=$K)"
