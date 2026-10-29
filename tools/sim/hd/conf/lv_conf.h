// HD simulator: firmware lv_conf.h + overrides for a 3840x2880 (×SIM_K) canvas.
#ifndef SIM_HD_LV_CONF_H
#define SIM_HD_LV_CONF_H
#include "../../../../include/lv_conf.h"
#undef LV_COLOR_DEPTH
#define LV_COLOR_DEPTH 32
#undef LV_MEM_CUSTOM
#define LV_MEM_CUSTOM 1
#define LV_USE_LARGE_COORD 1
#define LV_FONT_FMT_TXT_LARGE 1
// Montserrat (запасний шрифт для символів) — власні ×K копії з build/hd/fonts
#undef LV_FONT_MONTSERRAT_12
#undef LV_FONT_MONTSERRAT_14
#undef LV_FONT_MONTSERRAT_18
#undef LV_FONT_MONTSERRAT_26
#define LV_FONT_MONTSERRAT_12 0
#define LV_FONT_MONTSERRAT_14 0
#define LV_FONT_MONTSERRAT_18 0
#define LV_FONT_MONTSERRAT_26 0
#undef LV_FONT_CUSTOM_DECLARE
#define LV_FONT_CUSTOM_DECLARE LV_FONT_DECLARE(font_ua_12) LV_FONT_DECLARE(font_ua_14) LV_FONT_DECLARE(font_ua_18) \
  LV_FONT_DECLARE(font_ua_26) LV_FONT_DECLARE(font_ua_40) LV_FONT_DECLARE(lv_font_montserrat_12) \
  LV_FONT_DECLARE(lv_font_montserrat_14) LV_FONT_DECLARE(lv_font_montserrat_18) LV_FONT_DECLARE(lv_font_montserrat_26)
#endif
