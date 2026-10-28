/* Мінімальний lv_conf.h для LVGL 8.3 — решта значень береться за замовчуванням з lv_conf_internal.h */
#if 1
#ifndef LV_CONF_H
#define LV_CONF_H
#include <stdint.h>

#define LV_COLOR_DEPTH 16
#define LV_COLOR_16_SWAP 0
#define LV_COLOR_SCREEN_TRANSP 0

#define LV_MEM_CUSTOM 0
#define LV_MEM_SIZE (40U * 1024U)

#define LV_DISP_DEF_REFR_PERIOD 20
#define LV_INDEV_DEF_READ_PERIOD 20

#define LV_TICK_CUSTOM 1
#define LV_TICK_CUSTOM_INCLUDE "Arduino.h"
#define LV_TICK_CUSTOM_SYS_TIME_EXPR (millis())

#define LV_DPI_DEF 130
#define LV_USE_LOG 0
#define LV_USE_ASSERT_NULL 1
#define LV_USE_ASSERT_MALLOC 1
#define LV_USE_PERF_MONITOR 0
#define LV_USE_MEM_MONITOR 0

#define LV_SHADOW_CACHE_SIZE 0
#define LV_CIRCLE_CACHE_SIZE 4
#define LV_IMG_CACHE_DEF_SIZE 0
#define LV_USE_GPU_STM32_DMA2D 0

/* Вбудовані шрифти — лише як fallback для іконок LV_SYMBOL_* */
#define LV_FONT_MONTSERRAT_12 1
#define LV_FONT_MONTSERRAT_14 1
#define LV_FONT_MONTSERRAT_18 1
#define LV_FONT_MONTSERRAT_26 1
#define LV_FONT_CUSTOM_DECLARE LV_FONT_DECLARE(font_ua_12) LV_FONT_DECLARE(font_ua_14) LV_FONT_DECLARE(font_ua_18) LV_FONT_DECLARE(font_ua_26) LV_FONT_DECLARE(font_ua_40)
#define LV_FONT_DEFAULT &font_ua_14
#define LV_TXT_ENC LV_TXT_ENC_UTF8
#define LV_TXT_BREAK_CHARS " ,.;:-_/"
#define LV_USE_BIDI 0
#define LV_USE_ARABIC_PERSIAN_CHARS 0

#define LV_USE_ANIMATION 1
#define LV_USE_THEME_DEFAULT 1
#define LV_THEME_DEFAULT_DARK 1
#define LV_THEME_DEFAULT_GROW 1
#define LV_USE_THEME_BASIC 0
#define LV_USE_THEME_MONO 0

#define LV_USE_ARC 1
#define LV_USE_BAR 1
#define LV_USE_BTN 1
#define LV_USE_BTNMATRIX 1
#define LV_USE_LABEL 1
#define LV_LABEL_LONG_TXT_HINT 1
#define LV_USE_TEXTAREA 1
#define LV_USE_KEYBOARD 1
#define LV_USE_LIST 1
#define LV_USE_MSGBOX 1
#define LV_USE_SPINNER 1
#define LV_USE_SLIDER 1
#define LV_USE_SWITCH 1
#define LV_USE_LED 1
#define LV_USE_IMG 0
#define LV_USE_CANVAS 0
#define LV_USE_CHART 0
#define LV_USE_CALENDAR 0
#define LV_USE_COLORWHEEL 0
#define LV_USE_IMGBTN 0
#define LV_USE_METER 0
#define LV_USE_SPINBOX 0
#define LV_USE_TABVIEW 0
#define LV_USE_TILEVIEW 0
#define LV_USE_WIN 0
#define LV_USE_SPAN 0
#define LV_USE_MENU 0
#define LV_USE_ANIMIMG 0
#define LV_USE_TABLE 0
#define LV_USE_CHECKBOX 1
#define LV_USE_DROPDOWN 1
#define LV_USE_ROLLER 1
#define LV_USE_LINE 1
#define LV_USE_FLEX 1
#define LV_USE_GRID 0
#define LV_BUILD_EXAMPLES 0
#endif
#endif
