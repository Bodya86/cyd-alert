// CYD Alert UI simulator: compiles the REAL src/ui.cpp (included below, unchanged) against LVGL 8.3 on the host,
// renders into a 320x240 framebuffer and dumps PPM screenshots of scripted scenes.
// HD build (hd/build_hd.sh): SIM_K>1 — той самий UI, перераховані координати/шрифти/мапа ×K (див. hd/README).
// Usage: build/cyd_sim <out_dir>
#ifndef SIM_K
#define SIM_K 1
#endif
#ifdef SIM_UI_FILE
#include SIM_UI_FILE
#else
#include "../../src/ui.cpp"
#endif
#include <string>

extern "C" { extern uint32_t g_sim_ms; extern time_t g_sim_epoch; }
extern int g_sim_bat_mv;

static const int FB_W = 320 * SIM_K, FB_H = 240 * SIM_K;
static uint32_t* s_fb;
static lv_color_t* s_buf;
static void flushCb(lv_disp_drv_t* d, const lv_area_t* a, lv_color_t* px) {
  for (int y = a->y1; y <= a->y2; y++)
    for (int x = a->x1; x <= a->x2; x++) s_fb[y * FB_W + x] = lv_color_to32(*px++);
  lv_disp_flush_ready(d);
}
static void run(uint32_t ms) {
  for (uint32_t t = 0; t < ms; t += 5) { g_sim_ms += 5; lv_timer_handler(); }
}
static std::string s_outDir;
static void shot(const char* name) {
  lv_obj_invalidate(lv_scr_act());
  lv_refr_now(nullptr);
  std::string p = s_outDir + "/" + name + ".ppm";
  FILE* f = fopen(p.c_str(), "wb");
  fprintf(f, "P6\n%d %d\n255\n", FB_W, FB_H);
  for (int i = 0; i < FB_W * FB_H; i++) {
    uint32_t c = s_fb[i];
    uint8_t rgb[3] = {(uint8_t)(c >> 16), (uint8_t)(c >> 8), (uint8_t)c};
    fwrite(rgb, 1, 3, f);
  }
  fclose(f);
  printf("shot %s\n", p.c_str());
}

// ---------------- дані сцен ----------------
static int oblIdx(const char* name) {
  for (int i = 0; i < OBLAST_COUNT; i++) if (!strncmp(OBLASTS[i].name, name, strlen(name))) return i;
  fprintf(stderr, "no oblast %s\n", name); exit(1);
}
static int regionIdx(const char* name) {
  for (int i = 0; i < MAP_REGION_COUNT; i++) if (!strcmp(MAP_REGIONS[i].name, name)) return i;
  fprintf(stderr, "no region %s\n", name); exit(1);
}
static void clearAlerts() {
  memset(g_state.oblasts, 'N', OBLAST_COUNT); g_state.oblasts[OBLAST_COUNT] = 0;
  memset(g_state.regions, '.', MAP_REGION_COUNT); g_state.regions[MAP_REGION_COUNT] = 0;
  g_state.regionsFine = true; g_state.alertsOk = true; g_state.alertsSrc = "alerts.in.ua";
}
// уся область: c — 'R' ракети / 'Y' дрони (весь район), 'r'/'y' — частково
static void oblAlert(const char* obl, char c) {
  int o = oblIdx(obl);
  g_state.oblasts[o] = (c == 'r' || c == 'y') ? 'P' : 'A';
  for (int i = 0; i < MAP_REGION_COUNT; i++) if (MAP_REGIONS[i].oblast == o) g_state.regions[i] = c;
}
static void regAlert(const char* reg, char c) {
  int r = regionIdx(reg);
  g_state.regions[r] = c;
  char& o = g_state.oblasts[MAP_REGIONS[r].oblast];
  if (o == 'N') o = 'P';
}
// ціль + прогноз курсу (5/15/30 хв) і конус невизначеності
static MapTarget tgt(char ch, float lat, float lon, int hd, float kmh, int p, int eta, int n, std::initializer_list<float> mins = {5, 15, 30}) {
  MapTarget t; t.ch = ch; t.lat = lat; t.lon = lon; t.hd = hd; t.p = p; t.eta = eta; t.n = n;
  static const uint8_t SD[3] = {2, 5, 9};
  int k = 0;
  for (float m : mins) {
    if (k >= 3) break;
    float d = kmh * m / 60.0f, h = hd * 0.0174533f;
    t.pla[k] = lat + d * cosf(h) / 110.57f;
    t.plo[k] = lon + d * sinf(h) / (111.32f * cosf(lat * 0.0174533f));
    t.psd[k] = SD[k];
    k++;
  }
  t.npt = k;
  return t;
}
// пройдений шлях (від старих до нових точок) — цілі виляють
static MapTarget trail(MapTarget t, std::initializer_list<float> ll) {
  t.ntr = 0;
  for (auto it = ll.begin(); it != ll.end() && it + 1 != ll.end() && t.ntr < MAX_TRAIL; it += 2) {
    t.tla[t.ntr] = *it; t.tlo[t.ntr] = *(it + 1); t.ntr++;
  }
  return t;
}
static void setTargets(std::initializer_list<MapTarget> ts) {
  g_state.ntgt = 0;
  for (const MapTarget& t : ts) g_state.tgt[g_state.ntgt++] = t;
  g_state.trkOk = true;
}
static void resetThreat() {
  g_state.tgLevel = LVL_GREEN; g_state.tgScore = 0; g_state.tgExplicit = false; g_state.tgKind[0] = 0;
  g_state.trkOk = true; g_state.trkLevel = LVL_GREEN; g_state.ntgt = 0;
  g_state.pNear = g_state.pDrone = g_state.pMissile = g_state.pBallistic = 0; g_state.eta = -1;
  g_state.ltName[0] = 0; g_state.ltP = 0; strcpy(g_state.thr, "Загроз немає");
}
static time_t kyiv(int y, int mo, int d, int h, int mi) {
  struct tm tm = {}; tm.tm_year = y - 1900; tm.tm_mon = mo - 1; tm.tm_mday = d; tm.tm_hour = h; tm.tm_min = mi; tm.tm_isdst = -1;
  return mktime(&tm);
}
// графік відключень (черга 3.1, Полтаваобленерго): off — пари [з, до) у годинах
static void outage(std::initializer_list<std::pair<float, float>> today, std::initializer_list<std::pair<float, float>> tom) {
  OutageInfo& o = g_state.outage;
  o = OutageInfo();
  o.ndays = 2; o.ok = true; o.at = 1;
  strcpy(o.src, "Полтаваобленерго"); strcpy(o.updated, "29.10 18:12");
  o.days[0].ymd = 20261029; o.days[1].ymd = 20261030;
  int di = 0;
  for (auto lst : {today, tom}) {
    for (int k = 0; k < 48; k++) o.days[di].c[k] = OUT_ON;
    for (auto& pr : lst) for (int k = (int)(pr.first * 2); k < (int)(pr.second * 2); k++) o.days[di].c[k] = OUT_OFF;
    di++;
  }
}
static void resetMainView() {
  s_zoom = 0; s_view = &MAP_VIEW_UA; s_manualZoomAt = 0; s_autoHoldAt = 0; s_previewUntil = 0;
}
static void publish() {
  g_state.level = computeFinalLevel(g_state);
  g_state.version++;
}
static void blank() {   // прибрати попередній екран (інакше mainDeleted старого головного обнулить вказівники нового)
  lv_obj_t* b = lv_obj_create(nullptr);
  lv_scr_load_anim(b, LV_SCR_LOAD_ANIM_NONE, 0, 0, true);
  run(50);
}
static void mainShot(const char* name, int forceZoom = -1) {
  publish();
  resetMainView();
  blank();
  showMain();
  run(1500);
  if (forceZoom >= 0) { setZoom(forceZoom, false); s_manualZoomAt = millis() | 1; run(300); }
  if (s_uiLevel >= LVL_ORANGE) {   // кадр, коли пульс картки на яскравій фазі
    uint32_t hi = s_uiLevel == LVL_RED ? 0xFF3B3F : C_ORANGE;
    for (int i = 0; i < 4 && lv_obj_get_style_border_color(s_card, 0).full != col(hi).full; i++) run(RED_PULSE_MS);
  }
  printf("  level=%d zoom=%d\n", (int)s_uiLevel, (int)s_zoom);
  shot(name);
}

int main(int argc, char** argv) {
  s_outDir = argc > 1 ? argv[1] : ".";
  setenv("TZ", TZ_INFO, 1); tzset();
  g_sim_epoch = kyiv(2026, 10, 29, 19, 40);

  lv_init();
  static lv_disp_draw_buf_t db;
  s_fb = (uint32_t*)calloc((size_t)FB_W * FB_H, 4);
  s_buf = (lv_color_t*)calloc((size_t)FB_W * FB_H, sizeof(lv_color_t));
  lv_disp_draw_buf_init(&db, s_buf, nullptr, FB_W * FB_H);
  static lv_disp_drv_t dd;
  lv_disp_drv_init(&dd);
  dd.hor_res = FB_W; dd.ver_res = FB_H; dd.flush_cb = flushCb; dd.draw_buf = &db;
  lv_disp_drv_register(&dd);

  // налаштування користувача (як на його платі): Кременчук, Полтавська обл., черга 3.1
  g_set.ssid = "TP-Link_7F3A"; g_set.pass = "x";
  g_set.server = DEFAULT_SERVER_URL;
  g_set.oblast = oblIdx("Полтавська");
  for (int i = 0; i < CITY_COUNT; i++) if (!strcmp(CITIES[i].id, "kremenchuk")) g_set.city = i;
  g_set.district = -1;
  g_set.brightness = 179;   // 70 %
  g_set.battery = true; g_sim_bat_mv = 3984;   // 78 %
  g_set.outMode = OUT_MODE_AUTO;
  for (int i = 0; i < OUTAGE_GROUP_COUNT; i++) if (!strcmp(OUTAGE_GROUPS[i], "3.1")) g_set.outGroup = i;
  strcpy(g_ota.remote, FW_VERSION);
  outage({{7, 10.5}, {21, 24}}, {{0, 1.5}, {11, 14.5}, {21, 24}});

  lv_disp_t* d = lv_disp_get_default();
  lv_theme_t* th = lv_theme_default_init(d, col(C_ACCENT), col(C_RED), true, &font_ua_14);
  lv_disp_set_theme(d, th);
  memset(s_ob, '?', OBLAST_COUNT); s_ob[OBLAST_COUNT] = 0;
  memset(s_rg, '?', MAP_REGION_COUNT); s_rg[MAP_REGION_COUNT] = 0;

  // 1 — спокій
  clearAlerts(); resetThreat();
  mainShot("01_map_calm");

  // 2 — офіційні тривоги по Україні (Полтавщина чиста)
  clearAlerts(); resetThreat();
  oblAlert("Харківська", 'R'); oblAlert("Сумська", 'R'); oblAlert("Донецька", 'R'); oblAlert("Луганська", 'Y');
  oblAlert("Запорізька", 'R'); oblAlert("Херсонська", 'Y'); oblAlert("АР Крим", 'Y'); oblAlert("Чернігівська", 'Y');
  oblAlert("Миколаївська", 'y'); regAlert("Баштанський район", 'Y'); regAlert("Миколаївський район", 'Y');
  regAlert("Павлоградський район", 'R'); regAlert("Синельниківський район", 'R'); regAlert("Нікопольський район", 'R');
  regAlert("Дніпровський район", 'Y'); regAlert("Одеський район", 'Y'); regAlert("Ізмаїльський район", 'y');
  mainShot("02_map_alerts_ukraine");

  // 3 — Полтавщина: тривога, дрони в області (жовтий)
  clearAlerts(); resetThreat();
  oblAlert("Харківська", 'Y'); oblAlert("Сумська", 'R'); oblAlert("Донецька", 'R'); oblAlert("Луганська", 'Y');
  oblAlert("Полтавська", 'Y'); regAlert("Павлоградський район", 'Y'); oblAlert("Запорізька", 'R'); oblAlert("АР Крим", 'Y');
  setTargets({tgt('S', 49.85f, 35.30f, 240, 150, 9, -1, 3), tgt('S', 50.35f, 34.30f, 210, 150, 14, -1, 2),
              tgt('S', 49.42f, 34.62f, 262, 150, 18, 24, 1)});
  g_state.trkLevel = LVL_YELLOW; g_state.pNear = 18; g_state.pDrone = 18; g_state.eta = 24;
  strcpy(g_state.thr, "Дрони — Шахед/БпЛА, область"); strcpy(g_state.tgKind, "Дрони");
  strcpy(g_state.ltName, "Полтава"); g_state.ltP = 46;
  mainShot("03_poltava_drones_yellow");

  // 4 — курс на місто (помаранчевий)
  clearAlerts(); resetThreat();
  oblAlert("Харківська", 'Y'); oblAlert("Сумська", 'R'); oblAlert("Донецька", 'R'); oblAlert("Луганська", 'Y');
  oblAlert("Полтавська", 'Y'); oblAlert("Запорізька", 'R'); oblAlert("АР Крим", 'Y'); regAlert("Олександрійський район", 'Y');
  setTargets({trail(tgt('S', 49.20f, 34.45f, 250, 150, 42, 12, 2), {49.47f, 35.28f, 49.30f, 35.05f, 49.38f, 34.80f, 49.24f, 34.64f}),
              trail(tgt('S', 48.87f, 34.65f, 290, 150, 21, -1, 1), {48.62f, 35.30f, 48.78f, 35.12f, 48.70f, 34.88f})});
  g_state.trkLevel = LVL_ORANGE; g_state.pNear = 42; g_state.pDrone = 42; g_state.eta = 12;
  g_state.tgLevel = LVL_ORANGE; g_state.tgScore = 7; g_state.tgExplicit = true; strcpy(g_state.tgKind, "Дрони");
  strcpy(g_state.thr, "Дрони — Шахед/БпЛА, місто");
  strcpy(g_state.ltName, "Кременчук"); g_state.ltP = 55;
  mainShot("04_course_to_city_orange");
  mainShot("08_trail_weave_zoom", 1);
  // 7 — деталі загрози для того ж стану
  showDetail(); run(800); shot("07_threat_details");

  // 5 — небезпека: ракети + балістика (червоний)
  clearAlerts(); resetThreat();
  oblAlert("Харківська", 'R'); oblAlert("Сумська", 'R'); oblAlert("Донецька", 'R'); oblAlert("Луганська", 'Y');
  oblAlert("Полтавська", 'R'); oblAlert("Дніпропетровська", 'R'); oblAlert("Кіровоградська", 'R'); oblAlert("Черкаська", 'R');
  oblAlert("Запорізька", 'R'); oblAlert("АР Крим", 'Y'); oblAlert("Київська", 'Y');
  setTargets({tgt('C', 48.80f, 33.05f, 42, 800, 64, 3, 2, {2, 4, 6}), tgt('K', 48.86f, 33.86f, 333, 600, 38, 5, 1, {2, 5, 8}),
              tgt('B', 49.13f, 34.32f, 266, 3000, 71, 1, 1, {0.5f, 1.0f})});
  g_state.trkLevel = LVL_RED; g_state.pNear = 64; g_state.pMissile = 64; g_state.pBallistic = 71; g_state.eta = 1;
  g_state.tgLevel = LVL_RED; g_state.tgScore = 9; g_state.tgExplicit = true; strcpy(g_state.tgKind, "Ракети");
  strcpy(g_state.thr, "Ракети, місто");
  strcpy(g_state.ltName, "Кременчук"); g_state.ltP = 72;
  mainShot("05_missiles_ballistic_red");

  // 6 — наближена мапа (кнопки + / − / ⌂): околиці Кременчука, спокійно
  clearAlerts(); resetThreat();
  mainShot("06_zoom_city_buttons", 2);

  // 8 — графік відключень: світла немає (червоний)
  outage({{7, 10.5}, {18, 21.5}}, {{0, 1.5}, {11, 14.5}, {21, 24}});
  clearAlerts(); resetThreat(); publish();
  showOutage(); run(800); shot("08_power_outage");
  outage({{7, 10.5}, {21, 24}}, {{0, 1.5}, {11, 14.5}, {21, 24}});

  // 9 — налаштування: яскравість 70 %
  showSettings(); run(800); shot("09_settings_brightness");
  // 11 (бонус) — ті самі налаштування, прокручено до перемикача «Заряд АКБ»
  {
    lv_obj_t* list = lv_obj_get_child(lv_scr_act(), 1);
    for (uint32_t i = 0; i < lv_obj_get_child_cnt(list); i++) {
      lv_obj_t* r = lv_obj_get_child(list, i);
      lv_obj_t* l = lv_obj_get_child(r, 0);
      if (l && lv_obj_check_type(l, &lv_label_class) && strstr(lv_label_get_text(l), "АКБ")) {
        lv_obj_scroll_to_view(r, LV_ANIM_OFF);
        lv_obj_scroll_by(list, 0, -94 * SIM_K, LV_ANIM_OFF);
      }
    }
    run(400); shot("11_settings_battery");
  }

  // 10 — вибір Wi-Fi
  WiFi.nets = {{"TP-Link_7F3A", -47, 6, 3}, {"Xiaomi_B133", -58, 11, 3}, {"Kyivstar_4G_21C0", -66, 1, 3},
               {"ASUS_RT-N12", -71, 6, 3}, {"Lanet_Home", -79, 3, 3}, {"DIRECT-2B-HP M28", -84, 6, 3}};
  showWifiScan(); run(1500);
  lv_obj_scroll_to_y(s_wifiList, LV_COORD_MAX, LV_ANIM_OFF);
  run(400); shot("10_wifi_select");
  return 0;
}
