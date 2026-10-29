#!/usr/bin/env python3
"""Генерує build/hd/ui_hd.cpp — копію src/ui.cpp для HD-симулятора (прошивку не змінює).
Звичайні виклики LVGL з логічними координатами масштабує sim_scale.h; тут — лише місця, де ui.cpp сам
малює у фізичних пікселях (мапа, підписи мапи, цілі, смуги графіка світла). Кожна заміна перевіряється."""
import os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "../../.."))
src = open(os.path.join(ROOT, "src", "ui.cpp"), encoding="utf-8").read()

def rep(old, new, count=1):
    global src
    n = src.count(old)
    if n != count: sys.exit(f"patch mismatch ({n} != {count}): {old!r}")
    src = src.replace(old, new)

def func(sig, body):
    global src
    i = src.index(sig); j = src.index("\n}\n", i) + 3
    src = src[:i] + body.strip("\n") + "\n" + src[j:]

K = "SIM_K"
# мапа: проєкція → фізичні пікселі
rep("p.x = (lv_coord_t)(c.x1 + vx(s_view, lon) - s_pan.x);", f"p.x = (lv_coord_t)(c.x1 + {K} * (vx(s_view, lon) - s_pan.x));")
rep("p.y = (lv_coord_t)(c.y1 + vy(s_view, lat) - s_pan.y);", f"p.y = (lv_coord_t)(c.y1 + {K} * (vy(s_view, lat) - s_pan.y));")
func("static void drawLabels(lv_draw_ctx_t* dc, const lv_area_t& c) {", r'''
static void drawLabels(lv_draw_ctx_t* dc, const lv_area_t& c) {   // HD: ті самі підписи, координати ×SIM_K
  const MapView* v = s_view;
  if (!v->labels) return;
  const int K = SIM_K;
  lv_draw_label_dsc_t ld; lv_draw_label_dsc_init(&ld);
  ld.font = &font_ua_12;
  lv_draw_rect_dsc_t dd; lv_draw_rect_dsc_init(&dd);
  dd.bg_opa = LV_OPA_COVER; dd.radius = 0; dd.border_width = 0;
  static const uint32_t KC[3] = {0xE6EDF3, 0xC9D1D9, 0x8B98A5};
  const int ox = c.x1 - K * s_pan.x, oy = c.y1 - K * s_pan.y;
  for (int i = 0; i < v->nlabels; i++) {
    const MapLabel& L = v->labels[i];
    if (L.kind >= 2 && s_uiLevel >= LVL_ORANGE) continue;
    lv_area_t a = {(lv_coord_t)(ox + K * L.lx), (lv_coord_t)(oy + K * (L.ly - 1)), (lv_coord_t)(ox + K * (L.lx + 110)), (lv_coord_t)(oy + K * (L.ly + 14))};
    lv_area_t tb = a;
    tb.x2 = (lv_coord_t)(tb.x1 + lv_txt_get_width(L.name, strlen(L.name), ld.font, 0, LV_TEXT_FLAG_NONE));
    tb.y1 = (lv_coord_t)LV_MIN(tb.y1, oy + K * (L.y - 1));
    if (tb.x1 < c.x1 || tb.x2 > c.x2 || tb.y1 < c.y1 - 2 * K || tb.y1 + 14 * K > c.y2 || labelHidden(tb)) continue;
    lv_area_t dot = {(lv_coord_t)(ox + K * (L.x - 1)), (lv_coord_t)(oy + K * (L.y - 1)),
                     (lv_coord_t)(ox + K * (L.x + (L.kind < 2 ? 2 : 1)) - 1), (lv_coord_t)(oy + K * (L.y + (L.kind < 2 ? 2 : 1)) - 1)};
    dd.bg_color = col(KC[L.kind > 2 ? 2 : L.kind]);
    lv_draw_rect(dc, &dd, &dot);
    lv_area_t sh = a; sh.x1 += K; sh.x2 += K; sh.y1 += K; sh.y2 += K;
    ld.color = lv_color_black(); ld.opa = LV_OPA_70;
    lv_draw_label(dc, &ld, &sh, L.name, nullptr);
    ld.color = col(KC[L.kind > 2 ? 2 : L.kind]); ld.opa = LV_OPA_COVER;
    lv_draw_label(dc, &ld, &a, L.name, nullptr);
  }
}''')
func("static void mapDrawCb(lv_event_t* e) {", r'''
#include "map_hd.h"
static const HdMap* hdMapFor(const MapView* v) {
  int vi = v == &MAP_VIEW_UA ? -1 : (int)(v - MAP_VIEWS);
  for (int i = 0; i < HD_MAP_COUNT; i++) if (HD_MAPS[i].view == vi) return &HD_MAPS[i];
  return nullptr;
}
// HD: той самий RLE-алгоритм, але по перерастрованому ×K виду (tools/sim/hd/gen_map_hd.py);
// якщо HD-растра для виду немає — початковий растр, кожен піксель ×K.
static void mapDrawCb(lv_event_t* e) {
  lv_obj_t* obj = lv_event_get_target(e);
  lv_draw_ctx_t* dc = lv_event_get_draw_ctx(e);
  lv_area_t c; lv_obj_get_coords(obj, &c);
  const lv_area_t* clip = dc->clip_area;
  const HdMap* hm = HD_MAP_K == SIM_K ? hdMapFor(s_view) : nullptr;
  const int sub = hm ? 1 : SIM_K;
  lv_draw_rect_dsc_t d; lv_draw_rect_dsc_init(&d);
  d.bg_opa = LV_OPA_COVER; d.radius = 0; d.border_width = 0;
  int y0 = LV_MAX(clip->y1, c.y1), y1 = LV_MIN(LV_MIN(clip->y2, c.y2), c.y1 + MAP_H * SIM_K - 1);
  const int xs = (s_view->ex + s_pan.x) * SIM_K, ys = (s_view->ey + s_pan.y) * SIM_K;   // кадр у растрі (фіз. пікселі)
  const int FW = MAP_W * SIM_K, RH = s_view->rh * SIM_K;
  for (int y = y0; y <= y1; y++) {
    int yy = y - c.y1 + ys;
    if (yy < 0 || yy >= RH) continue;
    int row = yy / sub, x = -xs, runX = 0, runN = 0;
    uint32_t r0 = hm ? hm->rows[row] : s_view->rowOfs[row], r1 = hm ? hm->rows[row + 1] : s_view->rowOfs[row + 1];
    const uint8_t* RUNS = hm ? hm->runs : s_view->runs;
    lv_color_t runC = s_pal[MAP_BG];
    auto flush = [&]() {
      if (!runN) return;
      lv_area_t a = {(lv_coord_t)(c.x1 + runX), (lv_coord_t)y, (lv_coord_t)(c.x1 + runX + runN - 1), (lv_coord_t)y};
      lv_area_t r;
      if (_lv_area_intersect(&r, &a, clip)) { d.bg_color = runC; lv_draw_rect(dc, &d, &r); }
      runN = 0;
    };
    for (uint32_t i = r0; i < r1; i++) {
      int n = RUNS[i * 2] * sub; uint8_t v = RUNS[i * 2 + 1];
      int a0 = x, a1 = x + n;
      x = a1;
      if (a1 <= 0) continue;
      if (a0 >= FW) break;
      if (a0 < 0) a0 = 0;
      if (a1 > FW) a1 = FW;
      if (v == MAP_BG) { flush(); }
      else {
        lv_color_t cc = s_pal[v];
        if (runN && cc.full != runC.full) flush();
        if (!runN) { runX = a0; runC = cc; }
        runN = a1 - runX;
      }
    }
    flush();
  }
  drawHeat(dc, c, clip);
  if (s_zoom) drawLabels(dc, c);
  drawTargets(dc, c);
}''')
# пунктир курсу
rep("const float ON = 4, PER = 7;", f"const float ON = 4 * {K}, PER = 7 * {K};")
rep("if (ph < ON && seg >= 0.8f) {", f"if (ph < ON && seg >= 0.8f * {K}) {{")
# цілі
rep("const float KM_PX = s_view->scale / 111.0f;", f"const float KM_PX = {K} * s_view->scale / 111.0f;")
rep("static const float ICON[3] = {6.0f, 7.5f, 9.0f};", f"static const float ICON[3] = {{6.0f * {K}, 7.5f * {K}, 9.0f * {K}}};")
rep("bool in = !(p0.x < c.x1 - 12 || p0.x > c.x2 + 12 || p0.y < c.y1 - 12 || p0.y > c.y2 + 12);",
    f"bool in = !(p0.x < c.x1 - 12 * {K} || p0.x > c.x2 + 12 * {K} || p0.y < c.y1 - 12 * {K} || p0.y > c.y2 + 12 * {K});")
rep("int r = LV_CLAMP(2, (int)(t.psd[i] * KM_PX + 0.5f), s_zoom ? 40 : 16);",
    f"int r = LV_CLAMP(2 * {K}, (int)(t.psd[i] * KM_PX + 0.5f), (s_zoom ? 40 : 16) * {K});", 2)
rep("bb.x1 -= 4; bb.y1 -= 4; bb.x2 += 4; bb.y2 += 4;", f"bb.x1 -= 4 * {K}; bb.y1 -= 4 * {K}; bb.x2 += 4 * {K}; bb.y2 += 4 * {K};")
rep("lnD.width = 4;", f"lnD.width = 4 * {K};")
rep("lnW.width = 2;", f"lnW.width = 2 * {K};")
rep("tkD.width = 3;", f"tkD.width = 3 * {K};")
rep("tkW.width = 1;", f"tkW.width = 1 * {K};")
rep("lv_point_t a1 = {(lv_coord_t)(q.x + 3 * dy), (lv_coord_t)(q.y - 3 * dx)}, a2 = {(lv_coord_t)(q.x - 3 * dy), (lv_coord_t)(q.y + 3 * dx)};",
    f"lv_point_t a1 = {{(lv_coord_t)(q.x + 3 * {K} * dy), (lv_coord_t)(q.y - 3 * {K} * dx)}}, a2 = {{(lv_coord_t)(q.x - 3 * {K} * dy), (lv_coord_t)(q.y + 3 * {K} * dx)}};")
rep("const float grow[3] = {2.2f, 1.1f, 0.0f};", f"const float grow[3] = {{2.2f * {K}, 1.1f * {K}, 0.0f}};")
rep("lv_area_t bb = {(lv_coord_t)(p0.x - S - 4), (lv_coord_t)(p0.y - S - 4), (lv_coord_t)(p0.x + S + 4), (lv_coord_t)(p0.y + S + 4)}, tmp;",
    f"lv_area_t bb = {{(lv_coord_t)(p0.x - S - 4 * {K}), (lv_coord_t)(p0.y - S - 4 * {K}), (lv_coord_t)(p0.x + S + 4 * {K}), (lv_coord_t)(p0.y + S + 4 * {K})}}, tmp;")
# смужка «наступні 8 год» на головному екрані
rep("d.bg_opa = LV_OPA_COVER; d.radius = 1;\n    for (int k = 0; k < 16; k++) {", f"d.bg_opa = LV_OPA_COVER; d.radius = {K};\n    for (int k = 0; k < 16; k++) {{")
rep("lv_area_t a = {(lv_coord_t)(c.x1 + k * 4), c.y1, (lv_coord_t)(c.x1 + k * 4 + 2), c.y2};",
    f"lv_area_t a = {{(lv_coord_t)(c.x1 + k * 4 * {K}), c.y1, (lv_coord_t)(c.x1 + k * 4 * {K} + 3 * {K} - 1), c.y2}};")
# графік світла (екран «Відключення»)
rep("int w = lv_area_get_width(&c), barH = 18;", f"int w = lv_area_get_width(&c), barH = 18 * {K};")
rep("lv_area_t tk = {(lv_coord_t)x, c.y1, (lv_coord_t)x, (lv_coord_t)(c.y1 + barH + 2)};",
    f"lv_area_t tk = {{(lv_coord_t)x, c.y1, (lv_coord_t)(x + {K} - 1), (lv_coord_t)(c.y1 + barH + 2 * {K})}};")
rep("lv_area_t la = {(lv_coord_t)(x + 1), (lv_coord_t)(c.y1 + barH + 1), (lv_coord_t)(x + 24), c.y2};",
    f"lv_area_t la = {{(lv_coord_t)(x + {K}), (lv_coord_t)(c.y1 + barH + {K}), (lv_coord_t)(x + 24 * {K}), c.y2}};")
rep("m.bg_color = col(0xFFFFFF); m.radius = 1;", f"m.bg_color = col(0xFFFFFF); m.radius = {K};")
rep("lv_area_t ma = {(lv_coord_t)(x - 1), (lv_coord_t)(c.y1 - 3), (lv_coord_t)(x + 1), (lv_coord_t)(c.y1 + barH + 2)};",
    f"lv_area_t ma = {{(lv_coord_t)(x - {K} - {K} / 2), (lv_coord_t)(c.y1 - 3 * {K}), (lv_coord_t)(x + {K} + {K} / 2), (lv_coord_t)(c.y1 + barH + 2 * {K})}};")

hdr = ('// AUTO-GENERATED by tools/sim/hd/make_ui_hd.py from src/ui.cpp — simulator only, do not edit\n'
       '#include <lvgl.h>\n#include "sim_scale.h"\n#line 1 "src/ui.cpp"\n')
out = os.path.join(HERE, "../build/hd/ui_hd.cpp")
open(out, "w", encoding="utf-8").write(hdr + src)
print("wrote", out)
