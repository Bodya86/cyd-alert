// Інтерфейс на LVGL 8.3: майстер Wi-Fi, вибір локації, мапа, сповіщення, налаштування
#include "app.h"
#include "analyzer.h"
#include "textutil.h"
#include "map_meta.h"
#include <WiFi.h>
#include <lvgl.h>
#include <vector>

LV_FONT_DECLARE(font_ua_12)
LV_FONT_DECLARE(font_ua_14)
LV_FONT_DECLARE(font_ua_18)
LV_FONT_DECLARE(font_ua_26)
LV_FONT_DECLARE(font_ua_40)

// ------------------------------- Палітра (темна тема) -------------------------------
#define C_BG      0x0B0F14
#define C_PANEL   0x151B23
#define C_PANEL2  0x1E2631
#define C_BORDER  0x2B3440
#define C_TEXT    0xE6EDF3
#define C_MUTED   0x8B98A5
#define C_ACCENT  0x3B82F6
#define C_GREEN   0x2EA043
#define C_YELLOW  0xF2C94C
#define C_RED     0xE5484D
#define C_AMBER   0xF59E0B
#define C_MAP_N   0x263445
#define C_MAP_ME  0x3A5878
#define C_MAP_UNK 0x1A212B
#define C_MAP_OBL 0x2C3B4E   // райони вашої області
#define C_MAP_Y   0xD4A72C   // дрони / невідомий тип — весь район
#define C_MAP_YP  0x7C6526   // ... частково (окремі громади)
#define C_MAP_R   0xE5484D   // ракети / КАБ — весь район
#define C_MAP_RP  0x8A2E33   // ... частково
#define C_MAP_BRD 0x5E6C7E   // межі областей
#define C_MAP_RBD 0x18202A   // межі районів
#define C_OUT_ON  0x2EA043   // світло є
#define C_OUT_OFF 0x5B6573   // світла немає
#define C_OUT_MB  0xF59E0B   // можливе відключення

static lv_color_t col(uint32_t h) { return lv_color_hex(h); }
static uint32_t levelColor(Level l) { return l == LVL_RED ? C_RED : (l == LVL_YELLOW ? C_YELLOW : C_GREEN); }

enum ScreenKind { SK_NONE, SK_SETUP, SK_MAIN, SK_DETAIL, SK_ALERT, SK_SETTINGS, SK_OUTAGE };
static ScreenKind s_kind = SK_NONE;

// ------------------------------- Помічники -------------------------------
static lv_obj_t* mkScreen(ScreenKind k) {
  lv_obj_t* s = lv_obj_create(nullptr);
  lv_obj_set_style_bg_color(s, col(C_BG), 0);
  lv_obj_set_style_bg_opa(s, LV_OPA_COVER, 0);
  lv_obj_clear_flag(s, LV_OBJ_FLAG_SCROLLABLE);
  s_kind = k;
  return s;
}
static void loadScreen(lv_obj_t* s) { lv_scr_load_anim(s, LV_SCR_LOAD_ANIM_FADE_ON, 180, 0, true); }

static lv_obj_t* mkLabel(lv_obj_t* p, const char* txt, const lv_font_t* f, uint32_t c) {
  lv_obj_t* l = lv_label_create(p);
  lv_label_set_text(l, txt);
  lv_obj_set_style_text_font(l, f, 0);
  lv_obj_set_style_text_color(l, col(c), 0);
  return l;
}

static lv_obj_t* mkHeader(lv_obj_t* scr, const char* title, lv_event_cb_t back) {
  lv_obj_t* h = lv_obj_create(scr);
  lv_obj_remove_style_all(h);
  lv_obj_set_size(h, 320, 36);
  lv_obj_set_style_bg_color(h, col(C_PANEL), 0);
  lv_obj_set_style_bg_opa(h, LV_OPA_COVER, 0);
  lv_obj_set_style_border_side(h, LV_BORDER_SIDE_BOTTOM, 0);
  lv_obj_set_style_border_width(h, 1, 0);
  lv_obj_set_style_border_color(h, col(C_BORDER), 0);
  lv_obj_clear_flag(h, LV_OBJ_FLAG_SCROLLABLE);
  int x = 10;
  if (back) {
    lv_obj_t* b = lv_btn_create(h);
    lv_obj_set_size(b, 36, 28);
    lv_obj_align(b, LV_ALIGN_LEFT_MID, 4, 0);
    lv_obj_set_style_bg_color(b, col(C_PANEL2), 0);
    lv_obj_set_style_shadow_width(b, 0, 0);
    lv_obj_add_event_cb(b, back, LV_EVENT_CLICKED, nullptr);
    lv_obj_t* i = mkLabel(b, LV_SYMBOL_LEFT, &font_ua_14, C_TEXT);
    lv_obj_center(i);
    x = 48;
  }
  lv_obj_t* t = mkLabel(h, title, &font_ua_18, C_TEXT);
  lv_obj_set_width(t, 320 - x - 50);
  lv_label_set_long_mode(t, LV_LABEL_LONG_DOT);
  lv_obj_align(t, LV_ALIGN_LEFT_MID, x, 0);
  return h;
}

static lv_obj_t* mkHeaderBtn(lv_obj_t* header, const char* sym, lv_event_cb_t cb) {
  lv_obj_t* b = lv_btn_create(header);
  lv_obj_set_size(b, 40, 28);
  lv_obj_align(b, LV_ALIGN_RIGHT_MID, -4, 0);
  lv_obj_set_style_bg_color(b, col(C_ACCENT), 0);
  lv_obj_set_style_shadow_width(b, 0, 0);
  lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, nullptr);
  lv_obj_center(mkLabel(b, sym, &font_ua_14, C_TEXT));
  return b;
}

static lv_obj_t* mkList(lv_obj_t* scr, int y, int h) {
  lv_obj_t* l = lv_list_create(scr);
  lv_obj_set_size(l, 320, h);
  lv_obj_set_pos(l, 0, y);
  lv_obj_set_style_bg_color(l, col(C_BG), 0);
  lv_obj_set_style_border_width(l, 0, 0);
  lv_obj_set_style_radius(l, 0, 0);
  lv_obj_set_style_pad_all(l, 4, 0);
  lv_obj_set_style_pad_row(l, 4, 0);
  return l;
}

static lv_obj_t* mkListBtn(lv_obj_t* list, const char* icon, const char* txt, lv_event_cb_t cb, void* ud) {
  lv_obj_t* b = lv_list_add_btn(list, icon, txt);
  lv_obj_set_style_bg_color(b, col(C_PANEL), 0);
  lv_obj_set_style_bg_color(b, col(C_PANEL2), LV_STATE_PRESSED);
  lv_obj_set_style_text_color(b, col(C_TEXT), 0);
  lv_obj_set_style_radius(b, 8, 0);
  lv_obj_set_style_border_width(b, 0, 0);
  lv_obj_set_style_pad_ver(b, 9, 0);
  lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, ud);
  lv_obj_t* lbl = lv_obj_get_child(b, -1);
  if (lbl) lv_label_set_long_mode(lbl, LV_LABEL_LONG_DOT);
  return b;
}

static void toast(const char* txt, uint32_t color = C_PANEL2) {
  lv_obj_t* t = lv_obj_create(lv_layer_top());
  lv_obj_set_style_bg_color(t, col(color), 0);
  lv_obj_set_style_border_width(t, 0, 0);
  lv_obj_set_style_radius(t, 12, 0);
  lv_obj_set_style_pad_all(t, 10, 0);
  lv_obj_set_size(t, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
  lv_obj_t* l = mkLabel(t, txt, &font_ua_14, color == C_PANEL2 ? C_TEXT : 0x0B0F14);
  lv_obj_set_style_max_width(l, 280, 0);
  lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
  lv_obj_align(t, LV_ALIGN_BOTTOM_MID, 0, -12);
  lv_obj_del_delayed(t, 2500);
}

static String fmtTime(time_t t) {
  if (t < 100000) return "--:--";
  struct tm tm; localtime_r(&t, &tm);
  char b[8]; snprintf(b, sizeof(b), "%02d:%02d", tm.tm_hour, tm.tm_min);
  return b;
}

// Українська клавіатура для пошуку адреси
static const char* KB_UA_MAP[] = {
  "й", "ц", "у", "к", "е", "н", "г", "ш", "щ", "з", "х", LV_SYMBOL_BACKSPACE, "\n",
  "ф", "і", "в", "а", "п", "р", "о", "л", "д", "ж", "є", "\n",
  "1#", "я", "ч", "с", "м", "и", "т", "ь", "б", "ю", "ї", "\n",
  LV_SYMBOL_KEYBOARD, "'", " ", "-", ".", LV_SYMBOL_OK, ""};
static const lv_btnmatrix_ctrl_t KB_UA_CTRL[] = {
  1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, (lv_btnmatrix_ctrl_t)(LV_KEYBOARD_CTRL_BTN_FLAGS | 2),
  1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
  (lv_btnmatrix_ctrl_t)(LV_KEYBOARD_CTRL_BTN_FLAGS | 2), 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
  (lv_btnmatrix_ctrl_t)(LV_KEYBOARD_CTRL_BTN_FLAGS | 2), 1, 6, 1, 1, (lv_btnmatrix_ctrl_t)(LV_KEYBOARD_CTRL_BTN_FLAGS | 2)};

static lv_obj_t* mkKeyboard(lv_obj_t* scr, lv_obj_t* ta, bool ukr) {
  lv_obj_t* kb = lv_keyboard_create(scr);
  lv_obj_set_size(kb, 320, 128);
  lv_obj_align(kb, LV_ALIGN_BOTTOM_MID, 0, 0);
  lv_obj_set_style_bg_color(kb, col(C_PANEL), 0);
  lv_obj_set_style_pad_all(kb, 3, 0);
  lv_obj_set_style_pad_gap(kb, 3, 0);
  lv_obj_set_style_text_font(kb, &font_ua_14, LV_PART_ITEMS);
  lv_obj_set_style_bg_color(kb, col(C_PANEL2), LV_PART_ITEMS);
  lv_obj_set_style_text_color(kb, col(C_TEXT), LV_PART_ITEMS);
  lv_obj_set_style_radius(kb, 5, LV_PART_ITEMS);
  lv_keyboard_set_textarea(kb, ta);
  if (ukr) {
    lv_keyboard_set_map(kb, LV_KEYBOARD_MODE_USER_1, KB_UA_MAP, KB_UA_CTRL);
    lv_keyboard_set_mode(kb, LV_KEYBOARD_MODE_USER_1);
  }
  return kb;
}

static lv_obj_t* mkTextArea(lv_obj_t* scr, int y, const char* placeholder) {
  lv_obj_t* ta = lv_textarea_create(scr);
  lv_textarea_set_one_line(ta, true);
  lv_obj_set_size(ta, 304, 38);
  lv_obj_set_pos(ta, 8, y);
  lv_textarea_set_placeholder_text(ta, placeholder);
  lv_obj_set_style_bg_color(ta, col(C_PANEL), 0);
  lv_obj_set_style_border_color(ta, col(C_ACCENT), LV_STATE_FOCUSED);
  lv_obj_set_style_text_font(ta, &font_ua_14, 0);
  lv_obj_add_state(ta, LV_STATE_FOCUSED);
  return ta;
}

// ======================================================================================
//                                       Wi-Fi
// ======================================================================================
static void showWifiScan();
static void showPassword(const String& ssid);
static void showConnecting(const String& ssid, const String& pass, bool fromSetup);
static void showOblastPicker();
static void showMain();
static void showSettings();

static std::vector<String> s_ssids;
static lv_obj_t* s_wifiList = nullptr;
static lv_obj_t* s_wifiSpinner = nullptr;
static lv_timer_t* s_scanTimer = nullptr;

static void wifiItemCb(lv_event_t* e) {
  int i = (int)(intptr_t)lv_event_get_user_data(e);
  if (s_scanTimer) { lv_timer_del(s_scanTimer); s_scanTimer = nullptr; }
  if (i < 0) { showPassword(""); return; }  // ручне введення SSID
  showPassword(s_ssids[i]);
}

static void scanTimerCb(lv_timer_t*) {
  int n = WiFi.scanComplete();
  if (n == WIFI_SCAN_RUNNING) return;
  if (n == WIFI_SCAN_FAILED) { WiFi.scanNetworks(true); return; }
  lv_timer_del(s_scanTimer); s_scanTimer = nullptr;
  if (s_wifiSpinner) { lv_obj_del(s_wifiSpinner); s_wifiSpinner = nullptr; }
  lv_obj_clean(s_wifiList);
  s_ssids.clear();
  for (int i = 0; i < n && s_ssids.size() < 25; i++) {
    String ss = WiFi.SSID(i);
    if (!ss.length()) continue;
    bool dup = false;
    for (auto& x : s_ssids) if (x == ss) dup = true;
    if (dup) continue;
    s_ssids.push_back(ss);
    int rssi = WiFi.RSSI(i);
    char txt[64];
    snprintf(txt, sizeof(txt), "%s   %s%d dBm", ss.c_str(), WiFi.encryptionType(i) == WIFI_AUTH_OPEN ? "" : "• ", rssi);
    lv_obj_t* b = mkListBtn(s_wifiList, LV_SYMBOL_WIFI, txt, wifiItemCb, (void*)(intptr_t)(s_ssids.size() - 1));
    lv_obj_t* icon = lv_obj_get_child(b, 0);
    if (icon) lv_obj_set_style_text_color(icon, col(rssi > -60 ? C_GREEN : rssi > -75 ? C_YELLOW : C_RED), 0);
  }
  mkListBtn(s_wifiList, LV_SYMBOL_EDIT, "Ввести назву мережі вручну", wifiItemCb, (void*)(intptr_t)-1);
  WiFi.scanDelete();
}

static void startScan() {
  WiFi.scanDelete();
  WiFi.scanNetworks(true, false);
  if (s_scanTimer) lv_timer_del(s_scanTimer);
  s_scanTimer = lv_timer_create(scanTimerCb, 300, nullptr);
}

static void rescanCb(lv_event_t*) { showWifiScan(); }
static void wifiBackCb(lv_event_t*) {
  if (s_scanTimer) { lv_timer_del(s_scanTimer); s_scanTimer = nullptr; }
  if (g_set.configured() && WiFi.status() == WL_CONNECTED) showSettings();
  else showWifiScan();
}

static void showWifiScan() {
  lv_obj_t* scr = mkScreen(SK_SETUP);
  lv_obj_t* h = mkHeader(scr, "Оберіть Wi-Fi мережу", g_set.configured() ? wifiBackCb : nullptr);
  mkHeaderBtn(h, LV_SYMBOL_REFRESH, rescanCb);
  s_wifiList = mkList(scr, 36, 204);
  s_wifiSpinner = lv_spinner_create(scr, 1000, 60);
  lv_obj_set_size(s_wifiSpinner, 50, 50);
  lv_obj_center(s_wifiSpinner);
  lv_obj_set_style_arc_color(s_wifiSpinner, col(C_ACCENT), LV_PART_INDICATOR);
  loadScreen(scr);
  startScan();
}

static lv_obj_t* s_taSsid = nullptr;
static lv_obj_t* s_taPass = nullptr;
static String s_pendingSsid;

static void passReadyCb(lv_event_t*) {
  String ssid = s_taSsid ? String(lv_textarea_get_text(s_taSsid)) : s_pendingSsid;
  ssid.trim();
  if (!ssid.length()) { toast("Введіть назву мережі"); return; }
  showConnecting(ssid, lv_textarea_get_text(s_taPass), true);
}
static void eyeCb(lv_event_t*) {
  lv_textarea_set_password_mode(s_taPass, !lv_textarea_get_password_mode(s_taPass));
}
static void toScanCb(lv_event_t*) { showWifiScan(); }

static void showPassword(const String& ssid) {
  lv_obj_t* scr = mkScreen(SK_SETUP);
  s_pendingSsid = ssid;
  String title = ssid.length() ? ("Пароль: " + ssid) : String("Нова мережа");
  lv_obj_t* h = mkHeader(scr, title.c_str(), toScanCb);
  mkHeaderBtn(h, LV_SYMBOL_EYE_OPEN, eyeCb);
  int y = 42;
  s_taSsid = nullptr;
  if (!ssid.length()) {
    s_taSsid = mkTextArea(scr, y, "Назва мережі (SSID)");
    lv_obj_set_height(s_taSsid, 32);
    y += 36;
  }
  s_taPass = mkTextArea(scr, y, "Пароль Wi-Fi");
  lv_obj_set_height(s_taPass, s_taSsid ? 32 : 38);
  lv_textarea_set_password_mode(s_taPass, true);
  if (s_taSsid) lv_obj_clear_state(s_taPass, LV_STATE_FOCUSED);
  lv_obj_t* kb = mkKeyboard(scr, s_taSsid ? s_taSsid : s_taPass, false);
  lv_obj_set_height(kb, s_taSsid ? 124 : 140);
  if (s_taSsid) {
    // перемикати клавіатуру між полями при натисканні
    auto focusCb = [](lv_event_t* e) {
      lv_obj_t* kbd = (lv_obj_t*)lv_event_get_user_data(e);
      lv_obj_t* ta = lv_event_get_target(e);
      lv_keyboard_set_textarea(kbd, ta);
      lv_obj_add_state(ta, LV_STATE_FOCUSED);
      lv_obj_clear_state(ta == s_taPass ? s_taSsid : s_taPass, LV_STATE_FOCUSED);
    };
    lv_obj_add_event_cb(s_taSsid, focusCb, LV_EVENT_CLICKED, kb);
    lv_obj_add_event_cb(s_taPass, focusCb, LV_EVENT_CLICKED, kb);
  }
  lv_obj_add_event_cb(kb, passReadyCb, LV_EVENT_READY, nullptr);
  loadScreen(scr);
}

static lv_timer_t* s_connTimer = nullptr;
static uint32_t s_connStart = 0;
static String s_connSsid, s_connPass;

static void connTimerCb(lv_timer_t*) {
  wl_status_t st = WiFi.status();
  if (st == WL_CONNECTED) {
    lv_timer_del(s_connTimer); s_connTimer = nullptr;
    g_set.ssid = s_connSsid; g_set.pass = s_connPass;
    settingsSave();
    netForceRefresh();
    if (!g_set.configured()) showOblastPicker(); else showMain();
    return;
  }
  if (millis() - s_connStart > 20000) {
    lv_timer_del(s_connTimer); s_connTimer = nullptr;
    showWifiScan();
    toast("Не вдалося підключитися. Перевірте пароль.", C_RED);
  }
}

static void showConnecting(const String& ssid, const String& pass, bool) {
  lv_obj_t* scr = mkScreen(SK_SETUP);
  lv_obj_t* logo = mkLabel(scr, "CYD ALERT", &font_ua_26, C_TEXT);
  lv_obj_align(logo, LV_ALIGN_TOP_MID, 0, 30);
  lv_obj_t* sub = mkLabel(scr, "Мапа повітряних тривог України", &font_ua_14, C_MUTED);
  lv_obj_align(sub, LV_ALIGN_TOP_MID, 0, 64);
  lv_obj_t* sp = lv_spinner_create(scr, 1000, 60);
  lv_obj_set_size(sp, 56, 56);
  lv_obj_align(sp, LV_ALIGN_CENTER, 0, 20);
  lv_obj_set_style_arc_color(sp, col(C_ACCENT), LV_PART_INDICATOR);
  String t = "Підключення до «" + ssid + "»…";
  lv_obj_t* l = mkLabel(scr, t.c_str(), &font_ua_14, C_TEXT);
  lv_obj_align(l, LV_ALIGN_BOTTOM_MID, 0, -30);
  loadScreen(scr);
  s_connSsid = ssid; s_connPass = pass;
  WiFi.disconnect();
  WiFi.begin(ssid.c_str(), pass.c_str());
  s_connStart = millis();
  if (s_connTimer) lv_timer_del(s_connTimer);
  s_connTimer = lv_timer_create(connTimerCb, 250, nullptr);
}

// ======================================================================================
//                               Вибір області / міста / району
// ======================================================================================
static int8_t s_selOblast = -1;
static int16_t s_selCity = -1;
static void showCityPicker();
static void showDistrictPicker();
static void showAddressSearch();

static void finishLocation(int16_t district) {
  g_set.oblast = s_selOblast;
  g_set.city = s_selCity;
  g_set.district = district;
  settingsSave();
  stateLock();
  g_state.tgLevel = LVL_GREEN; g_state.tgText[0] = 0; g_state.tgReason[0] = 0; g_state.tgTime = 0;
  g_state.level = computeFinalLevel(g_state); g_state.version++;
  stateUnlock();
  netForceRefresh();
  showMain();
  toast(("Локація: " + userPlaceName()).c_str());
}

static void oblastCb(lv_event_t* e) { s_selOblast = (int8_t)(intptr_t)lv_event_get_user_data(e); showCityPicker(); }
static void locBackToMain(lv_event_t*) { if (g_set.configured()) showSettings(); }

static void showOblastPicker() {
  lv_obj_t* scr = mkScreen(SK_SETUP);
  mkHeader(scr, "Крок 1/3 · Оберіть область", g_set.configured() ? locBackToMain : nullptr);
  lv_obj_t* list = mkList(scr, 36, 204);
  // спочатку області з готовими містами (Полтавська — перша, бо там Кременчук)
  mkListBtn(list, LV_SYMBOL_GPS, OBLASTS[16].name, oblastCb, (void*)(intptr_t)16);
  for (int i = 0; i < OBLAST_COUNT; i++)
    if (i != 16) mkListBtn(list, LV_SYMBOL_GPS, OBLASTS[i].name, oblastCb, (void*)(intptr_t)i);
  loadScreen(scr);
}

static void cityCb(lv_event_t* e) {
  s_selCity = (int16_t)(intptr_t)lv_event_get_user_data(e);
  if (s_selCity >= 0 && CITIES[s_selCity].ndist > 0) showDistrictPicker();
  else finishLocation(-1);
}
static void toOblastCb(lv_event_t*) { showOblastPicker(); }

static void showCityPicker() {
  lv_obj_t* scr = mkScreen(SK_SETUP);
  String t = String("Крок 2/3 · ") + OBLASTS[s_selOblast].shortName;
  mkHeader(scr, t.c_str(), toOblastCb);
  lv_obj_t* list = mkList(scr, 36, 204);
  for (int i = 0; i < CITY_COUNT; i++)
    if (CITIES[i].oblast == s_selOblast) {
      String n = CITIES[i].name;
      if (CITIES[i].ndist) n += "  (" + String(CITIES[i].ndist) + " р-нів)";
      mkListBtn(list, LV_SYMBOL_HOME, n.c_str(), cityCb, (void*)(intptr_t)i);
    }
  mkListBtn(list, LV_SYMBOL_LIST, "Уся область (без міста)", cityCb, (void*)(intptr_t)-1);
  loadScreen(scr);
}

static void districtCb(lv_event_t* e) { finishLocation((int16_t)(intptr_t)lv_event_get_user_data(e)); }
static void searchCb(lv_event_t*) { showAddressSearch(); }
static void toCityCb(lv_event_t*) { showCityPicker(); }

static void showDistrictPicker() {
  const CityDef& c = CITIES[s_selCity];
  lv_obj_t* scr = mkScreen(SK_SETUP);
  String t = String("Крок 3/3 · ") + c.name;
  mkHeader(scr, t.c_str(), toCityCb);
  lv_obj_t* list = mkList(scr, 36, 204);
  if (c.nstreets) {
    lv_obj_t* b = mkListBtn(list, LV_SYMBOL_EDIT, "Знайти за адресою (вулиця)", searchCb, nullptr);
    lv_obj_set_style_bg_color(b, col(0x1D3557), 0);
  }
  for (int i = 0; i < c.ndist; i++) mkListBtn(list, LV_SYMBOL_GPS, c.districts[i].name, districtCb, (void*)(intptr_t)i);
  mkListBtn(list, LV_SYMBOL_HOME, "Усе місто", districtCb, (void*)(intptr_t)-1);
  loadScreen(scr);
}

static lv_obj_t* s_searchTa = nullptr;
static lv_obj_t* s_searchList = nullptr;
static lv_obj_t* s_searchKb = nullptr;

static void streetCb(lv_event_t* e) {
  int idx = (int)(intptr_t)lv_event_get_user_data(e);
  finishLocation(CITIES[s_selCity].streets[idx].district);
}

static void searchUpdate() {
  const CityDef& c = CITIES[s_selCity];
  char q[64], nm[96];
  utf8Lower(lv_textarea_get_text(s_searchTa), q, sizeof(q));
  lv_obj_clean(s_searchList);
  int shown = 0;
  for (int i = 0; i < c.nstreets && shown < 20; i++) {
    utf8Lower(c.streets[i].name, nm, sizeof(nm));
    if (q[0] && !strstr(nm, q)) continue;
    char txt[128];
    snprintf(txt, sizeof(txt), "%s  →  %s", c.streets[i].name, c.districts[c.streets[i].district].name);
    mkListBtn(s_searchList, LV_SYMBOL_GPS, txt, streetCb, (void*)(intptr_t)i);
    shown++;
  }
  if (!shown) lv_list_add_text(s_searchList, "Нічого не знайдено — оберіть район вручну");
}
static void searchChangedCb(lv_event_t*) { searchUpdate(); }
static void kbHideCb(lv_event_t*) { lv_obj_add_flag(s_searchKb, LV_OBJ_FLAG_HIDDEN); lv_obj_set_height(s_searchList, 160); }
static void taClickCb(lv_event_t*) { lv_obj_clear_flag(s_searchKb, LV_OBJ_FLAG_HIDDEN); lv_obj_set_height(s_searchList, 36); }
static void ukrKbCb(lv_event_t*) { lv_keyboard_set_mode(s_searchKb, LV_KEYBOARD_MODE_USER_1); taClickCb(nullptr); }
static void toDistrictCb(lv_event_t*) { showDistrictPicker(); }

static void showAddressSearch() {
  lv_obj_t* scr = mkScreen(SK_SETUP);
  lv_obj_t* h = mkHeader(scr, "Пошук вулиці", toDistrictCb);
  mkHeaderBtn(h, "УКР", ukrKbCb);
  s_searchTa = mkTextArea(scr, 40, "Почніть вводити назву…");
  lv_obj_set_height(s_searchTa, 34);
  s_searchList = mkList(scr, 76, 36);
  s_searchKb = mkKeyboard(scr, s_searchTa, true);
  lv_obj_add_event_cb(s_searchTa, searchChangedCb, LV_EVENT_VALUE_CHANGED, nullptr);
  lv_obj_add_event_cb(s_searchTa, taClickCb, LV_EVENT_CLICKED, nullptr);
  lv_obj_add_event_cb(s_searchKb, kbHideCb, LV_EVENT_READY, nullptr);
  lv_obj_add_event_cb(s_searchKb, kbHideCb, LV_EVENT_CANCEL, nullptr);
  searchUpdate();
  loadScreen(scr);
}

// ======================================================================================
//                                     Головний екран
// ======================================================================================
static char s_ob[OBLAST_COUNT + 1];
static char s_rg[MAP_REGION_COUNT + 1];
static lv_color_t s_pal[256];
static lv_obj_t* s_main = nullptr;
static lv_obj_t *s_map, *s_lblPlace, *s_lblTime, *s_lblWifi, *s_lblCount, *s_lblCountSub, *s_lblSrc, *s_card, *s_cardBar,
    *s_cardTitle, *s_cardText, *s_marker, *s_outBox, *s_outState, *s_outTime, *s_outBar;
static uint32_t s_seenVersion = UINT32_MAX;
static OutageInfo s_out;   // знімок для головного екрана / екрана графіка

static uint32_t regionColor(int i, int me) {
  switch (s_rg[i]) {
    case 'R': return C_MAP_R;
    case 'r': return C_MAP_RP;
    case 'Y': return C_MAP_Y;
    case 'y': return C_MAP_YP;
    case '.': return i == me ? C_MAP_ME : (MAP_REGIONS[i].oblast == (uint8_t)g_set.oblast ? C_MAP_OBL : C_MAP_N);
    default: return C_MAP_UNK;
  }
}

static void rebuildPalette() {
  int me = userRegion();
  for (int i = 0; i < MAP_REGION_COUNT; i++) s_pal[i] = col(regionColor(i, me));
  s_pal[MAP_RBORDER] = col(C_MAP_RBD);
  s_pal[MAP_BORDER] = col(C_MAP_BRD);
  s_pal[MAP_BG] = col(C_BG);
}

static void drawTargets(lv_draw_ctx_t* dc, const lv_area_t& c);

// мапа: RLE-рядки → прямокутники; сусідні відрізки з однаковим кольором зливаються
static void mapDrawCb(lv_event_t* e) {
  lv_obj_t* obj = lv_event_get_target(e);
  lv_draw_ctx_t* dc = lv_event_get_draw_ctx(e);
  lv_area_t c; lv_obj_get_coords(obj, &c);
  const lv_area_t* clip = dc->clip_area;
  lv_draw_rect_dsc_t d; lv_draw_rect_dsc_init(&d);
  d.bg_opa = LV_OPA_COVER; d.radius = 0; d.border_width = 0;
  int y0 = LV_MAX(clip->y1, c.y1), y1 = LV_MIN(LV_MIN(clip->y2, c.y2), c.y1 + MAP_H - 1);
  for (int y = y0; y <= y1; y++) {
    int row = y - c.y1, x = 0, runX = 0, runN = 0;
    lv_color_t runC = s_pal[MAP_BG];
    auto flush = [&]() {
      if (!runN) return;
      lv_area_t a = {(lv_coord_t)(c.x1 + runX), (lv_coord_t)y, (lv_coord_t)(c.x1 + runX + runN - 1), (lv_coord_t)y};
      lv_area_t r;
      if (_lv_area_intersect(&r, &a, clip)) { d.bg_color = runC; lv_draw_rect(dc, &d, &r); }
      runN = 0;
    };
    for (int i = MAP_ROW_OFS[row]; i < MAP_ROW_OFS[row + 1]; i++) {
      uint8_t n = MAP_RUNS[i * 2], v = MAP_RUNS[i * 2 + 1];
      if (v == MAP_BG) { flush(); }
      else {
        lv_color_t cc = s_pal[v];
        if (runN && cc.full != runC.full) flush();
        if (!runN) { runX = x; runC = cc; }
        runN += n;
      }
      x += n;
    }
    flush();
  }
  drawTargets(dc, c);
}

// ---- цілі трекера поверх мапи: конус невизначеності, прогнозний шлях зі стрілкою, значок за типом ----
static MapTarget s_tgt[MAX_TARGETS];
static uint8_t s_ntgt = 0;

static inline lv_point_t mapPt(const lv_area_t& c, float lat, float lon) {
  lv_point_t p;
  p.x = (lv_coord_t)(c.x1 + (lon * MAP_PROJ_COSLAT - MAP_PROJ_MINX) * MAP_PROJ_SCALE + MAP_PROJ_OFFX);
  p.y = (lv_coord_t)(c.y1 + (-lat - MAP_PROJ_MINY) * MAP_PROJ_SCALE + MAP_PROJ_OFFY);
  return p;
}

static uint32_t targetColor(char ch) {
  switch (ch) {
    case 'C': return 0xFF4D4F;   // крилата ракета
    case 'K': return 0xFF8A3D;   // КАБ
    case 'J': return 0xFFB020;   // реактивний шахед
    case 'D': return 0x58A6FF;   // розвідувальний
    default:  return 0xFFD54A;   // шахед / невідомо
  }
}

static void drawTargets(lv_draw_ctx_t* dc, const lv_area_t& c) {
  if (!s_ntgt) return;
  const float KM_PX = MAP_PROJ_SCALE / 111.0f;      // пікселів на км (широта)
  lv_draw_rect_dsc_t cone; lv_draw_rect_dsc_init(&cone);
  cone.radius = LV_RADIUS_CIRCLE; cone.border_width = 0; cone.bg_opa = LV_OPA_20;
  lv_draw_line_dsc_t ln; lv_draw_line_dsc_init(&ln);
  ln.width = 1; ln.opa = LV_OPA_80; ln.round_start = 0; ln.round_end = 0;
  for (int k = s_ntgt - 1; k >= 0; k--) {           // найімовірніша ціль — зверху
    const MapTarget& t = s_tgt[k];
    lv_color_t tc = col(targetColor(t.ch));
    lv_point_t p0 = mapPt(c, t.lat, t.lon);
    if (p0.x < c.x1 - 20 || p0.x > c.x2 + 20 || p0.y < c.y1 - 20 || p0.y > c.y2 + 20) continue;
    // конус: напівпрозорі кола радіусом σ уздовж прогнозу
    cone.bg_color = tc;
    for (int i = 0; i < t.npt; i++) {
      lv_point_t q = mapPt(c, t.pla[i], t.plo[i]);
      int r = LV_CLAMP(2, (int)(t.psd[i] * KM_PX + 0.5f), 16);
      lv_area_t a = {(lv_coord_t)(q.x - r), (lv_coord_t)(q.y - r), (lv_coord_t)(q.x + r), (lv_coord_t)(q.y + r)};
      lv_draw_rect(dc, &cone, &a);
    }
    // шлях і стрілка на кінці
    ln.color = tc;
    lv_point_t prev = p0, last = p0, before = p0;
    for (int i = 0; i < t.npt; i++) {
      lv_point_t q = mapPt(c, t.pla[i], t.plo[i]);
      if (q.x == prev.x && q.y == prev.y) continue;
      lv_draw_line(dc, &ln, &prev, &q);
      before = prev; prev = last = q;
    }
    if (last.x != p0.x || last.y != p0.y) {
      float dx = last.x - before.x, dy = last.y - before.y, L = sqrtf(dx * dx + dy * dy);
      if (L > 0.5f) {
        dx /= L; dy /= L;
        lv_point_t a1 = {(lv_coord_t)(last.x - 4 * dx + 3 * dy), (lv_coord_t)(last.y - 4 * dy - 3 * dx)};
        lv_point_t a2 = {(lv_coord_t)(last.x - 4 * dx - 3 * dy), (lv_coord_t)(last.y - 4 * dy + 3 * dx)};
        lv_draw_line(dc, &ln, &last, &a1);
        lv_draw_line(dc, &ln, &last, &a2);
      }
    }
    // значок: дрони — трикутник за курсом, ракети/КАБ — ромб; чорна обвідка для контрасту
    lv_draw_rect_dsc_t ic; lv_draw_rect_dsc_init(&ic);
    ic.bg_opa = LV_OPA_COVER; ic.border_width = 0;
    bool missile = t.ch == 'C' || t.ch == 'K';
    float hr = (t.hd >= 0 ? t.hd : 0) * 0.0174533f, sx = sinf(hr), sy = -cosf(hr);
    for (int pass = 0; pass < 2; pass++) {
      float s = pass ? 4.0f : 5.5f;
      ic.bg_color = pass ? tc : lv_color_black();
      lv_point_t pts[4];
      int n;
      if (missile || t.hd < 0) {
        pts[0] = {(lv_coord_t)(p0.x + s * sx), (lv_coord_t)(p0.y + s * sy)};
        pts[1] = {(lv_coord_t)(p0.x - s * 0.7f * sy), (lv_coord_t)(p0.y + s * 0.7f * sx)};
        pts[2] = {(lv_coord_t)(p0.x - s * sx), (lv_coord_t)(p0.y - s * sy)};
        pts[3] = {(lv_coord_t)(p0.x + s * 0.7f * sy), (lv_coord_t)(p0.y - s * 0.7f * sx)};
        n = 4;
      } else {
        pts[0] = {(lv_coord_t)(p0.x + s * sx), (lv_coord_t)(p0.y + s * sy)};
        pts[1] = {(lv_coord_t)(p0.x - s * 0.8f * sx - s * 0.8f * sy), (lv_coord_t)(p0.y - s * 0.8f * sy + s * 0.8f * sx)};
        pts[2] = {(lv_coord_t)(p0.x - s * 0.8f * sx + s * 0.8f * sy), (lv_coord_t)(p0.y - s * 0.8f * sy - s * 0.8f * sx)};
        n = 3;
      }
      lv_draw_polygon(dc, &ic, pts, n);
    }
  }
}

static void markerAnimCb(void* o, int32_t v) {
  lv_obj_set_style_shadow_spread((lv_obj_t*)o, v, 0);
  lv_obj_set_style_shadow_opa((lv_obj_t*)o, 255 - v * 40, 0);
}

static void mainDeleted(lv_event_t*) { s_main = nullptr; }
static void settingsBtnCb(lv_event_t*) { showSettings(); }
static void showDetail();
static void cardCb(lv_event_t*) { showDetail(); }
static void showOutage();
static void outBoxCb(lv_event_t*) { showOutage(); }

static lv_obj_t* legendRow(lv_obj_t* p, int y, uint32_t c, const char* t, int x = 6) {
  lv_obj_t* dot = lv_obj_create(p);
  lv_obj_remove_style_all(dot);
  lv_obj_set_size(dot, 10, 10);
  lv_obj_set_style_radius(dot, 3, 0);
  lv_obj_set_style_bg_color(dot, col(c), 0);
  lv_obj_set_style_bg_opa(dot, LV_OPA_COVER, 0);
  lv_obj_set_pos(dot, x, y + 3);
  lv_obj_t* l = mkLabel(p, t, &font_ua_12, C_MUTED);
  lv_obj_set_pos(l, x + 14, y);
  return l;
}

static void updateMain(bool full);

static void showMain() {
  lv_obj_t* scr = mkScreen(SK_MAIN);
  s_main = scr;
  lv_obj_add_event_cb(scr, mainDeleted, LV_EVENT_DELETE, nullptr);

  // --- верхня панель ---
  s_lblPlace = mkLabel(scr, userPlaceName().c_str(), &font_ua_14, C_TEXT);
  lv_obj_set_width(s_lblPlace, 196);
  lv_label_set_long_mode(s_lblPlace, LV_LABEL_LONG_DOT);
  lv_obj_set_pos(s_lblPlace, 8, 5);
  s_lblWifi = mkLabel(scr, LV_SYMBOL_WIFI, &font_ua_14, C_MUTED);
  lv_obj_set_pos(s_lblWifi, 210, 5);
  s_lblTime = mkLabel(scr, "--:--", &font_ua_18, C_TEXT);
  lv_obj_set_pos(s_lblTime, 232, 2);
  lv_obj_t* gear = lv_btn_create(scr);
  lv_obj_set_size(gear, 30, 24);
  lv_obj_set_pos(gear, 287, 1);
  lv_obj_set_style_bg_color(gear, col(C_PANEL2), 0);
  lv_obj_set_style_shadow_width(gear, 0, 0);
  lv_obj_set_style_radius(gear, 6, 0);
  lv_obj_set_ext_click_area(gear, 8);
  lv_obj_center(mkLabel(gear, LV_SYMBOL_SETTINGS, &font_ua_14, C_TEXT));
  lv_obj_add_event_cb(gear, settingsBtnCb, LV_EVENT_CLICKED, nullptr);

  // --- мапа ---
  s_map = lv_obj_create(scr);
  lv_obj_remove_style_all(s_map);
  lv_obj_set_size(s_map, MAP_W, MAP_H);
  lv_obj_set_pos(s_map, 0, 27);
  lv_obj_add_event_cb(s_map, mapDrawCb, LV_EVENT_DRAW_MAIN, nullptr);
  lv_obj_clear_flag(s_map, LV_OBJ_FLAG_CLICKABLE);

  // маркер користувача
  float lat, lon; bool have = false;
  const CityDef* c = userCity();
  int mx = 0, my = 0;
  if (c) { lat = c->lat; lon = c->lon; have = true; }
  if (have) {
    mx = (int)((lon * MAP_PROJ_COSLAT - MAP_PROJ_MINX) * MAP_PROJ_SCALE + MAP_PROJ_OFFX);
    my = (int)((-lat - MAP_PROJ_MINY) * MAP_PROJ_SCALE + MAP_PROJ_OFFY);
  } else if (g_set.oblast >= 0) {
    mx = MAP_CENTROID[g_set.oblast][0]; my = MAP_CENTROID[g_set.oblast][1];
  }
  s_marker = lv_obj_create(s_map);
  lv_obj_remove_style_all(s_marker);
  lv_obj_set_size(s_marker, 9, 9);
  lv_obj_set_style_radius(s_marker, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_color(s_marker, col(0xFFFFFF), 0);
  lv_obj_set_style_bg_opa(s_marker, LV_OPA_COVER, 0);
  lv_obj_set_style_border_color(s_marker, col(C_ACCENT), 0);
  lv_obj_set_style_border_width(s_marker, 2, 0);
  lv_obj_set_style_shadow_color(s_marker, col(0xFFFFFF), 0);
  lv_obj_set_style_shadow_width(s_marker, 4, 0);
  lv_obj_set_pos(s_marker, mx - 4, my - 4);
  lv_anim_t a; lv_anim_init(&a);
  lv_anim_set_var(&a, s_marker);
  lv_anim_set_exec_cb(&a, markerAnimCb);
  lv_anim_set_values(&a, 0, 6);
  lv_anim_set_time(&a, 900);
  lv_anim_set_playback_time(&a, 900);
  lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
  lv_anim_start(&a);

  // --- права панель ---
  lv_obj_t* rp = lv_obj_create(scr);
  lv_obj_remove_style_all(rp);
  lv_obj_set_size(rp, 76, 160);
  lv_obj_set_pos(rp, 242, 28);
  lv_obj_set_style_bg_color(rp, col(C_PANEL), 0);
  lv_obj_set_style_bg_opa(rp, LV_OPA_COVER, 0);
  lv_obj_set_style_radius(rp, 10, 0);
  lv_obj_clear_flag(rp, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_t* l1 = mkLabel(rp, "ТРИВОГА", &font_ua_12, C_MUTED);
  lv_obj_align(l1, LV_ALIGN_TOP_MID, 0, 3);
  s_lblCount = mkLabel(rp, "–", &font_ua_40, C_RED);
  lv_obj_align(s_lblCount, LV_ALIGN_TOP_MID, 0, 12);
  s_lblCountSub = mkLabel(rp, "областей", &font_ua_12, C_MUTED);
  lv_obj_align(s_lblCountSub, LV_ALIGN_TOP_MID, 0, 55);
  legendRow(rp, 71, C_MAP_R, "ракети");
  legendRow(rp, 84, C_MAP_Y, "дрони");
  legendRow(rp, 97, C_MAP_YP, "частково");
  // --- графік відключень (компактно) ---
  s_outBox = lv_obj_create(rp);
  lv_obj_remove_style_all(s_outBox);
  lv_obj_set_size(s_outBox, 76, 48);
  lv_obj_set_pos(s_outBox, 0, 112);
  lv_obj_set_style_border_side(s_outBox, LV_BORDER_SIDE_TOP, 0);
  lv_obj_set_style_border_width(s_outBox, 1, 0);
  lv_obj_set_style_border_color(s_outBox, col(C_BORDER), 0);
  lv_obj_set_style_bg_color(s_outBox, col(C_PANEL2), LV_STATE_PRESSED);
  lv_obj_set_style_bg_opa(s_outBox, LV_OPA_COVER, LV_STATE_PRESSED);
  lv_obj_add_flag(s_outBox, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_clear_flag(s_outBox, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_set_ext_click_area(s_outBox, 4);
  lv_obj_add_event_cb(s_outBox, outBoxCb, LV_EVENT_CLICKED, nullptr);
  s_outState = mkLabel(s_outBox, LV_SYMBOL_CHARGE " Світло", &font_ua_12, C_MUTED);
  lv_obj_set_width(s_outState, 72);
  lv_label_set_long_mode(s_outState, LV_LABEL_LONG_DOT);
  lv_obj_set_style_text_align(s_outState, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_align(s_outState, LV_ALIGN_TOP_MID, 0, 3);
  s_outTime = mkLabel(s_outBox, "—", &font_ua_14, C_TEXT);
  lv_obj_align(s_outTime, LV_ALIGN_TOP_MID, 0, 17);
  s_outBar = lv_obj_create(s_outBox);   // наступні 8 годин (16 півгодин)
  lv_obj_remove_style_all(s_outBar);
  lv_obj_set_size(s_outBar, 64, 6);
  lv_obj_align(s_outBar, LV_ALIGN_TOP_MID, 0, 37);
  lv_obj_clear_flag(s_outBar, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_add_event_cb(s_outBar, [](lv_event_t* e) {
    lv_obj_t* o = lv_event_get_target(e);
    lv_draw_ctx_t* dc = lv_event_get_draw_ctx(e);
    lv_area_t c; lv_obj_get_coords(o, &c);
    time_t now = time(nullptr);
    if (now < 100000) return;
    lv_draw_rect_dsc_t d; lv_draw_rect_dsc_init(&d);
    d.bg_opa = LV_OPA_COVER; d.radius = 1;
    for (int k = 0; k < 16; k++) {
      time_t t = now + k * 1800;
      const OutageDay* day = outageDay(s_out, outageYmd(t));
      uint8_t v = OUT_NONE;
      if (day) { struct tm tm; localtime_r(&t, &tm); v = day->c[(tm.tm_hour * 60 + tm.tm_min) / 30]; }
      d.bg_color = col(v == OUT_OFF ? C_OUT_OFF : v == OUT_MAYBE ? C_OUT_MB : v == OUT_ON ? C_OUT_ON : C_PANEL2);
      if (v == OUT_ON) d.bg_color = lv_color_mix(col(C_OUT_ON), col(C_PANEL), 150);
      lv_area_t a = {(lv_coord_t)(c.x1 + k * 4), c.y1, (lv_coord_t)(c.x1 + k * 4 + 2), c.y2};
      lv_draw_rect(dc, &d, &a);
    }
  }, LV_EVENT_DRAW_MAIN, nullptr);

  // джерело даних — у кутку мапи
  s_lblSrc = mkLabel(s_map, "", &font_ua_12, 0x5C6773);
  lv_obj_set_pos(s_lblSrc, 2, MAP_H - 15);

  // --- нижня картка статусу ---
  s_card = lv_obj_create(scr);
  lv_obj_remove_style_all(s_card);
  lv_obj_set_size(s_card, 312, 46);
  lv_obj_set_pos(s_card, 4, 191);
  lv_obj_set_style_radius(s_card, 10, 0);
  lv_obj_set_style_bg_opa(s_card, LV_OPA_COVER, 0);
  lv_obj_set_style_clip_corner(s_card, true, 0);
  lv_obj_add_flag(s_card, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_clear_flag(s_card, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_add_event_cb(s_card, cardCb, LV_EVENT_CLICKED, nullptr);
  s_cardBar = lv_obj_create(s_card);
  lv_obj_remove_style_all(s_cardBar);
  lv_obj_set_size(s_cardBar, 6, 46);
  lv_obj_set_style_bg_opa(s_cardBar, LV_OPA_COVER, 0);
  s_cardTitle = mkLabel(s_card, "", &font_ua_18, C_TEXT);
  lv_obj_set_pos(s_cardTitle, 14, 3);
  s_cardText = mkLabel(s_card, "", &font_ua_12, C_MUTED);
  lv_obj_set_width(s_cardText, 290);
  lv_label_set_long_mode(s_cardText, LV_LABEL_LONG_SCROLL_CIRCULAR);
  lv_obj_set_pos(s_cardText, 14, 26);

  s_seenVersion = UINT32_MAX;
  updateMain(true);
  loadScreen(scr);
}

static void updateMain(bool full) {
  if (!s_main) return;
  struct tm tm;
  if (getLocalTime(&tm, 0)) lv_label_set_text_fmt(s_lblTime, "%02d:%02d", tm.tm_hour, tm.tm_min);
  bool wifi = WiFi.status() == WL_CONNECTED;
  lv_obj_set_style_text_color(s_lblWifi, col(wifi ? C_GREEN : C_RED), 0);

  static AppState snap;
  stateLock();
  snap = g_state;
  stateUnlock();
  uint32_t ver = snap.version;
  bool changed = full || ver != s_seenVersion;

  if (changed) {
    s_seenVersion = ver;
    memcpy(s_ob, snap.oblasts, sizeof(s_ob));
    memcpy(s_rg, snap.regions, sizeof(s_rg));
    s_out = snap.outage;
    s_ntgt = snap.trkOk ? snap.ntgt : 0;
    memcpy(s_tgt, snap.tgt, sizeof(s_tgt));
    rebuildPalette();
    lv_obj_invalidate(s_map);
    int cnt = 0;
    if (snap.regionsFine) {
      for (int i = 0; i < MAP_REGION_COUNT; i++) if (s_rg[i] == 'R' || s_rg[i] == 'Y') cnt++;
      lv_label_set_text(s_lblCountSub, "районів");
    } else {
      for (int i = 0; i < OBLAST_COUNT; i++) if (s_ob[i] == 'A' || s_ob[i] == 'R') cnt++;
      lv_label_set_text(s_lblCountSub, "областей");
    }
    if (snap.alertsOk) lv_label_set_text_fmt(s_lblCount, "%d", cnt);
    else lv_label_set_text(s_lblCount, "?");

    Level L = snap.level;
    static const uint32_t cardBg[3] = {0x10261A, 0x332A08, 0x3A0F12};
    lv_obj_set_style_bg_color(s_card, col(cardBg[L]), 0);
    lv_obj_set_style_bg_color(s_cardBar, col(levelColor(L)), 0);
    const char* sub;
    bool off = snap.offLevel > LVL_GREEN;
    bool tgDrives = snap.tgLevel >= L && snap.tgLevel > LVL_GREEN && snap.tgScore >= 3;
    if (L == LVL_RED && snap.trkOk && snap.trkLevel == LVL_RED && !off && !tgDrives) sub = snap.pBallistic >= 15 ? "балістика — в укриття!" : "ракета може пролетіти поруч";
    else if (L == LVL_RED) sub = snap.tgNear && tgDrives ? "ракетна загроза поруч" : off ? (snap.offLevel == LVL_RED ? "ракетна небезпека в районі" : "загроза ракет / КАБ") : "ракети — будьте в укритті";
    else if (L == LVL_YELLOW && snap.trkOk && snap.trkLevel == LVL_YELLOW && !off && !tgDrives) sub = "дрон може пролетіти поруч";
    else if (L == LVL_YELLOW) sub = snap.tgNear && tgDrives ? "дрони поруч з вами" : off ? (snap.offPartial ? "тривога в частині району" : (userRegion() >= 0 ? "тривога у вашому районі" : "тривога в області")) : "активність дронів поруч";
    else {
      char os = userOblastStatus();
      sub = !snap.alertsOk ? "немає даних" : (os == 'A' || os == 'P' || os == 'R' || os == 'Y') ? "тривога в області, не у вас" : "тривоги немає";
    }
    lv_label_set_text_fmt(s_cardTitle, "%s  ·  %s", levelName(L), sub);
    lv_obj_set_style_text_color(s_cardTitle, col(levelColor(L)), 0);
    String line;
    if (snap.tgText[0]) {
      line = fmtTime(snap.tgTime) + "  " + snap.tgText;
    } else if (snap.tgOk) {
      line = String("Telegram: ") + (snap.tgReason[0] ? snap.tgReason : "немає загроз");
    } else {
      int n = channelsEnabledCount() + (g_set.channel.length() ? 1 : 0);
      line = n ? ("Читаю Telegram-канали (" + String(n) + ")…") : String("Telegram-канали вимкнено (Налаштування)");
    }
    if (snap.trkOk && (snap.ntgt || snap.pNear || snap.pBallistic)) {
      char pre[64];
      int pn = snap.pBallistic > snap.pNear ? snap.pBallistic : snap.pNear;
      if (snap.eta >= 0) snprintf(pre, sizeof(pre), "P(близько) %d%% · ETA %d хв  ·  ", pn, snap.eta);
      else snprintf(pre, sizeof(pre), "P(близько) %d%% · цілей %d  ·  ", pn, snap.ntgt);
      line = String(pre) + line;
    }
    lv_label_set_text(s_cardText, line.c_str());
  }
  // графік відключень: щохвилини (зміна півгодинки) або при оновленні даних
  static uint32_t lastOutUi = 0;
  if (changed || millis() - lastOutUi > 30000) {
    lastOutUi = millis();
    time_t now = time(nullptr), at = 0;
    uint8_t nv = OUT_NONE;
    uint8_t cur = g_set.outMode == OUT_MODE_OFF ? OUT_NONE : outageNow(s_out, now, &at, &nv);
    if (g_set.outMode == OUT_MODE_OFF) {
      lv_label_set_text(s_outState, LV_SYMBOL_CHARGE " Світло");
      lv_label_set_text(s_outTime, "вимк.");
      lv_obj_set_style_text_color(s_outState, col(C_MUTED), 0);
    } else if (cur == OUT_NONE) {
      lv_label_set_text(s_outState, LV_SYMBOL_CHARGE " Графік");
      lv_obj_set_style_text_color(s_outState, col(C_MUTED), 0);
      lv_label_set_text(s_outTime, g_set.outGroup < 0 && g_set.outMode == OUT_MODE_AUTO ? "черга?" : (s_out.at ? "немає" : "…"));
    } else {
      static const char* st[] = {LV_SYMBOL_CHARGE " Світло є", LV_SYMBOL_CHARGE " Вимкнено", LV_SYMBOL_CHARGE " Можливо"};
      static const uint32_t sc[] = {C_OUT_ON, 0xB8C2CC, C_OUT_MB};
      lv_label_set_text(s_outState, st[cur]);
      lv_obj_set_style_text_color(s_outState, col(sc[cur]), 0);
      if (at) lv_label_set_text_fmt(s_outTime, "до %s", fmtTime(at).c_str());
      else lv_label_set_text(s_outTime, cur == OUT_ON ? "без відкл." : "—");
    }
    lv_obj_invalidate(s_outBar);
  }
  // вік даних
  uint32_t age = snap.alertsAt ? (millis() - snap.alertsAt) / 1000 : 0;
  lv_label_set_text_fmt(s_lblSrc, "%s · %lus", snap.alertsSrc, (unsigned long)age);
}

// ------------------------------- Деталі -------------------------------
static void backMainCb(lv_event_t*) { showMain(); }
static void refreshCb(lv_event_t*) { netForceRefresh(); toast("Оновлюю…"); }

static void showDetail() {
  lv_obj_t* scr = mkScreen(SK_DETAIL);
  lv_obj_t* h = mkHeader(scr, "Деталі загрози", backMainCb);
  mkHeaderBtn(h, LV_SYMBOL_REFRESH, refreshCb);
  lv_obj_t* cont = lv_obj_create(scr);
  lv_obj_set_size(cont, 320, 204);
  lv_obj_set_pos(cont, 0, 36);
  lv_obj_set_style_bg_color(cont, col(C_BG), 0);
  lv_obj_set_style_border_width(cont, 0, 0);
  lv_obj_set_style_radius(cont, 0, 0);
  lv_obj_set_flex_flow(cont, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_style_pad_row(cont, 6, 0);
  stateLock(); AppState s = g_state; stateUnlock();
  char os = userOblastStatus();
  const char* osTxt = os == 'A' || os == 'R' ? "тривога" : os == 'Y' ? "попередня загроза" : os == 'P' ? "часткова тривога" : os == 'N' ? "тривоги немає" : "невідомо";
  int me = userRegion();
  const char* offTxt = s.offLevel == LVL_RED ? "ракети / КАБ" : s.offLevel == LVL_YELLOW ? (s.regionsFine ? "дрони / тип невідомий" : "тривога (тип невідомий)") : "тривоги немає";
  static const char* lvlTxt[3] = {"Чисто", "Дрони (БпЛА)", "Ракети / КАБ"};
  String t1 = String(s.level == LVL_GREEN ? LV_SYMBOL_OK " " : LV_SYMBOL_WARNING " ") + lvlTxt[s.level];
  lv_obj_t* l = mkLabel(cont, t1.c_str(), &font_ua_26, levelColor(s.level));
  String t2 = userPlaceName();
  if (me >= 0) t2 += String("\n") + MAP_REGIONS[me].name + ": " + offTxt + (s.offPartial ? " (частково)" : "");
  t2 += String("\nОбласть: ") + osTxt + "  (" + s.alertsSrc + (s.regionsFine ? ", по районах" : ", по областях") + ")";
  l = mkLabel(cont, t2.c_str(), &font_ua_14, C_TEXT);
  lv_obj_set_width(l, 300); lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
  String ch = s.tgChan[0] ? String(s.tgChan) : String("—");
  String t3 = String("Telegram ") + s.tgNok + "/" + s.tgNch + " каналів, головне @" + ch + " (" + s.tgSrc +
              (s.tgAgree >= 2 ? String(", підтверджують ") + s.tgAgree : String()) + "): " + levelName(s.tgLevel) +
              (s.tgKind[0] ? String(" / ") + s.tgKind : String()) + ", бал " + s.tgScore + (s.tgNear ? ", поруч" : "") +
              (s.tgReason[0] ? String("\nПричини: ") + s.tgReason : String());
  l = mkLabel(cont, t3.c_str(), &font_ua_12, C_MUTED);
  lv_obj_set_width(l, 300); lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
  if (s.tgText[0]) {
    lv_obj_t* box = lv_obj_create(cont);
    lv_obj_set_width(box, 300); lv_obj_set_height(box, LV_SIZE_CONTENT);
    lv_obj_set_style_bg_color(box, col(C_PANEL), 0);
    lv_obj_set_style_border_width(box, 0, 0);
    lv_obj_set_style_radius(box, 10, 0);
    String t4 = fmtTime(s.tgTime) + "  " + s.tgText;
    l = mkLabel(box, t4.c_str(), &font_ua_14, C_TEXT);
    lv_obj_set_width(l, 276); lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
  }
  l = mkLabel(cont, "Аналіз автоматичний (ключові слова) і може помилятися. Завжди дійте за офіційними сигналами тривоги.", &font_ua_12, 0x5C6773);
  lv_obj_set_width(l, 300); lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
  loadScreen(scr);
}

// ======================================================================================
//                                Повноекранне сповіщення
// ======================================================================================
static Level s_lastLevel = LVL_GREEN;
static time_t s_lastAlertTs = 0;
static bool s_testMode = false;

static void alertOkCb(lv_event_t*) { notifySilence(); s_testMode = false; showMain(); }
static void alertBgAnim(void* o, int32_t v) {
  lv_obj_set_style_bg_color((lv_obj_t*)o, lv_color_mix(col(0xFF3B3F), col(0x7A0010), v), 0);
}

// ЧЕРВОНИЙ — ракети/КАБ (миготливий червоний фон), ЖОВТИЙ — дрони (бурштиновий фон)
static void showAlert(Level L, const char* text, time_t ts, const char* reason, bool near) {
  lv_obj_t* scr = mkScreen(SK_ALERT);
  bool red = L == LVL_RED;
  uint32_t fg = red ? 0xFFFFFF : 0x1A1400;
  lv_obj_set_style_bg_color(scr, col(red ? 0xC0151E : C_YELLOW), 0);
  if (red) {
    lv_anim_t a; lv_anim_init(&a);
    lv_anim_set_var(&a, scr); lv_anim_set_exec_cb(&a, alertBgAnim);
    lv_anim_set_values(&a, 0, 255); lv_anim_set_time(&a, 550); lv_anim_set_playback_time(&a, 550);
    lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE); lv_anim_start(&a);
  }
  lv_obj_t* ic = mkLabel(scr, LV_SYMBOL_WARNING, &font_ua_40, fg);
  lv_obj_set_pos(ic, 12, 10);
  lv_obj_t* t = mkLabel(scr, red ? "РАКЕТНА НЕБЕЗПЕКА" : "ДРОНИ / БпЛА", &font_ua_26, fg);
  lv_obj_set_width(t, 262); lv_label_set_long_mode(t, LV_LABEL_LONG_DOT);
  lv_obj_set_pos(t, 52, 8);
  String sub = String(red ? (near ? "Ракети/КАБ поруч: " : "Ракети / КАБ: ") : (near ? "Шахеди поруч: " : "Шахеди / БпЛА: ")) + userPlaceName();
  lv_obj_t* s = mkLabel(scr, sub.c_str(), &font_ua_14, fg);
  lv_obj_set_width(s, 260); lv_label_set_long_mode(s, LV_LABEL_LONG_DOT);
  lv_obj_set_pos(s, 52, 40);

  lv_obj_t* box = lv_obj_create(scr);
  lv_obj_set_size(box, 304, 120);
  lv_obj_set_pos(box, 8, 66);
  lv_obj_set_style_bg_color(box, col(0x000000), 0);
  lv_obj_set_style_bg_opa(box, red ? LV_OPA_40 : LV_OPA_10, 0);
  lv_obj_set_style_border_width(box, 0, 0);
  lv_obj_set_style_radius(box, 12, 0);
  lv_obj_set_style_pad_all(box, 8, 0);
  String body;
  if (reason && reason[0]) body = String("• ") + reason + "\n";
  if (text && text[0]) body += fmtTime(ts) + "  " + text;
  else body += red ? "Ракетна небезпека! Негайно прямуйте в укриття."
                   : (near ? "Дрони поруч з вами. Пройдіть в укриття, тримайтеся далі від вікон."
                           : "Повітряна тривога (дрони або тип не уточнено). Слідкуйте за оновленнями.");
  lv_obj_t* bl = mkLabel(box, body.c_str(), &font_ua_14, fg);
  lv_obj_set_width(bl, 284); lv_label_set_long_mode(bl, LV_LABEL_LONG_WRAP);

  lv_obj_t* b = lv_btn_create(scr);
  lv_obj_set_size(b, 220, 40);
  lv_obj_align(b, LV_ALIGN_BOTTOM_MID, 0, -8);
  lv_obj_set_style_bg_color(b, col(red ? 0xFFFFFF : 0x1A1400), 0);
  lv_obj_set_style_radius(b, 20, 0);
  lv_obj_add_event_cb(b, alertOkCb, LV_EVENT_CLICKED, nullptr);
  lv_obj_center(mkLabel(b, red || near ? "ЗРОЗУМІВ, В УКРИТТЯ" : "ЗРОЗУМІВ", &font_ua_14, red ? 0xC0151E : C_YELLOW));
  lv_scr_load_anim(scr, LV_SCR_LOAD_ANIM_NONE, 0, 0, true);
}

// ======================================================================================
//                                      Налаштування
// ======================================================================================
typedef void (*TextSaveFn)(const String&);
static TextSaveFn s_textSave = nullptr;
static lv_obj_t* s_editTa = nullptr;

static void editReadyCb(lv_event_t*) {
  String v = lv_textarea_get_text(s_editTa); v.trim();
  if (s_textSave) s_textSave(v);
  settingsSave();
  netForceRefresh();
  showSettings();
  toast("Збережено");
}
static void toSettingsCb(lv_event_t*) { showSettings(); }

static void showTextEdit(const char* title, const String& val, const char* hint, TextSaveFn fn) {
  lv_obj_t* scr = mkScreen(SK_SETTINGS);
  mkHeader(scr, title, toSettingsCb);
  s_editTa = mkTextArea(scr, 42, hint);
  lv_textarea_set_text(s_editTa, val.c_str());
  lv_obj_t* kb = mkKeyboard(scr, s_editTa, false);
  lv_obj_set_height(kb, 140);
  lv_obj_add_event_cb(kb, editReadyCb, LV_EVENT_READY, nullptr);
  s_textSave = fn;
  loadScreen(scr);
}

static void setWifiCb(lv_event_t*) { showWifiScan(); }
static void setLocCb(lv_event_t*) { showOblastPicker(); }
static void setChanCb(lv_event_t*) {
  showTextEdit("Свій Telegram-канал (без @)", g_set.channel, "напр. kremen_sv (порожньо — немає)",
               [](const String& v) { String s = v; s.replace("@", ""); s.replace("https://t.me/s/", ""); s.replace("https://t.me/", ""); s.replace("t.me/", ""); s.trim(); g_set.channel = s; });
}
static void setGhTokCb(lv_event_t*) {
  showTextEdit("GitHub token (необовʼязково)", g_set.ghToken, "fine-grained, лише читання публічних репо",
               [](const String& v) { g_set.ghToken = v; });
}
static void showChannels();
static void setChanListCb(lv_event_t*) { showChannels(); }
static void chanToggleCb(lv_event_t* e) {
  int i = (int)(intptr_t)lv_event_get_user_data(e);
  bool on = lv_obj_has_state(lv_event_get_target(e), LV_STATE_CHECKED);
  channelSetEnabled(i, on);
  settingsSave();
  netForceRefresh();
}
static void chanDefaultsCb(lv_event_t*) {
  g_set.chCustom = false; g_set.chOn = 0;
  settingsSave(); netForceRefresh();
  showChannels(); toast("Типові канали для області");
}
// Список каналів: спершу ваші (місто/область), далі загальнонаціональні, далі інших областей
static void showChannels() {
  lv_obj_t* scr = mkScreen(SK_SETTINGS);
  mkHeader(scr, "Telegram-канали", toSettingsCb);
  lv_obj_t* list = mkList(scr, 36, 204);
  String own = "Свій канал: " + (g_set.channel.length() ? "@" + g_set.channel : String("—"));
  mkListBtn(list, LV_SYMBOL_EDIT, own.c_str(), setChanCb, nullptr);
  mkListBtn(list, LV_SYMBOL_REFRESH, g_set.chCustom ? "Повернути типові для області" : "Типові для області (зараз)",
            chanDefaultsCb, nullptr);
  const CityDef* city = userCity();
  for (int pass = 0; pass < 3; pass++) {
    for (int i = 0; i < CHANNEL_COUNT && i < 32; i++) {
      const ChannelDef& c = CHANNELS[i];
      bool mine = channelIsLocal(c.id, city) || (c.scope && g_set.oblast >= 0 && (c.obMask & (1u << g_set.oblast)));
      int grp = mine ? 0 : (c.scope == 0 ? 1 : 2);
      if (grp != pass) continue;
      Level L = LVL_GREEN;
      int st = netChannelStatus(i, &L);
      String t = String(c.name) + "\n@" + c.id + (grp == 1 ? "  · вся Україна" : grp == 0 ? "  · ваш регіон" : "") +
                 (st == 1 ? String("  · ") + levelName(L) : st == 2 ? String("  · помилка") : String());
      lv_obj_t* cb = lv_checkbox_create(list);
      lv_checkbox_set_text(cb, t.c_str());
      lv_obj_set_width(cb, LV_PCT(100));
      lv_obj_set_style_text_font(cb, &font_ua_12, 0);
      lv_obj_set_style_text_color(cb, col(grp == 2 ? C_MUTED : C_TEXT), 0);
      lv_obj_set_style_pad_ver(cb, 4, 0);
      if (channelEnabled(i)) lv_obj_add_state(cb, LV_STATE_CHECKED);
      lv_obj_add_event_cb(cb, chanToggleCb, LV_EVENT_VALUE_CHANGED, (void*)(intptr_t)i);
    }
  }
  loadScreen(scr);
}
static void setSrvCb(lv_event_t*) {
  showTextEdit("Companion-сервер", g_set.server, "https://bodya86.github.io/cyd-alert або http://IP:8080 (порожньо = вимк.)",
               [](const String& v) { g_set.server = v; });
}
static void setTokCb(lv_event_t*) {
  showTextEdit("Токен alerts.in.ua", g_set.token, "порожньо = ubilling.net.ua", [](const String& v) { g_set.token = v; });
}
static void testYCb(lv_event_t*) { s_testMode = true; notifyTest(LVL_YELLOW); showAlert(LVL_YELLOW, "Тестове повідомлення: шахеди курсом на Кременчук з боку Глобиного", time(nullptr), "Шахед/БпЛА, курс", true); }
static void testRCb(lv_event_t*) { s_testMode = true; notifyTest(LVL_RED); showAlert(LVL_RED, "Тестове повідомлення: ракета на місто, вибухи, всі в укриття!", time(nullptr), "Ракета, ваш район, вибухи/ППО", true); }
static void setOutCb(lv_event_t*);
static void resetCb(lv_event_t*) {
  static const char* btns[] = {"Скинути", "Скасувати", ""};
  lv_obj_t* mb = lv_msgbox_create(nullptr, "Скинути налаштування?", "Wi-Fi, локація і токени будуть видалені.", btns, false);
  lv_obj_center(mb);
  lv_obj_add_event_cb(mb, [](lv_event_t* e) {
    lv_obj_t* m = lv_event_get_current_target(e);
    if (lv_msgbox_get_active_btn(m) == 0) { settingsReset(); delay(200); ESP.restart(); }
    lv_msgbox_close(m);
  }, LV_EVENT_VALUE_CHANGED, nullptr);
}

static lv_obj_t* settingRow(lv_obj_t* list, const char* txt) {
  lv_obj_t* r = lv_obj_create(list);
  lv_obj_set_size(r, LV_PCT(100), 42);
  lv_obj_set_style_bg_color(r, col(C_PANEL), 0);
  lv_obj_set_style_border_width(r, 0, 0);
  lv_obj_set_style_radius(r, 8, 0);
  lv_obj_set_style_pad_hor(r, 10, 0);
  lv_obj_clear_flag(r, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_t* l = mkLabel(r, txt, &font_ua_14, C_TEXT);
  lv_obj_align(l, LV_ALIGN_LEFT_MID, 0, 0);
  return r;
}

static void showSettings() {
  lv_obj_t* scr = mkScreen(SK_SETTINGS);
  mkHeader(scr, "Налаштування", backMainCb);
  lv_obj_t* list = mkList(scr, 36, 204);
  String w = "Wi-Fi: " + (g_set.ssid.length() ? g_set.ssid : String("—"));
  mkListBtn(list, LV_SYMBOL_WIFI, w.c_str(), setWifiCb, nullptr);
  String loc = "Локація: " + userPlaceName();
  mkListBtn(list, LV_SYMBOL_GPS, loc.c_str(), setLocCb, nullptr);
  String ch = "Telegram: " + String(channelsEnabledCount() + (g_set.channel.length() ? 1 : 0)) + " каналів" +
              (g_set.chCustom ? "" : " (типові)");
  mkListBtn(list, LV_SYMBOL_BELL, ch.c_str(), setChanListCb, nullptr);
  String sv = "Сервер: " + (g_set.server.length() ? g_set.server : String("вимкнено"));
  mkListBtn(list, LV_SYMBOL_UPLOAD, sv.c_str(), setSrvCb, nullptr);
  String gt = String("GitHub token: ") + (g_set.ghToken.length() ? "задано (опит. 20 с)" : "немає (опит. 75 с)");
  mkListBtn(list, LV_SYMBOL_DOWNLOAD, gt.c_str(), setGhTokCb, nullptr);
  String tk = String("alerts.in.ua: ") + (g_set.token.length() ? "токен задано" : "без токена (ubilling)");
  mkListBtn(list, LV_SYMBOL_KEYBOARD, tk.c_str(), setTokCb, nullptr);
  static const char* om[] = {"вимкнено", "авто", "вручну"};
  String ou = String("Світло: ") + (g_set.outGroup >= 0 ? String("черга ") + OUTAGE_GROUPS[g_set.outGroup] : String("черга не обрана")) + " · " + om[g_set.outMode];
  mkListBtn(list, LV_SYMBOL_CHARGE, ou.c_str(), setOutCb, nullptr);

  lv_obj_t* r = settingRow(list, "Яскравість");
  lv_obj_t* sl = lv_slider_create(r);
  lv_obj_set_width(sl, 150);
  lv_obj_align(sl, LV_ALIGN_RIGHT_MID, -6, 0);
  lv_slider_set_range(sl, 10, 255);
  lv_slider_set_value(sl, g_set.brightness, LV_ANIM_OFF);
  lv_obj_add_event_cb(sl, [](lv_event_t* e) {
    g_set.brightness = lv_slider_get_value(lv_event_get_target(e)); hwSetBrightness(g_set.brightness);
  }, LV_EVENT_VALUE_CHANGED, nullptr);
  lv_obj_add_event_cb(sl, [](lv_event_t*) { settingsSave(); }, LV_EVENT_RELEASED, nullptr);

  r = settingRow(list, "Звук сирени");
  lv_obj_t* sw = lv_switch_create(r);
  lv_obj_align(sw, LV_ALIGN_RIGHT_MID, 0, 0);
  if (g_set.sound) lv_obj_add_state(sw, LV_STATE_CHECKED);
  lv_obj_add_event_cb(sw, [](lv_event_t* e) {
    g_set.sound = lv_obj_has_state(lv_event_get_target(e), LV_STATE_CHECKED); settingsSave();
  }, LV_EVENT_VALUE_CHANGED, nullptr);

  r = settingRow(list, "Екран на жовтому рівні");
  sw = lv_switch_create(r);
  lv_obj_align(sw, LV_ALIGN_RIGHT_MID, 0, 0);
  if (g_set.alertOnYellow) lv_obj_add_state(sw, LV_STATE_CHECKED);
  lv_obj_add_event_cb(sw, [](lv_event_t* e) {
    g_set.alertOnYellow = lv_obj_has_state(lv_event_get_target(e), LV_STATE_CHECKED); settingsSave();
  }, LV_EVENT_VALUE_CHANGED, nullptr);

  mkListBtn(list, LV_SYMBOL_PLAY, "Тест: дрони (жовтий)", testYCb, nullptr);
  mkListBtn(list, LV_SYMBOL_PLAY, "Тест: ракети (червоний)", testRCb, nullptr);
  mkListBtn(list, LV_SYMBOL_TRASH, "Скинути всі налаштування", resetCb, nullptr);
  String info = "IP " + WiFi.localIP().toString() + " · RAM " + String(ESP.getFreeHeap() / 1024) + " КБ · v1.1";
  lv_list_add_text(list, info.c_str());
  loadScreen(scr);
}

// ======================================================================================
//                              Графік відключень світла
// ======================================================================================
static lv_obj_t *s_dayBar[2], *s_outSum, *s_outSrc;
static uint32_t s_outSeen = UINT32_MAX;
static void outSettingsCb(lv_event_t*);
static void showOutSettings();

static time_t dayStart(int dayIdx) {   // локальна північ сьогодні/завтра
  time_t now = time(nullptr);
  struct tm tm; localtime_r(&now, &tm);
  tm.tm_hour = 0; tm.tm_min = 0; tm.tm_sec = 0; tm.tm_mday += dayIdx; tm.tm_isdst = -1;
  return mktime(&tm);
}

static void dayBarDrawCb(lv_event_t* e) {
  lv_obj_t* o = lv_event_get_target(e);
  int idx = (int)(intptr_t)lv_event_get_user_data(e);
  lv_draw_ctx_t* dc = lv_event_get_draw_ctx(e);
  lv_area_t c; lv_obj_get_coords(o, &c);
  time_t now = time(nullptr);
  const OutageDay* day = now > 100000 ? outageDay(s_out, outageYmd(dayStart(idx) + 3600)) : nullptr;
  lv_draw_rect_dsc_t d; lv_draw_rect_dsc_init(&d);
  d.bg_opa = LV_OPA_COVER; d.radius = 0;
  int w = lv_area_get_width(&c), barH = 18;
  for (int k = 0; k < 48; k++) {
    uint8_t v = day ? day->c[k] : OUT_NONE;
    d.bg_color = col(v == OUT_OFF ? C_OUT_OFF : v == OUT_MAYBE ? C_OUT_MB : v == OUT_ON ? C_OUT_ON : C_PANEL2);
    lv_area_t a = {(lv_coord_t)(c.x1 + k * w / 48), c.y1, (lv_coord_t)(c.x1 + (k + 1) * w / 48 - (k % 2 ? 1 : 0)), (lv_coord_t)(c.y1 + barH - 1)};
    lv_draw_rect(dc, &d, &a);
  }
  // мітки годин
  lv_draw_label_dsc_t ld; lv_draw_label_dsc_init(&ld);
  ld.font = &font_ua_12; ld.color = col(C_MUTED);
  for (int h = 0; h < 24; h += 3) {
    int x = c.x1 + h * w / 24;
    lv_draw_rect_dsc_t t; lv_draw_rect_dsc_init(&t); t.bg_opa = LV_OPA_COVER; t.bg_color = col(C_BG);
    lv_area_t tk = {(lv_coord_t)x, c.y1, (lv_coord_t)x, (lv_coord_t)(c.y1 + barH + 2)};
    if (h) lv_draw_rect(dc, &t, &tk);
    char b[4]; snprintf(b, sizeof(b), "%02d", h);
    lv_area_t la = {(lv_coord_t)(x + 1), (lv_coord_t)(c.y1 + barH + 1), (lv_coord_t)(x + 24), c.y2};
    lv_draw_label(dc, &ld, &la, b, nullptr);
  }
  // поточний час
  if (idx == 0 && now > 100000) {
    struct tm tm; localtime_r(&now, &tm);
    int x = c.x1 + (tm.tm_hour * 60 + tm.tm_min) * w / 1440;
    lv_draw_rect_dsc_t m; lv_draw_rect_dsc_init(&m); m.bg_opa = LV_OPA_COVER; m.bg_color = col(0xFFFFFF); m.radius = 1;
    lv_area_t ma = {(lv_coord_t)(x - 1), (lv_coord_t)(c.y1 - 3), (lv_coord_t)(x + 1), (lv_coord_t)(c.y1 + barH + 2)};
    lv_draw_rect(dc, &m, &ma);
  }
}

// ручний режим: тап по клітинці «сьогодні» перемикає півгодинку (графік повторюється щодня)
static void dayBarClickCb(lv_event_t* e) {
  if (g_set.outMode != OUT_MODE_MANUAL) { showOutSettings(); return; }
  lv_obj_t* o = lv_event_get_target(e);
  lv_point_t p; lv_indev_get_point(lv_indev_get_act(), &p);
  lv_area_t c; lv_obj_get_coords(o, &c);
  int k = (p.x - c.x1) * 48 / lv_area_get_width(&c);
  if (k < 0 || k > 47) return;
  g_set.outMask ^= (1ULL << k);
  settingsSave();
  netOutageRefresh();
  stateLock(); outageFromMask(g_state.outage, g_set.outMask, time(nullptr)); s_out = g_state.outage; stateUnlock();
  lv_obj_invalidate(s_dayBar[0]); lv_obj_invalidate(s_dayBar[1]);
}

static void outageFill() {
  time_t now = time(nullptr), at = 0;
  uint8_t nv = OUT_NONE;
  uint8_t cur = outageNow(s_out, now, &at, &nv);
  String t;
  if (g_set.outMode == OUT_MODE_OFF) t = "Графік вимкнено. Увімкніть у налаштуваннях " LV_SYMBOL_SETTINGS;
  else if (g_set.outMode == OUT_MODE_AUTO && g_set.outGroup < 0) t = "Оберіть свою чергу " LV_SYMBOL_SETTINGS;
  else if (cur == OUT_NONE) t = s_out.err[0] ? String("Немає даних: ") + s_out.err : String("Графік ще не оприлюднено");
  else {
    static const char* nm[] = {"Світло є", "Світла немає", "Можливе відключення"};
    t = nm[cur];
    if (at) t += String(" до ") + fmtTime(at) + (nv == OUT_OFF ? " (далі відключення)" : nv == OUT_ON ? " (далі буде світло)" : nv == OUT_MAYBE ? " (далі можливе)" : "");
    else t += " до кінця графіка";
  }
  for (int i = 0; i < 2; i++) {
    const OutageDay* d = now > 100000 ? outageDay(s_out, outageYmd(dayStart(i) + 3600)) : nullptr;
    int hh = d ? outageOffHalfHours(*d) : -1;
    t += i ? " · завтра: " : "\n" LV_SYMBOL_CHARGE " без світла сьогодні: ";
    t += hh < 0 ? String("—") : String(hh / 2) + (hh % 2 ? ",5" : "") + " год";
  }
  lv_label_set_text(s_outSum, t.c_str());
  String src = s_out.manual ? String("Ручний графік (тапніть по шкалі «сьогодні»)") : String("Джерело: ") + (s_out.src[0] ? s_out.src : "—");
  if (!s_out.manual && s_out.updated[0]) src += String(" · ") + s_out.updated;
  lv_label_set_text(s_outSrc, src.c_str());
  lv_obj_invalidate(s_dayBar[0]); lv_obj_invalidate(s_dayBar[1]);
}

static void outageTick() {
  stateLock();
  uint32_t v = g_state.version;
  bool ch = v != s_outSeen;
  if (ch) { s_out = g_state.outage; s_outSeen = v; }
  stateUnlock();
  static uint32_t last = 0;
  if (ch || millis() - last > 30000) { last = millis(); outageFill(); }
}

static void showOutage() {
  lv_obj_t* scr = mkScreen(SK_OUTAGE);
  String title = g_set.outGroup >= 0 ? String("Відключення · черга ") + OUTAGE_GROUPS[g_set.outGroup] : String("Відключення світла");
  lv_obj_t* h = mkHeader(scr, title.c_str(), backMainCb);
  mkHeaderBtn(h, LV_SYMBOL_SETTINGS, outSettingsCb);
  stateLock(); s_out = g_state.outage; s_outSeen = g_state.version; stateUnlock();
  static const char* dn[] = {"Сьогодні", "Завтра"};
  for (int i = 0; i < 2; i++) {
    time_t ds = dayStart(i);
    struct tm tm; localtime_r(&ds, &tm);
    char b[40]; snprintf(b, sizeof(b), "%s, %02d.%02d", dn[i], tm.tm_mday, tm.tm_mon + 1);
    lv_obj_t* l = mkLabel(scr, b, &font_ua_14, i ? C_MUTED : C_TEXT);
    lv_obj_set_pos(l, 10, 42 + i * 50);
    s_dayBar[i] = lv_obj_create(scr);
    lv_obj_remove_style_all(s_dayBar[i]);
    lv_obj_set_size(s_dayBar[i], 300, 32);
    lv_obj_set_pos(s_dayBar[i], 10, 60 + i * 50);
    lv_obj_add_event_cb(s_dayBar[i], dayBarDrawCb, LV_EVENT_DRAW_MAIN, (void*)(intptr_t)i);
    if (i == 0) { lv_obj_add_flag(s_dayBar[i], LV_OBJ_FLAG_CLICKABLE); lv_obj_add_event_cb(s_dayBar[i], dayBarClickCb, LV_EVENT_CLICKED, nullptr); }
  }
  s_outSum = mkLabel(scr, "", &font_ua_14, C_TEXT);
  lv_obj_set_width(s_outSum, 300); lv_label_set_long_mode(s_outSum, LV_LABEL_LONG_WRAP);
  lv_obj_set_pos(s_outSum, 10, 144);
  legendRow(scr, 194, C_OUT_ON, "світло є", 10);
  legendRow(scr, 194, C_OUT_OFF, "немає", 90);
  legendRow(scr, 194, C_OUT_MB, "можливо", 156);
  s_outSrc = mkLabel(scr, "", &font_ua_12, C_MUTED);
  lv_obj_set_width(s_outSrc, 300); lv_label_set_long_mode(s_outSrc, LV_LABEL_LONG_DOT);
  lv_obj_set_pos(s_outSrc, 10, 214);
  outageFill();
  loadScreen(scr);
}

// ---------------- налаштування графіка ----------------
static lv_obj_t *s_osMode, *s_osProv, *s_osGrp;
static void osModeFill() {
  static const char* om[] = {"Режим: вимкнено", "Режим: авто (з інтернету)", "Режим: вручну"};
  lv_label_set_text(lv_obj_get_child(s_osMode, -1), om[g_set.outMode]);
  String p = "Джерело: ";
  p += g_set.outProv >= 0 && g_set.outProv < OUTAGE_PROV_COUNT ? String(OUTAGE_PROVS[g_set.outProv].name) : String("авто за областю");
  lv_label_set_text(lv_obj_get_child(s_osProv, -1), p.c_str());
}
static void outSettingsCb(lv_event_t*) { showOutSettings(); }
static void outBackCb(lv_event_t*) { settingsSave(); netOutageRefresh(); showOutage(); }

static void showOutSettings() {
  lv_obj_t* scr = mkScreen(SK_SETTINGS);
  mkHeader(scr, "Графік світла · налаштування", outBackCb);
  lv_obj_t* list = mkList(scr, 36, 204);
  s_osMode = mkListBtn(list, LV_SYMBOL_REFRESH, "", [](lv_event_t*) {
    g_set.outMode = (g_set.outMode + 1) % 3;
    if (g_set.outMode == OUT_MODE_MANUAL && !g_set.outMask) g_set.outMask = 0;
    osModeFill();
  }, nullptr);
  s_osProv = mkListBtn(list, LV_SYMBOL_DOWNLOAD, "", [](lv_event_t*) {
    // по колу: авто → постачальники цієї області → авто
    int i = g_set.outProv;
    for (int n = 0; n <= OUTAGE_PROV_COUNT; n++) {
      i++;
      if (i >= OUTAGE_PROV_COUNT) { i = -1; break; }
      if (OUTAGE_PROVS[i].oblMask & (1UL << g_set.oblast)) break;
    }
    g_set.outProv = i;
    osModeFill();
  }, nullptr);
  lv_list_add_text(list, "Ваша черга (група) відключень:");
  static const char* map[] = {"1.1", "1.2", "2.1", "2.2", "\n", "3.1", "3.2", "4.1", "4.2", "\n", "5.1", "5.2", "6.1", "6.2", ""};
  s_osGrp = lv_btnmatrix_create(list);
  lv_btnmatrix_set_map(s_osGrp, map);
  lv_btnmatrix_set_btn_ctrl_all(s_osGrp, LV_BTNMATRIX_CTRL_CHECKABLE);
  lv_btnmatrix_set_one_checked(s_osGrp, true);
  lv_obj_set_size(s_osGrp, LV_PCT(100), 120);
  lv_obj_set_style_bg_color(s_osGrp, col(C_BG), 0);
  lv_obj_set_style_border_width(s_osGrp, 0, 0);
  lv_obj_set_style_pad_all(s_osGrp, 2, 0);
  lv_obj_set_style_pad_gap(s_osGrp, 4, 0);
  lv_obj_set_style_bg_color(s_osGrp, col(C_PANEL), LV_PART_ITEMS);
  lv_obj_set_style_bg_color(s_osGrp, col(C_ACCENT), LV_PART_ITEMS | LV_STATE_CHECKED);
  lv_obj_set_style_text_color(s_osGrp, col(C_TEXT), LV_PART_ITEMS);
  lv_obj_set_style_text_font(s_osGrp, &font_ua_14, LV_PART_ITEMS);
  if (g_set.outGroup >= 0) {
    // індекс кнопки = індекс у OUTAGE_GROUPS (порядок 1.1..6.2)
    for (int i = 0; i < 12; i++)
      if (!strcmp(map[i < 4 ? i : i < 8 ? i + 1 : i + 2], OUTAGE_GROUPS[g_set.outGroup])) lv_btnmatrix_set_btn_ctrl(s_osGrp, i, LV_BTNMATRIX_CTRL_CHECKED);
  }
  lv_obj_add_event_cb(s_osGrp, [](lv_event_t* e) {
    lv_obj_t* m = lv_event_get_target(e);
    const char* t = lv_btnmatrix_get_btn_text(m, lv_btnmatrix_get_selected_btn(m));
    if (!t) return;
    for (int i = 0; i < OUTAGE_GROUP_COUNT; i++) if (!strcmp(t, OUTAGE_GROUPS[i])) g_set.outGroup = i;
    if (g_set.outMode == OUT_MODE_OFF) g_set.outMode = OUT_MODE_AUTO;
    osModeFill();
  }, LV_EVENT_VALUE_CHANGED, nullptr);
  lv_list_add_text(list, "Вручну: тапайте по шкалі «Сьогодні» на екрані графіка, щоб позначити години без світла.");
  osModeFill();
  loadScreen(scr);
}
static void setOutCb(lv_event_t*) { showOutSettings(); }

// ======================================================================================
static uint32_t s_lastUi = 0;

void uiInit() {
  lv_disp_t* d = lv_disp_get_default();
  lv_theme_t* th = lv_theme_default_init(d, col(C_ACCENT), col(C_RED), true, &font_ua_14);
  lv_disp_set_theme(d, th);
  lv_obj_set_style_bg_color(lv_scr_act(), col(C_BG), 0);
  memset(s_ob, '?', OBLAST_COUNT); s_ob[OBLAST_COUNT] = 0;
  memset(s_rg, '?', MAP_REGION_COUNT); s_rg[MAP_REGION_COUNT] = 0;
  if (!g_set.ssid.length()) showWifiScan();
  else showConnecting(g_set.ssid, g_set.pass, false);
}

void uiLoop() {
  uint32_t now = millis();
  if (now - s_lastUi < 250) return;
  s_lastUi = now;

  // якщо Wi-Fi відвалився вже після налаштування — просто чекаємо автоперепідключення
  stateLock();
  Level L = g_state.level;
  bool near = g_state.tgNear && g_state.tgLevel == L;
  time_t ts = g_state.tgTime;
  char text[TG_TEXT_MAX]; strlcpy(text, g_state.tgText, sizeof(text));
  char reason[120]; strlcpy(reason, g_state.tgReason, sizeof(reason));
  stateUnlock();
  static bool s_lastNear = false;

  bool canInterrupt = s_kind == SK_MAIN || s_kind == SK_DETAIL || s_kind == SK_ALERT || s_kind == SK_OUTAGE;
  if (!s_testMode && g_set.configured() && canInterrupt) {
    if (L > s_lastLevel || (L == LVL_YELLOW && near && !s_lastNear)) {   // ескалація або дрон підлетів
      notifyLevel(L, near);
      if (L == LVL_RED || g_set.alertOnYellow || near) showAlert(L, text, ts, reason, near);
      s_lastAlertTs = ts;
    } else if (L == LVL_RED && ts && ts != s_lastAlertTs) {  // новий «ракетний» пост
      notifyLevel(L, near);
      showAlert(L, text, ts, reason, near);
      s_lastAlertTs = ts;
    } else if (L < s_lastLevel) {
      notifyLevel(L, near);
      if (s_kind == SK_ALERT) showMain();
      if (L == LVL_GREEN) toast("Відбій · чисто", C_GREEN);
      else toast("Рівень знижено: дрони", C_YELLOW);
    }
    s_lastLevel = L;
    s_lastNear = near;
  }
  if (s_kind == SK_OUTAGE) outageTick();
  if (s_kind == SK_MAIN) updateMain(false);
}
