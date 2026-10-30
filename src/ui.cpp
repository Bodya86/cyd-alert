// Інтерфейс на LVGL 8.3: майстер Wi-Fi, вибір локації, мапа, сповіщення, налаштування
#include "app.h"
#include "analyzer.h"
#include "textutil.h"
#include "map_meta.h"
#include "map_views_meta.h"
#include "ota.h"
#include "version.h"
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
#define C_ORANGE  0xFF8C1A
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
#define C_TXT_ON  0x34C759   // текст статусу: світло є (зелений)
#define C_TXT_OFF 0xFF3B30   // текст статусу: світла немає (червоний)

static lv_color_t col(uint32_t h) { return lv_color_hex(h); }
static uint32_t levelColor(Level l) {
  return l == LVL_RED ? C_RED : l == LVL_ORANGE ? C_ORANGE : l == LVL_YELLOW ? C_YELLOW : C_GREEN;
}
// тло головного екрана за рівнем: колір загрози «заливає» весь екран (без модальних вікон)
static const uint32_t LEVEL_BG[4] = {C_BG, 0x17140A, 0x261406, 0x3A0A0E};
static const uint32_t LEVEL_CARD[4] = {0x10261A, 0x332A08, 0x40220A, 0x5A1016};

enum ScreenKind { SK_NONE, SK_SETUP, SK_MAIN, SK_DETAIL, SK_SETTINGS, SK_OUTAGE };
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

static uint8_t s_scanFails = 0;
static uint32_t s_scanAt = 0;
static lv_obj_t* s_wifiHint = nullptr;

// Зупинити спроби підключення до старої мережі: поки STA «connecting», esp_wifi_scan_start
// повертає помилку, і scanComplete() вічно дає WIFI_SCAN_FAILED (-2) — список порожній.
static void wifiIdle() {
  WiFi.setAutoReconnect(false);
  WiFi.disconnect(false, false);
  WiFi.mode(WIFI_STA);
}

static bool kickScan() {
  WiFi.scanDelete();
  int16_t r = WiFi.scanNetworks(true, true);   // async, з прихованими
  s_scanAt = millis();
  Serial.printf("[wifi] scan start -> %d (status %d)\n", r, (int)WiFi.status());
  return r != WIFI_SCAN_FAILED;
}

static void scanTimerCb(lv_timer_t*) {
  int n = WiFi.scanComplete();
  if (n == WIFI_SCAN_RUNNING && millis() - s_scanAt < 15000) return;
  if (n < 0) {   // помилка або завис
    s_scanFails++;
    Serial.printf("[wifi] scan failed (%d), try %u\n", n, s_scanFails);
    if (s_scanFails % 4 == 0) { WiFi.mode(WIFI_OFF); delay(50); }
    wifiIdle();
    kickScan();
    if (s_wifiHint && s_scanFails >= 3) lv_label_set_text(s_wifiHint, "Пошук не вдався, повтор… (або введіть вручну)");
    return;
  }
  lv_timer_del(s_scanTimer); s_scanTimer = nullptr;
  s_scanFails = 0;
  if (s_wifiSpinner) { lv_obj_del(s_wifiSpinner); s_wifiSpinner = nullptr; }
  lv_obj_clean(s_wifiList);
  s_wifiHint = nullptr;
  s_ssids.clear();
  Serial.printf("[wifi] scan done: %d networks\n", n);
  for (int i = 0; i < n; i++) Serial.printf("[wifi]  %s ch%d %d dBm\n", WiFi.SSID(i).c_str(), WiFi.channel(i), WiFi.RSSI(i));
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
  if (s_ssids.empty()) lv_list_add_text(s_wifiList, "Мереж не знайдено. Потрібна мережа 2.4 ГГц (5 ГГц не видно).");
  mkListBtn(s_wifiList, LV_SYMBOL_EDIT, "Ввести назву мережі вручну", wifiItemCb, (void*)(intptr_t)-1);
  mkListBtn(s_wifiList, LV_SYMBOL_REFRESH, "Оновити список", [](lv_event_t*) { showWifiScan(); }, nullptr);
  WiFi.scanDelete();
}

static void startScan() {
  s_scanFails = 0;
  wifiIdle();
  kickScan();
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
  s_wifiHint = lv_list_add_text(s_wifiList, "Пошук мереж… (тільки 2.4 ГГц)");
  mkListBtn(s_wifiList, LV_SYMBOL_EDIT, "Ввести назву мережі вручну", wifiItemCb, (void*)(intptr_t)-1);
  s_wifiSpinner = lv_spinner_create(scr, 1000, 60);
  lv_obj_set_size(s_wifiSpinner, 50, 50);
  lv_obj_align(s_wifiSpinner, LV_ALIGN_CENTER, 0, 30);
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
    WiFi.setAutoReconnect(true);
    Serial.printf("[wifi] connected to %s\n", s_connSsid.c_str());
    g_set.ssid = s_connSsid; g_set.pass = s_connPass;
    settingsSave();
    netForceRefresh();
    if (!g_set.configured()) showOblastPicker(); else showMain();
    return;
  }
  if (millis() - s_connStart > 20000) {
    lv_timer_del(s_connTimer); s_connTimer = nullptr;
    Serial.printf("[wifi] connect to %s failed (status %d) -> scan\n", s_connSsid.c_str(), (int)st);
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
  if (s_scanTimer) { lv_timer_del(s_scanTimer); s_scanTimer = nullptr; }
  WiFi.scanDelete();
  WiFi.disconnect();
  WiFi.setAutoReconnect(true);
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
static lv_obj_t *s_map, *s_lblPlace, *s_lblTime, *s_lblWifi, *s_lblBat, *s_lblCount, *s_lblCountSub, *s_lblSrc, *s_card, *s_cardBar,
    *s_cardTitle, *s_cardText, *s_marker, *s_outBox, *s_outState, *s_outTime, *s_outBar, *s_frame;
static Level s_uiLevel = LVL_GREEN;      // рівень, яким зараз пофарбовано головний екран
static Level s_preview = LVL_GREEN;      // тест кольору з налаштувань
static uint32_t s_previewUntil = 0;
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
  s_pal[MAP_BG] = col(LEVEL_BG[s_uiLevel]);
  s_pal[MAP_WATER] = col(0x1F4A73);   // водосховища (зум)
  s_pal[MAP_RIVER] = col(0x2C6AA8);   // річки (зум)
}

// ---- зум мапи: 0 — уся Україна, 1 — ваша область, 2 — околиці міста (±75 км) ----
// Растри кожного виду згенеровано заздалегідь (tools/gen_map_views.py) — на пристрої лише RLE-відмальовка.
extern const MapView MAP_VIEW_UA;
static const MapView* s_view = &MAP_VIEW_UA;
static uint8_t s_zoom = 0;
static uint32_t s_manualZoomAt = 0;      // натиснуто кнопку зуму — автозум мовчить 5 хв
#define MANUAL_ZOOM_HOLD_MS 300000UL
static uint32_t s_autoHoldAt = 0;        // коли востаннє була причина тримати наближення
static lv_obj_t* s_zoomBox = nullptr;
static lv_obj_t* s_zoomTag = nullptr;
static lv_obj_t *s_btnZin = nullptr, *s_btnZout = nullptr, *s_btnZhome = nullptr;
static RouteHeat s_heat;

static const MapView* viewFor(int z) {
  if (z == 1 && g_set.oblast >= 0 && g_set.oblast < 27 && MAP_OBL_VIEW[g_set.oblast] >= 0) return &MAP_VIEWS[MAP_OBL_VIEW[g_set.oblast]];
  if (z == 2) {
    const CityDef* c = userCity();
    if (c) for (int i = 0; i < MAP_CITY_VIEW_COUNT; i++)
      if (!strcmp(MAP_CITY_VIEWS[i].city, c->id)) return &MAP_VIEWS[MAP_CITY_VIEWS[i].view];
  }
  return z == 0 ? &MAP_VIEW_UA : nullptr;
}

// підпис поточного рівня: «Україна» / «Полтавська» / «Кременчуцький р-н»
static const char* zoomName(int z) {
  static char buf[48];
  if (z == 1 && g_set.oblast >= 0 && g_set.oblast < OBLAST_COUNT) {
    snprintf(buf, sizeof(buf), "%s", OBLASTS[g_set.oblast].name);
    char* p = strstr(buf, " обл.");
    if (p) *p = 0;
    return buf;
  }
  if (z == 2) {
    const CityDef* c = userCity();
    if (c && c->region >= 0 && c->region < MAP_REGION_COUNT) {
      snprintf(buf, sizeof(buf), "%s", MAP_REGIONS[c->region].name);
      char* p = strstr(buf, " район");
      if (p) strcpy(p, " р-н");
      return buf;
    }
    if (c) { snprintf(buf, sizeof(buf), "%s ±75 км", c->name); return buf; }
    return "Район";
  }
  return z == 0 ? "Україна" : "Область";
}

// vx/vy — координати в базовому кадрі виду; на екрані кадр зсунуто на panOf(), щоб ваша точка стояла
// в центрі видимої мапи (між верхньою панеллю і карткою статусу). Растр області має запас для цього зсуву.
static inline float vx(const MapView* v, float lon) { return (lon * MAP_PROJ_COSLAT - v->minx) * v->scale + v->offx; }
static inline float vy(const MapView* v, float lat) { return (-lat - v->miny) * v->scale + v->offy; }
static lv_point_t panOf(const MapView* v);
static lv_point_t s_pan = {0, 0};          // зсув кадру поточного виду
static inline lv_point_t mapPt(const lv_area_t& c, float lat, float lon) {
  lv_point_t p;
  p.x = (lv_coord_t)(c.x1 + vx(s_view, lon) - s_pan.x);
  p.y = (lv_coord_t)(c.y1 + vy(s_view, lat) - s_pan.y);
  return p;
}
static float kmBetween(float la1, float lo1, float la2, float lo2) {
  float dx = (lo2 - lo1) * 111.32f * cosf((la1 + la2) * 0.5f * 0.0174533f), dy = (la2 - la1) * 110.57f;
  return sqrtf(dx * dx + dy * dy);
}
// точка користувача: місто, на зумі 2 — зміщена на ~3 км у бік вашої частини міста
static bool userPoint(float& lat, float& lon) {
  const CityDef* c = userCity();
  if (!c) return false;
  lat = c->lat; lon = c->lon;
  const DistrictDef* d = userDistrict();
  if (d && d->sector >= 0 && s_zoom == 2) {
    float b = d->sector * 0.0174533f;
    lat += 3.0f / 111.0f * cosf(b);
    lon += 3.0f / (111.0f * cosf(lat * 0.0174533f)) * sinf(b);
  }
  return true;
}
// зсув кадру: місто користувача — у центр видимої мапи, у межах наявного растру (уся Україна — без зсуву)
static lv_point_t panOf(const MapView* v) {
  lv_point_t p = {0, 0};
  const CityDef* c = userCity();
  if (!v || !c || (v->rw <= MAP_W && v->rh <= MAP_H)) return p;
  int dx = (int)lroundf(vx(v, c->lon) - MAP_W / 2.0f), dy = (int)lroundf(vy(v, c->lat) - MAP_H / 2.0f);
  p.x = (lv_coord_t)LV_CLAMP(-v->ex, dx, v->rw - v->ex - MAP_W);
  p.y = (lv_coord_t)LV_CLAMP(-v->ey, dy, v->rh - v->ey - MAP_H);
  return p;
}
// підпис мапи, що потрапляє під мітку рівня зуму або кнопки «+ − ⌂», не малюємо (екранні координати)
static bool labelHidden(const lv_area_t& a) {
  lv_obj_t* const cover[4] = {s_zoomTag, s_btnZin, s_btnZout, s_btnZhome};
  for (lv_obj_t* o : cover) {
    if (!o || lv_obj_has_flag(o, LV_OBJ_FLAG_HIDDEN)) continue;
    lv_area_t r, x; lv_obj_get_coords(o, &r);
    if (_lv_area_intersect(&x, &a, &r)) return true;
  }
  return false;
}

static void drawTargets(lv_draw_ctx_t* dc, const lv_area_t& c);

static void drawHeat(lv_draw_ctx_t* dc, const lv_area_t& c, const lv_area_t* clip) {
  const RouteHeat& h = s_heat;
  if (!g_set.showRoutes || s_zoom < 1 || !h.w || !h.h) return;
  lv_draw_rect_dsc_t d; lv_draw_rect_dsc_init(&d);
  d.radius = 0; d.border_width = 0; d.bg_color = col(0xFF7A1A);
  for (int r = 0; r < h.h; r++)
    for (int q = 0; q < h.w; q++) {
      uint8_t v = h.v[r * h.w + q];
      if (v < 3) continue;
      float la = h.la0 - r * HEAT_STEP, lo = h.lo0 + q * HEAT_STEP;
      lv_point_t a = mapPt(c, la + HEAT_STEP / 2, lo - HEAT_STEP / 2), b = mapPt(c, la - HEAT_STEP / 2, lo + HEAT_STEP / 2);
      lv_area_t ar = {a.x, a.y, (lv_coord_t)(b.x - 1), (lv_coord_t)(b.y - 1)}, rr;
      if (!_lv_area_intersect(&rr, &ar, clip)) continue;
      d.bg_opa = (lv_opa_t)(20 + v * 4);   // ≤ ~60% — не перекриває тривоги
      lv_draw_rect(dc, &d, &rr);
    }
}

static void drawLabels(lv_draw_ctx_t* dc, const lv_area_t& c) {
  const MapView* v = s_view;
  if (!v->labels) return;
  lv_draw_label_dsc_t ld; lv_draw_label_dsc_init(&ld);
  ld.font = &font_ua_12;
  lv_draw_rect_dsc_t dd; lv_draw_rect_dsc_init(&dd);
  dd.bg_opa = LV_OPA_COVER; dd.radius = 0; dd.border_width = 0;
  static const uint32_t KC[3] = {0xE6EDF3, 0xC9D1D9, 0x8B98A5};
  const int ox = c.x1 - s_pan.x, oy = c.y1 - s_pan.y;
  for (int i = 0; i < v->nlabels; i++) {
    const MapLabel& L = v->labels[i];
    if (L.kind >= 2 && s_uiLevel >= LVL_ORANGE) continue;   // під час загрози — без дрібних сіл, лише міста/райцентри
    lv_area_t a = {(lv_coord_t)(ox + L.lx), (lv_coord_t)(oy + L.ly - 1), (lv_coord_t)(ox + L.lx + 110), (lv_coord_t)(oy + L.ly + 14)};
    lv_area_t tb = a;
    tb.x2 = (lv_coord_t)(tb.x1 + lv_txt_get_width(L.name, strlen(L.name), ld.font, 0, LV_TEXT_FLAG_NONE));
    tb.y1 = (lv_coord_t)LV_MIN(tb.y1, oy + L.y - 1);
    if (tb.x1 < c.x1 || tb.x2 > c.x2 || tb.y1 < c.y1 - 2 || tb.y1 + 14 > c.y2 || labelHidden(tb)) continue;   // не обрізаний краєм, не під міткою зуму / кнопками
    lv_area_t dot = {(lv_coord_t)(ox + L.x - 1), (lv_coord_t)(oy + L.y - 1), (lv_coord_t)(ox + L.x + (L.kind < 2 ? 1 : 0)), (lv_coord_t)(oy + L.y + (L.kind < 2 ? 1 : 0))};
    dd.bg_color = col(KC[L.kind > 2 ? 2 : L.kind]);
    lv_draw_rect(dc, &dd, &dot);
    lv_area_t sh = a; sh.x1++; sh.x2++; sh.y1++; sh.y2++;
    ld.color = lv_color_black(); ld.opa = LV_OPA_70;
    lv_draw_label(dc, &ld, &sh, L.name, nullptr);
    ld.color = col(KC[L.kind > 2 ? 2 : L.kind]); ld.opa = LV_OPA_COVER;
    lv_draw_label(dc, &ld, &a, L.name, nullptr);
  }
}

// мапа: RLE-рядки → прямокутники; сусідні відрізки з однаковим кольором зливаються
static void mapDrawCb(lv_event_t* e) {
  lv_obj_t* obj = lv_event_get_target(e);
  lv_draw_ctx_t* dc = lv_event_get_draw_ctx(e);
  lv_area_t c; lv_obj_get_coords(obj, &c);
  const lv_area_t* clip = dc->clip_area;
  const uint16_t* ROWS = s_view->rowOfs;
  const uint8_t* RUNS = s_view->runs;
  lv_draw_rect_dsc_t d; lv_draw_rect_dsc_init(&d);
  d.bg_opa = LV_OPA_COVER; d.radius = 0; d.border_width = 0;
  int y0 = LV_MAX(clip->y1, c.y1), y1 = LV_MIN(LV_MIN(clip->y2, c.y2), c.y1 + MAP_H - 1);
  const int xs = s_view->ex + s_pan.x, ys = s_view->ey + s_pan.y;   // кадр у растрі
  for (int y = y0; y <= y1; y++) {
    int row = y - c.y1 + ys, x = -xs, runX = 0, runN = 0;
    if (row < 0 || row >= s_view->rh) continue;
    lv_color_t runC = s_pal[MAP_BG];
    auto flush = [&]() {
      if (!runN) return;
      lv_area_t a = {(lv_coord_t)(c.x1 + runX), (lv_coord_t)y, (lv_coord_t)(c.x1 + runX + runN - 1), (lv_coord_t)y};
      lv_area_t r;
      if (_lv_area_intersect(&r, &a, clip)) { d.bg_color = runC; lv_draw_rect(dc, &d, &r); }
      runN = 0;
    };
    for (int i = ROWS[row]; i < ROWS[row + 1]; i++) {
      uint8_t n = RUNS[i * 2], v = RUNS[i * 2 + 1];
      int a0 = x, a1 = x + n;
      x = a1;
      if (a1 <= 0) continue;
      if (a0 >= MAP_W) break;
      if (a0 < 0) a0 = 0;
      if (a1 > MAP_W) a1 = MAP_W;
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
}

// ---- цілі трекера поверх мапи ----
// значок за типом (масштаб від зуму), колір = тип (без підписів), пунктир теоретичного курсу з мітками
// +5/+15/+30 хв, блідий конус невизначеності. Кожна ціль малюється окремо — навіть дві далекі на всю Україну.
static MapTarget s_tgt[MAX_TARGETS];
static uint8_t s_ntgt = 0;

// Ціль розрізняється лише кольором і силуетом (без підписів). Кольори обрано так, щоб не зливалися
// із заливкою областей (зелена/жовта/помаранчева/червона): дрони — фіолетовий «шахед» з білою+темною обвідкою,
// крилаті ракети / КАБ — червоний ромб з білою обвідкою, балістика — блакитний ромб з темною обвідкою,
// інше / невідомо — помаранчевий з білою обвідкою. Курс — білий пунктир з темною підкладкою;
// пройдений шлях — дрібний пунктир кольором цілі з темною підкладкою.
#define TC_DRONE   0xB44CFF
#define TC_MISSILE 0xFF2B2B
#define TC_BALL    0x2EE6FF
#define TC_OTHER   0xFF8C1A
#define TC_COURSE  0xFFFFFF
static uint32_t targetColor(char ch) {
  switch (ch) {
    case 'S': case 'J': case 'D': return TC_DRONE;
    case 'C': case 'K':           return TC_MISSILE;
    case 'B':                     return TC_BALL;
    default:                      return TC_OTHER;
  }
}

// пунктир: phase переноситься між відрізками, щоб штрихи йшли рівно по ламаній
static void dashLine(lv_draw_ctx_t* dc, const lv_draw_line_dsc_t* ln, lv_point_t a, lv_point_t b, float& phase,
                     float ON = 4, float PER = 7) {
  float dx = b.x - a.x, dy = b.y - a.y, L = sqrtf(dx * dx + dy * dy);
  if (L < 0.5f) return;
  dx /= L; dy /= L;
  float s = 0;
  while (s < L) {
    float ph = fmodf(phase + s, PER);
    float seg = ph < ON ? LV_MIN(ON - ph, L - s) : LV_MIN(PER - ph, L - s);
    if (ph < ON && seg >= 0.8f) {
      lv_point_t p = {(lv_coord_t)(a.x + dx * s + 0.5f), (lv_coord_t)(a.y + dy * s + 0.5f)};
      lv_point_t q = {(lv_coord_t)(a.x + dx * (s + seg) + 0.5f), (lv_coord_t)(a.y + dy * (s + seg) + 0.5f)};
      lv_draw_line(dc, ln, &p, &q);
    }
    s += seg > 0.01f ? seg : 0.5f;
  }
  phase = fmodf(phase + L, PER);
}

// силует за курсом: (u, v) — u вздовж курсу (вперед +), v — вбік; p0 — центр
static inline lv_point_t tpt(lv_point_t p0, float sx, float sy, float s, float fw, float side) {
  // fw — вперед (у одиницях s), side — праворуч
  return {(lv_coord_t)lroundf(p0.x + s * (fw * sx - side * sy)), (lv_coord_t)lroundf(p0.y + s * (fw * sy + side * sx))};
}

static void drawTargets(lv_draw_ctx_t* dc, const lv_area_t& c) {
  if (!s_ntgt) return;
  const lv_area_t* clip = dc->clip_area;
  const float KM_PX = s_view->scale / 111.0f;      // пікселів на км (широта)
  static const float ICON[3] = {6.0f, 7.5f, 9.0f};
  float S = ICON[s_zoom > 2 ? 2 : s_zoom];
  lv_draw_rect_dsc_t cone; lv_draw_rect_dsc_init(&cone);
  cone.radius = LV_RADIUS_CIRCLE; cone.border_width = 0; cone.bg_opa = LV_OPA_10;
  // курс: темна підкладка (ширша) + білий пунктир 2 px — видно на будь-якій заливці
  lv_draw_line_dsc_t lnD; lv_draw_line_dsc_init(&lnD);
  lnD.width = 4; lnD.opa = LV_OPA_70; lnD.color = lv_color_black(); lnD.round_start = 0; lnD.round_end = 0;
  lv_draw_line_dsc_t lnW = lnD; lnW.width = 2; lnW.opa = LV_OPA_COVER; lnW.color = col(TC_COURSE);
  lv_draw_line_dsc_t tkD = lnD; tkD.width = 3;
  lv_draw_line_dsc_t tkW = lnW; tkW.width = 1;
  lv_point_t pos[MAX_TARGETS]; bool vis[MAX_TARGETS] = {false};
  // 1) конуси й пунктир — під значками
  for (int k = s_ntgt - 1; k >= 0; k--) {
    const MapTarget& t = s_tgt[k];
    lv_color_t tc = col(targetColor(t.ch));
    lv_point_t p0 = mapPt(c, t.lat, t.lon);
    pos[k] = p0;
    bool in = !(p0.x < c.x1 - 12 || p0.x > c.x2 + 12 || p0.y < c.y1 - 12 || p0.y > c.y2 + 12);
    vis[k] = in;
    // межі курсу — щоб не рахувати лінії для смуг буфера, яких ціль не торкається
    lv_area_t bb = {p0.x, p0.y, p0.x, p0.y};
    for (int i = 0; i < t.npt; i++) {
      lv_point_t q = mapPt(c, t.pla[i], t.plo[i]);
      int r = LV_CLAMP(2, (int)(t.psd[i] * KM_PX + 0.5f), s_zoom ? 40 : 16);
      bb.x1 = LV_MIN(bb.x1, q.x - r); bb.y1 = LV_MIN(bb.y1, q.y - r); bb.x2 = LV_MAX(bb.x2, q.x + r); bb.y2 = LV_MAX(bb.y2, q.y + r);
    }
    for (int i = 0; i < t.ntr; i++) {
      lv_point_t q = mapPt(c, t.tla[i], t.tlo[i]);
      bb.x1 = LV_MIN(bb.x1, q.x); bb.y1 = LV_MIN(bb.y1, q.y); bb.x2 = LV_MAX(bb.x2, q.x); bb.y2 = LV_MAX(bb.y2, q.y);
    }
    bb.x1 -= 4; bb.y1 -= 4; bb.x2 += 4; bb.y2 += 4;
    lv_area_t tmp;
    if (!_lv_area_intersect(&tmp, &bb, clip)) continue;
    cone.bg_color = tc;
    for (int i = 0; i < t.npt; i++) {
      lv_point_t q = mapPt(c, t.pla[i], t.plo[i]);
      int r = LV_CLAMP(2, (int)(t.psd[i] * KM_PX + 0.5f), s_zoom ? 40 : 16);
      lv_area_t a = {(lv_coord_t)(q.x - r), (lv_coord_t)(q.y - r), (lv_coord_t)(q.x + r), (lv_coord_t)(q.y + r)};
      lv_draw_rect(dc, &cone, &a);
    }
    // пройдений шлях: дрібний пунктир кольором цілі на темній підкладці (цілі виляють — ламана, не пряма)
    if (t.ntr) {
      lv_draw_line_dsc_t trC = lnW; trC.color = tc;
      for (int pass = 0; pass < 2; pass++) {
        float phase = 0;
        lv_point_t prev = mapPt(c, t.tla[0], t.tlo[0]);
        for (int i = 1; i <= t.ntr; i++) {
          lv_point_t q = i < t.ntr ? mapPt(c, t.tla[i], t.tlo[i]) : p0;
          dashLine(dc, pass ? &trC : &lnD, prev, q, phase, 2, 5);
          prev = q;
        }
      }
    }
    for (int pass = 0; pass < 2; pass++) {
      const lv_draw_line_dsc_t* ln = pass ? &lnW : &lnD;
      const lv_draw_line_dsc_t* tk = pass ? &tkW : &tkD;
      lv_point_t prev = p0;
      float phase = 0;
      for (int i = 0; i < t.npt; i++) {
        lv_point_t q = mapPt(c, t.pla[i], t.plo[i]);
        if (q.x == prev.x && q.y == prev.y) continue;
        dashLine(dc, ln, prev, q, phase);
        // мітка часу +5/+15/+30 хв: короткий поперечний штрих (без тексту)
        float dx = q.x - prev.x, dy = q.y - prev.y, L = sqrtf(dx * dx + dy * dy);
        if (L > 0.5f) {
          dx /= L; dy /= L;
          lv_point_t a1 = {(lv_coord_t)(q.x + 3 * dy), (lv_coord_t)(q.y - 3 * dx)}, a2 = {(lv_coord_t)(q.x - 3 * dy), (lv_coord_t)(q.y + 3 * dx)};
          lv_draw_line(dc, tk, &a1, &a2);
        }
        prev = q;
      }
    }
  }
  // 2) значки: дрони — силует «шахеда» (дельта-крило + фюзеляж) за курсом; ракети/КАБ/балістика — ромб.
  //    Шари: темний контур → світлий контур → заливка кольором типу.
  lv_draw_rect_dsc_t ic; lv_draw_rect_dsc_init(&ic);
  ic.bg_opa = LV_OPA_COVER; ic.border_width = 0;
  for (int k = s_ntgt - 1; k >= 0; k--) {
    if (!vis[k]) continue;
    lv_point_t p0 = pos[k];
    lv_area_t bb = {(lv_coord_t)(p0.x - S - 4), (lv_coord_t)(p0.y - S - 4), (lv_coord_t)(p0.x + S + 4), (lv_coord_t)(p0.y + S + 4)}, tmp;
    if (!_lv_area_intersect(&tmp, &bb, clip)) continue;
    const MapTarget& t = s_tgt[k];
    lv_color_t tc = col(targetColor(t.ch));
    bool missile = t.ch == 'C' || t.ch == 'K' || t.ch == 'B';
    bool drone = !missile && t.hd >= 0 && (t.ch == 'S' || t.ch == 'J' || t.ch == 'D');
    float hr = (t.hd >= 0 ? t.hd : 0) * 0.0174533f, sx = sinf(hr), sy = -cosf(hr);
    // ballistic: темний контур, без білого (заливка й так світла); решта — темний + білий
    const lv_color_t layers[3] = {lv_color_black(), t.ch == 'B' ? lv_color_black() : lv_color_white(), tc};
    const float grow[3] = {2.2f, 1.1f, 0.0f};
    for (int pass = 0; pass < 3; pass++) {
      float s = S + grow[pass];
      ic.bg_color = layers[pass];
      if (drone) {
        // дельта-крило
        lv_point_t w[3] = {tpt(p0, sx, sy, s, 0.85f, 0), tpt(p0, sx, sy, s, -0.75f, 1.0f), tpt(p0, sx, sy, s, -0.75f, -1.0f)};
        lv_draw_polygon(dc, &ic, w, 3);
        // фюзеляж (видовжений ромб) — ніс виступає вперед, хвіст позаду крила
        float g = grow[pass] / s;
        lv_point_t f[4] = {tpt(p0, sx, sy, s, 1.15f + g * 0.3f, 0), tpt(p0, sx, sy, s, 0.0f, 0.2f + g * 0.6f),
                           tpt(p0, sx, sy, s, -1.0f - g * 0.3f, 0), tpt(p0, sx, sy, s, 0.0f, -0.2f - g * 0.6f)};
        lv_draw_polygon(dc, &ic, f, 4);
      } else {
        lv_point_t pts[4] = {tpt(p0, sx, sy, s, 1.0f, 0), tpt(p0, sx, sy, s, 0, 0.7f), tpt(p0, sx, sy, s, -1.0f, 0), tpt(p0, sx, sy, s, 0, -0.7f)};
        lv_draw_polygon(dc, &ic, pts, 4);
      }
    }
  }
}

// маркер користувача під поточний вид
static void placeMarker() {
  if (!s_marker) return;
  float la, lo;
  int mx, my;
  if (userPoint(la, lo)) { mx = (int)lroundf(vx(s_view, lo)) - s_pan.x; my = (int)lroundf(vy(s_view, la)) - s_pan.y; }
  else if (g_set.oblast >= 0 && s_zoom == 0) { mx = MAP_CENTROID[g_set.oblast][0]; my = MAP_CENTROID[g_set.oblast][1]; }
  else { mx = MAP_W / 2; my = MAP_H / 2; }
  lv_obj_set_pos(s_marker, mx - 4, my - 4);
}

// перехід: рамка-«лупа» з прямокутника нового виду (у старих координатах) розтягується на всю мапу, ~180 мс
static lv_area_t s_zoomFrom;
static void zoomAnimCb(void* o, int32_t v) {
  lv_obj_t* b = (lv_obj_t*)o;
  int x1 = s_zoomFrom.x1 + (0 - s_zoomFrom.x1) * v / 256, y1 = s_zoomFrom.y1 + (0 - s_zoomFrom.y1) * v / 256;
  int x2 = s_zoomFrom.x2 + (MAP_W - 1 - s_zoomFrom.x2) * v / 256, y2 = s_zoomFrom.y2 + (MAP_H - 1 - s_zoomFrom.y2) * v / 256;
  lv_obj_set_pos(b, x1, y1);
  lv_obj_set_size(b, LV_MAX(4, x2 - x1 + 1), LV_MAX(4, y2 - y1 + 1));
  lv_obj_set_style_border_opa(b, (lv_opa_t)(255 - v * 3 / 4), 0);
}
static void zoomAnimDone(lv_anim_t* a) { lv_obj_add_flag((lv_obj_t*)a->var, LV_OBJ_FLAG_HIDDEN); }

static int nextZoom(int dir) {          // наступний доступний рівень (без міста — лише Україна ↔ область)
  for (int z = s_zoom + dir; z >= 0 && z <= 2; z += dir) if (viewFor(z)) return z;
  return -1;
}
static void zoomBtnState(lv_obj_t* b, bool on) {
  if (!b) return;
  if (on) lv_obj_clear_state(b, LV_STATE_DISABLED); else lv_obj_add_state(b, LV_STATE_DISABLED);
}
static void updateZoomBtns() {
  zoomBtnState(s_btnZin, nextZoom(+1) >= 0);
  zoomBtnState(s_btnZout, nextZoom(-1) >= 0);
  zoomBtnState(s_btnZhome, s_zoom != 0);
}

static void setZoom(int z, bool anim) {
  const MapView* nv = viewFor(z);
  if (!nv || !s_map || (z == s_zoom && nv == s_view)) return;
  const MapView* ov = s_view;
  // межі нового виду в пікселях старого (або навпаки при віддаленні)
  const MapView* inner = z > s_zoom ? nv : ov;
  const MapView* outer = z > s_zoom ? ov : nv;
  lv_point_t pi = panOf(inner), po = panOf(outer);
  float lo1 = (inner->minx + (pi.x - inner->offx) / inner->scale) / MAP_PROJ_COSLAT, la1 = -(inner->miny + (pi.y - inner->offy) / inner->scale);
  float lo2 = (inner->minx + (pi.x + MAP_W - inner->offx) / inner->scale) / MAP_PROJ_COSLAT, la2 = -(inner->miny + (pi.y + MAP_H - inner->offy) / inner->scale);
  s_zoomFrom.x1 = (lv_coord_t)LV_CLAMP(0, (int)vx(outer, lo1) - po.x, MAP_W - 4); s_zoomFrom.y1 = (lv_coord_t)LV_CLAMP(0, (int)vy(outer, la1) - po.y, MAP_H - 4);
  s_zoomFrom.x2 = (lv_coord_t)LV_CLAMP(s_zoomFrom.x1 + 3, (int)vx(outer, lo2) - po.x, MAP_W - 1); s_zoomFrom.y2 = (lv_coord_t)LV_CLAMP(s_zoomFrom.y1 + 3, (int)vy(outer, la2) - po.y, MAP_H - 1);
  bool zoomIn = z > s_zoom;
  s_zoom = z; s_view = nv; s_pan = panOf(nv);
  placeMarker();
  lv_obj_invalidate(s_map);
  if (s_zoomTag) lv_label_set_text(s_zoomTag, zoomName(z));
  updateZoomBtns();
  if (anim && s_zoomBox) {
    lv_obj_clear_flag(s_zoomBox, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(s_zoomBox);
    lv_anim_t a; lv_anim_init(&a);
    lv_anim_set_var(&a, s_zoomBox);
    lv_anim_set_exec_cb(&a, zoomAnimCb);
    lv_anim_set_values(&a, zoomIn ? 0 : 256, zoomIn ? 256 : 0);
    lv_anim_set_time(&a, 180);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
    lv_anim_set_ready_cb(&a, zoomAnimDone);
    lv_anim_start(&a);
  }
}

// кнопки «+» / «−» / «⌂» у кутку мапи (тап по самій мапі зум не змінює)
static void zoomBtnCb(lv_event_t* e) {
  int act = (int)(intptr_t)lv_event_get_user_data(e);   // +1, -1, 0 — уся Україна
  int z = act == 0 ? 0 : nextZoom(act);
  s_manualZoomAt = millis() | 1;
  s_autoHoldAt = 0;
  if (z >= 0) setZoom(z, true);
}
static lv_obj_t* mkZoomBtn(lv_obj_t* p, const char* sym, int act, int x, int y) {
  lv_obj_t* b = lv_btn_create(p);
  lv_obj_set_size(b, 36, 36);
  lv_obj_set_pos(b, x, y);
  lv_obj_set_style_radius(b, 8, 0);
  lv_obj_set_style_shadow_width(b, 0, 0);
  lv_obj_set_style_bg_color(b, col(C_BG), 0);
  lv_obj_set_style_bg_opa(b, LV_OPA_70, 0);
  lv_obj_set_style_border_color(b, col(C_BORDER), 0);
  lv_obj_set_style_border_width(b, 1, 0);
  lv_obj_set_style_border_opa(b, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(b, col(C_PANEL2), LV_STATE_PRESSED);
  lv_obj_set_style_bg_opa(b, LV_OPA_90, LV_STATE_PRESSED);
  lv_obj_set_style_border_color(b, col(C_ACCENT), LV_STATE_PRESSED);
  lv_obj_set_style_bg_color(b, col(C_BG), LV_STATE_DISABLED);
  lv_obj_set_style_bg_opa(b, LV_OPA_40, LV_STATE_DISABLED);
  lv_obj_set_style_text_opa(b, LV_OPA_40, LV_STATE_DISABLED);
  lv_obj_set_ext_click_area(b, 2);
  lv_obj_clear_flag(b, LV_OBJ_FLAG_SCROLL_ON_FOCUS);
  lv_obj_t* l = mkLabel(b, sym, &font_ua_18, C_TEXT);
  lv_obj_center(l);
  lv_obj_add_event_cb(b, zoomBtnCb, LV_EVENT_CLICKED, (void*)(intptr_t)act);
  return b;
}

// автозум: ціль поруч / рівень ≥ помаранчевий → район; загроза в області → область; чисто 2 хв → Україна
static void autoZoom(const AppState& s) {
  if (!g_set.autoZoom || !s_map) return;
  if (s_manualZoomAt && millis() - s_manualZoomAt < MANUAL_ZOOM_HOLD_MS) return;
  s_manualZoomAt = 0;
  float ula, ulo; bool haveU = userPoint(ula, ulo);
  float nearKm = 1e9f; bool inObl = false;
  const MapView* ov = viewFor(1);
  for (int i = 0; i < s.ntgt; i++) {
    const MapTarget& t = s.tgt[i];
    if (haveU) {
      float d = kmBetween(t.lat, t.lon, ula, ulo);
      for (int j = 0; j < t.npt; j++) d = LV_MIN(d, kmBetween(t.pla[j], t.plo[j], ula, ulo) + 15);   // курс на вас
      nearKm = LV_MIN(nearKm, d);
    }
    if (ov) {
      lv_point_t po = panOf(ov);
      float x = vx(ov, t.lon) - po.x, y = vy(ov, t.lat) - po.y;
      if (x >= 0 && x < MAP_W && y >= 0 && y < MAP_H) inObl = true;
    }
  }
  int want = 0;
  if (s.level >= LVL_ORANGE || nearKm < 45) want = viewFor(2) ? 2 : 1;
  else if (s.level == LVL_YELLOW || inObl || nearKm < 150) want = 1;
  if (!viewFor(want)) want = viewFor(1) ? 1 : 0;
  uint32_t now = millis();
  if (want >= s_zoom) { if (want > 0) s_autoHoldAt = now; if (want > s_zoom) setZoom(want, true); }
  else if (now - s_autoHoldAt > 120000UL) setZoom(want, true);
}


#define RED_PULSE_MS 500
static lv_timer_t* s_pulseT = nullptr;
static void mainDeleted(lv_event_t*) {
  s_main = nullptr; s_frame = nullptr; s_map = nullptr; s_marker = s_zoomBox = s_zoomTag = nullptr; s_btnZin = s_btnZout = s_btnZhome = nullptr;
  s_card = s_cardBar = nullptr;
  lv_anim_del(nullptr, zoomAnimCb);
  if (s_pulseT) { lv_timer_del(s_pulseT); s_pulseT = nullptr; }
}
static void settingsBtnCb(lv_event_t*) { showSettings(); }
static void showDetail();
static void cardCb(lv_event_t*) { notifySilence(); showDetail(); }   // дотик до картки — тиша до наступного підвищення
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
// 4 рядки картки загрози: місце; тип + куди летить; ймовірність прольоту поруч; ймовірна ціль
static void cardLines(const AppState& s, Level L, String ln[4]) {
  const CityDef* c = userCity();
  int me = userRegion();
  ln[0] = c ? String(c->name) : userPlaceName();
  if (me >= 0) {
    String r = MAP_REGIONS[me].name;
    r.replace(" район", " р-н");
    ln[0] += ", " + r;
  }
  if (L == LVL_GREEN) ln[1] = "Загроз немає";
  else if (s.thr[0] && strcmp(s.thr, "Загроз немає")) ln[1] = s.thr;
  else {
    bool drone = !strcmp(s.tgKind, "Дрони") || !s.tgKind[0];
    ln[1] = drone ? String("Дрони — Шахед/БпЛА") : String(s.tgKind);
    ln[1] += L == LVL_RED ? ", ваш район" : L == LVL_ORANGE ? ", місто" : ", область";
  }
  int pn = s.pBallistic > s.pNear ? s.pBallistic : s.pNear;
  // червона тривога: рядок ймовірності не показуємо (рішення користувача — цифра лише відволікає, треба в укриття)
  if (L == LVL_RED) ln[2] = "";
  else ln[2] = s.trkOk ? "Ймовірність прольоту поруч за 30 хв: " + String(pn) + "%" : String("Ймовірність прольоту поруч за 30 хв: —");
  ln[3] = s.ltName[0] && s.ltP ? "Ймовірна ціль " + String(s.ltName) + " — " + String(s.ltP) + "%" : String("Ймовірна ціль: —");
}

// пульс лише картки статусу (рамка й смужка), ~2 кадри/с: раніше пульсувала рамка всього екрана й тло
// на 50 кадрах/с — це щоразу перемальовувало весь екран разом із мапою і гальмувало інтерфейс.
static void pulseTimerCb(lv_timer_t*) {
  if (!s_card || !s_cardBar) return;
  static bool ph = false;
  Level L = s_uiLevel;
  if (L < LVL_ORANGE) return;
  ph = !ph;
  uint32_t hi = L == LVL_RED ? 0xFF3B3F : C_ORANGE, lo = L == LVL_RED ? 0x7A0010 : 0x8A4008;
  lv_obj_set_style_border_color(s_card, col(ph ? hi : lo), 0);
  lv_obj_set_style_bg_color(s_cardBar, col(ph ? hi : lo), 0);
}

static void applyLevelTint(Level L) {
  s_uiLevel = L;
  static const uint8_t bw[4] = {0, 1, 3, 5};
  lv_obj_set_style_bg_color(s_main, col(LEVEL_BG[L]), 0);
  lv_obj_set_style_border_width(s_frame, bw[L], 0);
  lv_obj_set_style_border_color(s_frame, col(levelColor(L)), 0);
  lv_obj_set_style_border_opa(s_frame, L == LVL_YELLOW ? LV_OPA_60 : LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(s_card, col(LEVEL_CARD[L]), 0);
  lv_obj_set_style_bg_color(s_cardBar, col(levelColor(L)), 0);
  lv_obj_set_size(s_cardBar, L >= LVL_ORANGE ? 10 : 6, 46);
  lv_obj_set_style_border_width(s_card, L >= LVL_ORANGE ? 2 : 0, 0);
  lv_obj_set_style_border_color(s_card, col(levelColor(L)), 0);
  lv_obj_set_style_text_color(s_cardTitle, col(levelColor(L)), 0);
  lv_obj_set_style_text_color(s_cardText, col(L == LVL_RED ? 0xFFD9DA : L == LVL_ORANGE ? 0xFFE2C4 : C_MUTED), 0);
  lv_obj_set_pos(s_cardTitle, L >= LVL_ORANGE ? 18 : 14, 3);
  lv_obj_set_pos(s_cardText, L >= LVL_ORANGE ? 18 : 14, 26);
}

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
  s_lblBat = mkLabel(scr, "", &font_ua_12, C_MUTED);   // заряд АКБ — лише якщо підключено дільник
  lv_obj_set_pos(s_lblBat, 160, 6);
  lv_obj_add_flag(s_lblBat, LV_OBJ_FLAG_HIDDEN);
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
  lv_obj_clear_flag(s_map, LV_OBJ_FLAG_CLICKABLE);   // тап по мапі нічого не робить — зум лише кнопками
  lv_obj_clear_flag(s_map, LV_OBJ_FLAG_SCROLLABLE);
  if (!viewFor(s_zoom)) { s_zoom = 0; }
  s_view = viewFor(s_zoom);
  s_pan = panOf(s_view);

  // маркер користувача
  s_marker = lv_obj_create(s_map);
  lv_obj_remove_style_all(s_marker);
  lv_obj_clear_flag(s_marker, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_set_size(s_marker, 9, 9);
  lv_obj_set_style_radius(s_marker, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_color(s_marker, col(0xFFFFFF), 0);
  lv_obj_set_style_bg_opa(s_marker, LV_OPA_COVER, 0);
  lv_obj_set_style_border_color(s_marker, col(C_ACCENT), 0);
  lv_obj_set_style_border_width(s_marker, 2, 0);
  placeMarker();
  // рамка переходу між видами + мітка поточного зуму
  s_zoomBox = lv_obj_create(s_map);
  lv_obj_remove_style_all(s_zoomBox);
  lv_obj_clear_flag(s_zoomBox, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_set_style_border_color(s_zoomBox, col(C_ACCENT), 0);
  lv_obj_set_style_border_width(s_zoomBox, 2, 0);
  lv_obj_set_style_radius(s_zoomBox, 3, 0);
  lv_obj_add_flag(s_zoomBox, LV_OBJ_FLAG_HIDDEN);
  s_zoomTag = mkLabel(s_map, "", &font_ua_12, C_MUTED);
  lv_obj_clear_flag(s_zoomTag, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_set_style_bg_color(s_zoomTag, col(C_BG), 0);
  lv_obj_set_style_bg_opa(s_zoomTag, LV_OPA_60, 0);
  lv_obj_set_style_pad_hor(s_zoomTag, 3, 0);
  lv_obj_set_style_radius(s_zoomTag, 3, 0);
  lv_obj_set_pos(s_zoomTag, 2, 2);
  lv_label_set_text(s_zoomTag, zoomName(s_zoom));
  // кнопки зуму — лівий нижній кут (на мапі України там порожньо): «+» над «−», «⌂» праворуч
  s_btnZin = mkZoomBtn(s_map, LV_SYMBOL_PLUS, +1, 2, MAP_H - 2 - 36 - 3 - 36);
  s_btnZout = mkZoomBtn(s_map, LV_SYMBOL_MINUS, -1, 2, MAP_H - 2 - 36);
  s_btnZhome = mkZoomBtn(s_map, LV_SYMBOL_HOME, 0, 2 + 36 + 3, MAP_H - 2 - 36);
  updateZoomBtns();

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

  // джерело/вік даних більше не показуються на мапі (див. «Деталі загрози»)
  s_lblSrc = nullptr;
  if (s_btnZin) { lv_obj_move_foreground(s_btnZin); lv_obj_move_foreground(s_btnZout); lv_obj_move_foreground(s_btnZhome); }

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
  lv_obj_set_width(s_cardTitle, 290);
  lv_label_set_long_mode(s_cardTitle, LV_LABEL_LONG_DOT);
  lv_obj_set_pos(s_cardTitle, 14, 3);
  s_cardText = mkLabel(s_card, "", &font_ua_12, C_MUTED);
  lv_obj_set_width(s_cardText, 290);
  lv_label_set_long_mode(s_cardText, LV_LABEL_LONG_DOT);   // без бігучого рядка (він гальмував)
  lv_obj_set_pos(s_cardText, 14, 26);

  // рамка рівня поверх усього екрана (не перехоплює дотики): помаранчевий — тонка, червоний — товста, пульсує
  s_frame = lv_obj_create(scr);
  lv_obj_remove_style_all(s_frame);
  lv_obj_set_size(s_frame, 320, 240);
  lv_obj_set_pos(s_frame, 0, 0);
  lv_obj_set_style_border_side(s_frame, LV_BORDER_SIDE_FULL, 0);
  lv_obj_set_style_radius(s_frame, 6, 0);
  lv_obj_clear_flag(s_frame, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_clear_flag(s_frame, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_add_flag(s_frame, LV_OBJ_FLAG_IGNORE_LAYOUT);
  if (s_pulseT) lv_timer_del(s_pulseT);
  s_pulseT = lv_timer_create(pulseTimerCb, RED_PULSE_MS, nullptr);

  s_seenVersion = UINT32_MAX;
  updateMain(true);
  loadScreen(scr);
}

static void updateMain(bool full) {
  if (!s_main) return;
  // віджети чіпаємо лише при реальній зміні — кожен set_text/set_style інвалідовує область і тягне перемальовку
  static int lastMin = -1, lastWifi = -1;
  if (full) { lastMin = -1; lastWifi = -1; }
  struct tm tm;
  if (getLocalTime(&tm, 0) && tm.tm_hour * 60 + tm.tm_min != lastMin) {
    lastMin = tm.tm_hour * 60 + tm.tm_min;
    lv_label_set_text_fmt(s_lblTime, "%02d:%02d", tm.tm_hour, tm.tm_min);
  }
  int wifi = WiFi.status() == WL_CONNECTED;
  if (wifi != lastWifi) { lastWifi = wifi; lv_obj_set_style_text_color(s_lblWifi, col(wifi ? C_GREEN : C_RED), 0); }
  static int lastBat = -2;
  if (full) lastBat = -2;
  int mv = hwBatteryMv(), pct = mv < 0 ? -1 : hwBatteryPct(mv);
  if (pct != lastBat) {
    if (pct < 0) { lv_obj_add_flag(s_lblBat, LV_OBJ_FLAG_HIDDEN); lv_obj_set_width(s_lblPlace, 196); }
    else {
      const char* ic = pct > 80 ? LV_SYMBOL_BATTERY_FULL : pct > 55 ? LV_SYMBOL_BATTERY_3 : pct > 30 ? LV_SYMBOL_BATTERY_2
                     : pct > 10 ? LV_SYMBOL_BATTERY_1 : LV_SYMBOL_BATTERY_EMPTY;
      lv_label_set_text_fmt(s_lblBat, "%s%d%%", ic, pct);
      lv_obj_set_style_text_color(s_lblBat, col(pct > 50 ? C_GREEN : pct > 20 ? C_YELLOW : C_RED), 0);
      lv_obj_clear_flag(s_lblBat, LV_OBJ_FLAG_HIDDEN);
      if (lastBat < 0) lv_obj_set_width(s_lblPlace, 148);
    }
    lastBat = pct;
  }

  static AppState snap;   // копія стану — лише коли мережева задача опублікувала нову версію
  stateLock();
  uint32_t ver = g_state.version;
  bool changed = full || ver != s_seenVersion;
  if (changed) snap = g_state;
  stateUnlock();

  if (changed) {
    s_seenVersion = ver;
    memcpy(s_ob, snap.oblasts, sizeof(s_ob));
    memcpy(s_rg, snap.regions, sizeof(s_rg));
    s_out = snap.outage;
    s_ntgt = snap.trkOk ? snap.ntgt : 0;
    memcpy(s_tgt, snap.tgt, sizeof(s_tgt));
    s_heat = snap.heat;
    lv_obj_invalidate(s_map);
    // Велике число — кількість ОБЛАСТЕЙ з тривогою (повністю або частково), як на alerts.in.ua.
    // Раніше при районних даних тут рахувались райони (до ~50 з 139 — разом із Кримом/Донеччиною/Луганщиною).
    int cnt = 0;
    for (int i = 0; i < OBLAST_COUNT; i++) if (s_ob[i] == 'A' || s_ob[i] == 'P' || s_ob[i] == 'R' || s_ob[i] == 'Y') cnt++;
    lv_label_set_text(s_lblCountSub, "областей");
    if (snap.alertsOk) lv_label_set_text_fmt(s_lblCount, "%d", cnt);
    else lv_label_set_text(s_lblCount, "?");

    bool preview = s_previewUntil && (int32_t)(millis() - s_previewUntil) < 0;
    Level L = preview ? s_preview : snap.level;
    applyLevelTint(L);
    rebuildPalette();
    String ln[4];
    cardLines(snap, L, ln);
    // картка: статично 2 з 4 рядків (тип + куди; ймовірність поруч або ймовірна ціль), повністю — у «Деталях»
    lv_label_set_text(s_cardTitle, preview ? "Тест кольору екрана" : ln[1].c_str());
    lv_label_set_text(s_cardText, (L > LVL_GREEN && L != LVL_RED && snap.trkOk ? ln[2] : ln[3]).c_str());
  }
  autoZoom(snap);
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
      static const uint32_t sc[] = {C_TXT_ON, C_TXT_OFF, C_OUT_MB};
      lv_label_set_text(s_outState, st[cur]);
      lv_obj_set_style_text_color(s_outState, col(sc[cur]), 0);
      if (at) lv_label_set_text_fmt(s_outTime, "до %s", fmtTime(at).c_str());
      else lv_label_set_text(s_outTime, cur == OUT_ON ? "без відкл." : "—");
    }
    if (changed || cur != OUT_NONE) lv_obj_invalidate(s_outBar);
  }
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
  static AppState s; stateLock(); s = g_state; stateUnlock();   // static: AppState завеликий для стеку
  String ln[4];
  cardLines(s, s.level, ln);
  lv_obj_set_style_pad_row(cont, 10, 0);
  for (int i = 0; i < 4; i++) {
    if (!ln[i].length()) continue;
    lv_obj_t* l = mkLabel(cont, ln[i].c_str(), i == 1 ? &font_ua_18 : &font_ua_14, i == 1 ? levelColor(s.level) : C_TEXT);
    lv_obj_set_width(l, 300); lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
  }
  loadScreen(scr);
}

// ======================================================================================
//                     Стан сповіщень (без модальних вікон — лише колір екрана)
// ======================================================================================
static Level s_lastLevel = LVL_GREEN;
static time_t s_lastAlertTs = 0;

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
// тест: 8 с головний екран у кольорі рівня + відповідний звук/LED, потім — повернення до реального стану
static void testLevel(Level L) { s_preview = L; s_previewUntil = millis() + 8000; notifyTest(L); showMain(); }
static void testYCb(lv_event_t*) { testLevel(LVL_YELLOW); }
static void testOCb(lv_event_t*) { testLevel(LVL_ORANGE); }
static void testRCb(lv_event_t*) { testLevel(LVL_RED); }
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

#define STR_(x) #x
#define STR2_(x) STR_(x)
#define LV_STRINGIFY_PIN STR2_(BAT_ADC_PIN)
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

  lv_obj_t* r = settingRow(list, "");
  lv_obj_move_to_index(r, 0);   // яскравість — першим рядком налаштувань
  static lv_obj_t* s_brLbl;
  s_brLbl = lv_obj_get_child(r, 0);
  int pct = (g_set.brightness * 100 + 127) / 255;
  lv_label_set_text_fmt(s_brLbl, "Яскравість %d%%", pct);
  lv_obj_t* sl = lv_slider_create(r);
  lv_obj_set_width(sl, 120);
  lv_obj_align(sl, LV_ALIGN_RIGHT_MID, -8, 0);
  lv_obj_set_ext_click_area(sl, 10);
  lv_slider_set_range(sl, 10, 100);
  lv_slider_set_value(sl, pct < 10 ? 10 : pct, LV_ANIM_OFF);
  lv_obj_add_event_cb(sl, [](lv_event_t* e) {
    int v = lv_slider_get_value(lv_event_get_target(e));
    g_set.brightness = (v * 255 + 50) / 100; hwSetBrightness(g_set.brightness);
    lv_label_set_text_fmt(s_brLbl, "Яскравість %d%%", v);
  }, LV_EVENT_VALUE_CHANGED, nullptr);
  lv_obj_add_event_cb(sl, [](lv_event_t*) { settingsSave(); }, LV_EVENT_RELEASED, nullptr);

  r = settingRow(list, "Звук сирени");
  lv_obj_t* sw = lv_switch_create(r);
  lv_obj_align(sw, LV_ALIGN_RIGHT_MID, 0, 0);
  if (g_set.sound) lv_obj_add_state(sw, LV_STATE_CHECKED);
  lv_obj_add_event_cb(sw, [](lv_event_t* e) {
    g_set.sound = lv_obj_has_state(lv_event_get_target(e), LV_STATE_CHECKED); settingsSave();
  }, LV_EVENT_VALUE_CHANGED, nullptr);

  r = settingRow(list, "Сигнал на жовтому рівні");
  sw = lv_switch_create(r);
  lv_obj_align(sw, LV_ALIGN_RIGHT_MID, 0, 0);
  if (g_set.alertOnYellow) lv_obj_add_state(sw, LV_STATE_CHECKED);
  lv_obj_add_event_cb(sw, [](lv_event_t* e) {
    g_set.alertOnYellow = lv_obj_has_state(lv_event_get_target(e), LV_STATE_CHECKED); settingsSave();
  }, LV_EVENT_VALUE_CHANGED, nullptr);

#if BAT_ADC_PIN >= 0
  r = settingRow(list, "Заряд АКБ (GPIO" LV_STRINGIFY_PIN ")");
  sw = lv_switch_create(r);
  lv_obj_align(sw, LV_ALIGN_RIGHT_MID, 0, 0);
  if (g_set.battery) lv_obj_add_state(sw, LV_STATE_CHECKED);
  lv_obj_add_event_cb(sw, [](lv_event_t* e) {
    g_set.battery = lv_obj_has_state(lv_event_get_target(e), LV_STATE_CHECKED); settingsSave();
    if (g_set.battery) {
      int mv = hwBatteryMv();
      if (mv < 0) toast("АКБ не знайдено: перевірте дільник на GPIO" LV_STRINGIFY_PIN);
      else { static char b[48]; snprintf(b, sizeof b, "АКБ %d.%02d В · %d%%", mv / 1000, mv % 1000 / 10, hwBatteryPct(mv)); toast(b); }
    }
  }, LV_EVENT_VALUE_CHANGED, nullptr);
#endif

  r = settingRow(list, "Автозум мапи до загрози");
  sw = lv_switch_create(r);
  lv_obj_align(sw, LV_ALIGN_RIGHT_MID, 0, 0);
  if (g_set.autoZoom) lv_obj_add_state(sw, LV_STATE_CHECKED);
  lv_obj_add_event_cb(sw, [](lv_event_t* e) {
    g_set.autoZoom = lv_obj_has_state(lv_event_get_target(e), LV_STATE_CHECKED); settingsSave();
  }, LV_EVENT_VALUE_CHANGED, nullptr);

  r = settingRow(list, "Типові маршрути (зум)");
  sw = lv_switch_create(r);
  lv_obj_align(sw, LV_ALIGN_RIGHT_MID, 0, 0);
  if (g_set.showRoutes) lv_obj_add_state(sw, LV_STATE_CHECKED);
  lv_obj_add_event_cb(sw, [](lv_event_t* e) {
    g_set.showRoutes = lv_obj_has_state(lv_event_get_target(e), LV_STATE_CHECKED); settingsSave();
  }, LV_EVENT_VALUE_CHANGED, nullptr);

  r = settingRow(list, "Автооновлення");
  sw = lv_switch_create(r);
  lv_obj_align(sw, LV_ALIGN_RIGHT_MID, 0, 0);
  if (g_set.autoUpdate) lv_obj_add_state(sw, LV_STATE_CHECKED);
  lv_obj_add_event_cb(sw, [](lv_event_t* e) {
    g_set.autoUpdate = lv_obj_has_state(lv_event_get_target(e), LV_STATE_CHECKED); settingsSave();
  }, LV_EVENT_VALUE_CHANGED, nullptr);
  String up = String("Перевірити оновлення · v") + FW_VERSION;
  if (g_ota.remote[0]) up += otaCompare(g_ota.remote, FW_VERSION) > 0 ? String(" → v") + g_ota.remote : String(" (остання)");
  mkListBtn(list, LV_SYMBOL_REFRESH, up.c_str(), [](lv_event_t*) { otaRequestCheck(); toast("Перевіряю оновлення…"); }, nullptr);

  mkListBtn(list, LV_SYMBOL_PLAY, "Тест: загроза в області (жовтий)", testYCb, nullptr);
  mkListBtn(list, LV_SYMBOL_PLAY, "Тест: курс на місто (помаранчевий)", testOCb, nullptr);
  mkListBtn(list, LV_SYMBOL_PLAY, "Тест: небезпека поруч (червоний)", testRCb, nullptr);
  mkListBtn(list, LV_SYMBOL_TRASH, "Скинути всі налаштування", resetCb, nullptr);
  String info = "IP " + WiFi.localIP().toString() + " · RAM " + String(ESP.getFreeHeap() / 1024) + " КБ · v" FW_VERSION " · " FW_ENV;
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
    static const char* nm[] = {"#34C759 Світло є#", "#FF3B30 Світла немає#", "#F59E0B Можливе відключення#"};
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
  lv_label_set_recolor(s_outSum, true);   // статус кольором: червоний/зелений/жовтий
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

// ------------------------------- OTA: екран прогресу -------------------------------
static lv_obj_t *s_otaBox = nullptr, *s_otaBar = nullptr, *s_otaLbl = nullptr;
static uint32_t s_otaSeq = 0;
static void otaUi() {
  uint8_t st = g_ota.state;
  bool busy = st == OTA_DOWNLOADING || st == OTA_REBOOTING;
  if (busy && !s_otaBox) {
    s_otaBox = lv_obj_create(lv_layer_top());
    lv_obj_set_size(s_otaBox, 320, 240);
    lv_obj_set_pos(s_otaBox, 0, 0);
    lv_obj_set_style_bg_color(s_otaBox, col(C_BG), 0);
    lv_obj_set_style_bg_opa(s_otaBox, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(s_otaBox, 0, 0);
    lv_obj_set_style_radius(s_otaBox, 0, 0);
    lv_obj_clear_flag(s_otaBox, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t* ic = mkLabel(s_otaBox, LV_SYMBOL_DOWNLOAD, &lv_font_montserrat_26, C_ACCENT);
    lv_obj_align(ic, LV_ALIGN_CENTER, 0, -62);
    String t = String("Оновлення прошивки\nv") + FW_VERSION + " → v" + g_ota.remote;
    lv_obj_t* h = mkLabel(s_otaBox, t.c_str(), &font_ua_18, C_TEXT);
    lv_obj_set_style_text_align(h, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(h, LV_ALIGN_CENTER, 0, -20);
    s_otaBar = lv_bar_create(s_otaBox);
    lv_obj_set_size(s_otaBar, 240, 14);
    lv_obj_align(s_otaBar, LV_ALIGN_CENTER, 0, 22);
    lv_obj_set_style_bg_color(s_otaBar, col(C_PANEL2), 0);
    lv_obj_set_style_bg_color(s_otaBar, col(C_ACCENT), LV_PART_INDICATOR);
    lv_bar_set_range(s_otaBar, 0, 100);
    s_otaLbl = mkLabel(s_otaBox, "0%", &font_ua_14, C_MUTED);
    lv_obj_align(s_otaLbl, LV_ALIGN_CENTER, 0, 48);
    lv_obj_t* n = mkLabel(s_otaBox, "Не вимикайте живлення", &font_ua_12, C_MUTED);
    lv_obj_align(n, LV_ALIGN_BOTTOM_MID, 0, -14);
  }
  if (s_otaBox) {
    if (!busy) { lv_obj_del(s_otaBox); s_otaBox = s_otaBar = s_otaLbl = nullptr; }
    else {
      lv_bar_set_value(s_otaBar, g_ota.pct, LV_ANIM_OFF);
      char b[48];
      if (st == OTA_REBOOTING) snprintf(b, sizeof(b), "Готово · перезапуск…");
      else snprintf(b, sizeof(b), "%u%%", (unsigned)g_ota.pct);
      lv_label_set_text(s_otaLbl, b);
    }
  }
  uint32_t seq = g_ota.msgSeq;
  if (seq != s_otaSeq) {
    s_otaSeq = seq;
    if (!busy && g_ota.msg[0]) toast(g_ota.msg);
  }
}

void uiLoop() {
  uint32_t now = millis();
  if (now - s_lastUi < 250) return;
  s_lastUi = now;
  otaUi();

  // якщо Wi-Fi відвалився вже після налаштування — просто чекаємо автоперепідключення
  stateLock();
  Level L = g_state.level;
  bool near = g_state.tgNear && g_state.tgLevel == L;
  time_t ts = g_state.tgTime;
  char text[TG_TEXT_MAX]; strlcpy(text, g_state.tgText, sizeof(text));
  char reason[120]; strlcpy(reason, g_state.tgReason, sizeof(reason));
  stateUnlock();

  // тест кольору завершився — повертаємо реальний рівень
  if (s_previewUntil && (int32_t)(now - s_previewUntil) >= 0) {
    s_previewUntil = 0;
    notifyLevel(L, near); notifySilence();
    if (s_kind == SK_MAIN) updateMain(true);
  }
  bool onInfo = s_kind == SK_MAIN || s_kind == SK_DETAIL || s_kind == SK_OUTAGE;
  if (!s_previewUntil && g_set.configured() && onInfo) {
    if (L > s_lastLevel) {   // ескалація: звук/LED за рівнем, екран сам перефарбовується
      notifyLevel(L, near);
      if (L >= LVL_ORANGE && s_kind != SK_MAIN) showMain();   // з деталей/графіка — назад на кольоровий головний
      s_lastAlertTs = ts;
    } else if (L == LVL_RED && ts && ts != s_lastAlertTs) {  // новий пост про небезпеку поруч — сирена ще раз
      notifyLevel(L, near);
      s_lastAlertTs = ts;
    } else if (L < s_lastLevel) {
      notifyLevel(L, near);
      if (L == LVL_GREEN) toast("Відбій · чисто", C_GREEN);
      else toast((String("Рівень знижено: ") + levelName(L)).c_str(), levelColor(L));
    }
    s_lastLevel = L;
  }
  if (s_kind == SK_OUTAGE) outageTick();
  if (s_kind == SK_MAIN) updateMain(false);
}
