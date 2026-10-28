#!/bin/bash
# Генерує LVGL-шрифти з кирилицею (Golos Text, OFL) + fallback на вбудований Montserrat для іконок LV_SYMBOL_*
# Потрібно: npm i -g lv_font_conv ; pip install fonttools
set -e
cd "$(dirname "$0")/.."
R="0x20-0x7E,0xA0-0xBB,0x400-0x45F,0x490-0x491,0x2013-0x2014,0x2019,0x201C-0x201E,0x2026,0x2116"
gen(){ # weight size name fallback
  lv_font_conv --no-compress --no-prefilter --bpp 4 --size $2 --font tools/fonts/Golos-$1.ttf -r $R \
    --format lvgl --lv-include lvgl.h -o src/fonts/$3.c --lv-font-name $3 --force-fast-kern-format
  sed -i "s/\.fallback = NULL,/.fallback = \&$4,/" src/fonts/$3.c
}
gen 500 12 font_ua_12 lv_font_montserrat_12
gen 500 14 font_ua_14 lv_font_montserrat_14
gen 600 18 font_ua_18 lv_font_montserrat_18
gen 700 26 font_ua_26 lv_font_montserrat_26
gen 700 40 font_ua_40 lv_font_montserrat_26
