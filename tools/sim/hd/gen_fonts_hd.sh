#!/bin/bash
# HD-шрифти для симулятора: ті самі Golos-ваги/діапазони, що tools/gen_fonts.sh, але розмір ×K.
# Fallback-и lv_font_montserrat_N теж ×K (Montserrat ASCII + FontAwesome-символи LVGL).
set -e
K=${1:-12}
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../../.." && pwd)"
OUT="$HERE/../build/hd/fonts"; mkdir -p "$OUT"
CONV="$HERE/node_modules/.bin/lv_font_conv"
LVF="$ROOT/.pio/libdeps/cyd/lvgl/scripts/built_in_font"
R="0x20-0x7E,0xA0-0xBB,0x400-0x45F,0x490-0x491,0x2013-0x2014,0x2019,0x201C-0x201E,0x2026,0x2116"
SYMS="61441,61448,61451,61452,61453,61457,61459,61461,61465,61468,61473,61478,61479,61480,61502,61507,61512,61515,61516,61517,61521,61522,61523,61524,61543,61544,61550,61552,61553,61556,61559,61560,61561,61563,61587,61589,61636,61637,61639,61641,61664,61671,61674,61683,61724,61732,61787,61931,62016,62017,62018,62019,62020,62087,62099,62212,62189,62810,63426,63650"
gen(){ # weight size name fallback
  [ -f "$OUT/$3_$K.c" ] && return
  "$CONV" --no-compress --no-prefilter --bpp 4 --size $(( $2 * K )) --font "$ROOT/tools/fonts/Golos-$1.ttf" -r $R \
    --format lvgl --lv-include lvgl.h -o "$OUT/$3_$K.c" --lv-font-name $3 --force-fast-kern-format
  sed -i "s/\.fallback = NULL,/.fallback = \&$4,/" "$OUT/$3_$K.c"
  sed -i "s/^\(const \)\{0,1\}lv_font_t $3 = {/extern const lv_font_t $4;\n&/" "$OUT/$3_$K.c"
}
sym(){ # size
  [ -f "$OUT/lv_font_montserrat_$1_$K.c" ] && return
  "$CONV" --no-compress --no-prefilter --bpp 4 --size $(( $1 * K )) --font "$LVF/Montserrat-Medium.ttf" -r 0x20-0x7F,0xB0,0x2022 \
    --font "$LVF/FontAwesome5-Solid+Brands+Regular.woff" -r $SYMS \
    --format lvgl --lv-include lvgl.h -o "$OUT/lv_font_montserrat_$1_$K.c" --lv-font-name lv_font_montserrat_$1 --force-fast-kern-format
}
gen 500 12 font_ua_12 lv_font_montserrat_12 & gen 500 14 font_ua_14 lv_font_montserrat_14 &
gen 600 18 font_ua_18 lv_font_montserrat_18 & gen 700 26 font_ua_26 lv_font_montserrat_26 &
gen 700 40 font_ua_40 lv_font_montserrat_26 &
sym 12 & sym 14 & sym 18 & sym 26 &
wait
ls -la "$OUT"
