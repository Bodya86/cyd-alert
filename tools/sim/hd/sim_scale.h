// HD-симулятор: логічні координати UI (320x240) → фізичні ×SIM_K. Підключається перед кодом ui.cpp
// (після lvgl.h), тож внутрішні виклики LVGL не масштабуються вдруге. Спецзначення (LV_PCT, LV_SIZE_CONTENT,
// LV_RADIUS_CIRCLE) лишаються як є.
#pragma once
#include <lvgl.h>
#ifndef SIM_K
#define SIM_K 1
#endif
static inline lv_coord_t SC(lv_coord_t v) { return (v == LV_RADIUS_CIRCLE || LV_COORD_IS_SPEC(v)) ? v : (lv_coord_t)(v * SIM_K); }
#define lv_obj_set_size(o, w, h)            (lv_obj_set_size)((o), SC(w), SC(h))
#define lv_obj_set_pos(o, x, y)             (lv_obj_set_pos)((o), SC(x), SC(y))
#define lv_obj_set_x(o, x)                  (lv_obj_set_x)((o), SC(x))
#define lv_obj_set_y(o, y)                  (lv_obj_set_y)((o), SC(y))
#define lv_obj_set_width(o, w)              (lv_obj_set_width)((o), SC(w))
#define lv_obj_set_height(o, h)             (lv_obj_set_height)((o), SC(h))
#define lv_obj_align(o, a, x, y)            (lv_obj_align)((o), (a), SC(x), SC(y))
#define lv_obj_align_to(o, b, a, x, y)      (lv_obj_align_to)((o), (b), (a), SC(x), SC(y))
#define lv_obj_set_ext_click_area(o, v)     (lv_obj_set_ext_click_area)((o), SC(v))
#define SIM_STYLE(name) (lv_obj_set_style_##name)
#define lv_obj_set_style_radius(o, v, s)        SIM_STYLE(radius)((o), SC(v), (s))
#define lv_obj_set_style_border_width(o, v, s)  SIM_STYLE(border_width)((o), SC(v), (s))
#define lv_obj_set_style_outline_width(o, v, s) SIM_STYLE(outline_width)((o), SC(v), (s))
#define lv_obj_set_style_shadow_width(o, v, s)  SIM_STYLE(shadow_width)((o), SC(v), (s))
#define lv_obj_set_style_line_width(o, v, s)    SIM_STYLE(line_width)((o), SC(v), (s))
#define lv_obj_set_style_arc_width(o, v, s)     SIM_STYLE(arc_width)((o), SC(v), (s))
#define lv_obj_set_style_max_width(o, v, s)     SIM_STYLE(max_width)((o), SC(v), (s))
#define lv_obj_set_style_width(o, v, s)         SIM_STYLE(width)((o), SC(v), (s))
#define lv_obj_set_style_height(o, v, s)        SIM_STYLE(height)((o), SC(v), (s))
#define lv_obj_set_style_pad_all(o, v, s)       SIM_STYLE(pad_all)((o), SC(v), (s))
#define lv_obj_set_style_pad_hor(o, v, s)       SIM_STYLE(pad_hor)((o), SC(v), (s))
#define lv_obj_set_style_pad_ver(o, v, s)       SIM_STYLE(pad_ver)((o), SC(v), (s))
#define lv_obj_set_style_pad_gap(o, v, s)       SIM_STYLE(pad_gap)((o), SC(v), (s))
#define lv_obj_set_style_pad_row(o, v, s)       SIM_STYLE(pad_row)((o), SC(v), (s))
#define lv_obj_set_style_pad_column(o, v, s)    SIM_STYLE(pad_column)((o), SC(v), (s))
#define lv_obj_set_style_pad_left(o, v, s)      SIM_STYLE(pad_left)((o), SC(v), (s))
#define lv_obj_set_style_pad_right(o, v, s)     SIM_STYLE(pad_right)((o), SC(v), (s))
#define lv_obj_set_style_pad_top(o, v, s)       SIM_STYLE(pad_top)((o), SC(v), (s))
#define lv_obj_set_style_pad_bottom(o, v, s)    SIM_STYLE(pad_bottom)((o), SC(v), (s))
#define lv_obj_set_style_translate_x(o, v, s)   SIM_STYLE(translate_x)((o), SC(v), (s))
#define lv_obj_set_style_translate_y(o, v, s)   SIM_STYLE(translate_y)((o), SC(v), (s))
#define lv_obj_set_style_text_letter_space(o, v, s) SIM_STYLE(text_letter_space)((o), SC(v), (s))
#define lv_obj_set_style_text_line_space(o, v, s)   SIM_STYLE(text_line_space)((o), SC(v), (s))
#define lv_obj_set_style_shadow_ofs_x(o, v, s)  SIM_STYLE(shadow_ofs_x)((o), SC(v), (s))
#define lv_obj_set_style_shadow_ofs_y(o, v, s)  SIM_STYLE(shadow_ofs_y)((o), SC(v), (s))
#define lv_obj_set_style_shadow_spread(o, v, s) SIM_STYLE(shadow_spread)((o), SC(v), (s))
