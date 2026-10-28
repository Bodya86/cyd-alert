#include "app.h"
#include <Preferences.h>

Settings g_set;
AppState g_state;
static SemaphoreHandle_t s_mtx;

void stateLock() { if (!s_mtx) s_mtx = xSemaphoreCreateRecursiveMutex(); xSemaphoreTakeRecursive(s_mtx, portMAX_DELAY); }
void stateUnlock() { xSemaphoreGiveRecursive(s_mtx); }

const char* levelName(Level l) {
  switch (l) { case LVL_RED: return "РАКЕТИ"; case LVL_YELLOW: return "ДРОНИ"; default: return "ЧИСТО"; }
}

void settingsLoad() {
  Preferences p;
  p.begin("cydalert", true);
  g_set.ssid = p.getString("ssid", "");
  g_set.pass = p.getString("pass", "");
  g_set.token = p.getString("token", DEFAULT_ALERTS_TOKEN);
  g_set.server = p.getString("server", DEFAULT_SERVER_URL);
  g_set.channel = p.getString("channel", "");
  g_set.oblast = p.getChar("oblast", -1);
  g_set.city = p.getShort("city", -1);
  g_set.district = p.getShort("district", -1);
  g_set.brightness = p.getUChar("bright", 200);
  g_set.sound = p.getBool("sound", true);
  g_set.alertOnYellow = p.getBool("onyellow", true);
  g_set.outMode = p.getUChar("outmode", OUT_MODE_AUTO);
  g_set.outGroup = p.getChar("outgroup", -1);
  g_set.outProv = p.getChar("outprov", -1);
  g_set.outMask = p.getULong64("outmask", 0);
  p.end();
  if (g_set.outMode > OUT_MODE_MANUAL) g_set.outMode = OUT_MODE_AUTO;
  if (g_set.outGroup >= OUTAGE_GROUP_COUNT) g_set.outGroup = -1;
  if (g_set.outProv >= OUTAGE_PROV_COUNT) g_set.outProv = -1;
  if (g_set.oblast >= OBLAST_COUNT) g_set.oblast = -1;
  if (g_set.city >= CITY_COUNT) g_set.city = -1;
  memset(g_state.oblasts, '?', OBLAST_COUNT);
  g_state.oblasts[OBLAST_COUNT] = 0;
  memset(g_state.regions, '?', MAP_REGION_COUNT);
  g_state.regions[MAP_REGION_COUNT] = 0;
}

void settingsSave() {
  Preferences p;
  p.begin("cydalert", false);
  p.putString("ssid", g_set.ssid);
  p.putString("pass", g_set.pass);
  p.putString("token", g_set.token);
  p.putString("server", g_set.server);
  p.putString("channel", g_set.channel);
  p.putChar("oblast", g_set.oblast);
  p.putShort("city", g_set.city);
  p.putShort("district", g_set.district);
  p.putUChar("bright", g_set.brightness);
  p.putBool("sound", g_set.sound);
  p.putBool("onyellow", g_set.alertOnYellow);
  p.putUChar("outmode", g_set.outMode);
  p.putChar("outgroup", g_set.outGroup);
  p.putChar("outprov", g_set.outProv);
  p.putULong64("outmask", g_set.outMask);
  p.end();
}

void settingsReset() {
  Preferences p;
  p.begin("cydalert", false);
  p.clear();
  p.end();
}

const CityDef* userCity() { return (g_set.city >= 0 && g_set.city < CITY_COUNT) ? &CITIES[g_set.city] : nullptr; }
const DistrictDef* userDistrict() {
  const CityDef* c = userCity();
  return (c && g_set.district >= 0 && g_set.district < c->ndist) ? &c->districts[g_set.district] : nullptr;
}
String userChannel() {
  if (g_set.channel.length()) return g_set.channel;
  const CityDef* c = userCity();
  return c ? String(c->channel) : String();
}
String userPlaceName() {
  const CityDef* c = userCity();
  if (!c) return g_set.oblast >= 0 ? String(OBLASTS[g_set.oblast].name) : String("Локацію не обрано");
  String s = c->name;
  const DistrictDef* d = userDistrict();
  if (d) { s += " · "; s += d->name; }
  return s;
}

char userOblastStatus() { return g_set.oblast >= 0 ? g_state.oblasts[g_set.oblast] : '?'; }

int userRegion() {
  const CityDef* c = userCity();
  return (c && c->region >= 0 && c->region < MAP_REGION_COUNT) ? c->region : -1;
}

void regionsFromOblasts(AppState& s) {
  for (int i = 0; i < MAP_REGION_COUNT; i++) {
    char o = s.oblasts[MAP_REGIONS[i].oblast];
    s.regions[i] = (o == 'A' || o == 'R' || o == 'Y') ? 'Y' : o == 'P' ? 'y' : o == 'N' ? '.' : '?';
  }
  s.regionsFine = false;
}

static Level regionLevel(char c) { return (c == 'R' || c == 'r') ? LVL_RED : (c == 'Y' || c == 'y') ? LVL_YELLOW : LVL_GREEN; }

// Підсумковий рівень для адреси користувача:
//  • офіційна тривога у вашому районі (або області, якщо район невідомий) — рівень за типом загрози
//    (ракети/КАБ/авіація — ЧЕРВОНИЙ, дрони або невідомий тип — ЖОВТИЙ); Telegram може лише підвищити
//    (напр. «ракета на Кременчук» → ЧЕРВОНИЙ);
//  • без офіційної тривоги — рівень Telegram, тільки якщо бал >= 5 (ціль поруч), інакше ЗЕЛЕНИЙ.
Level computeFinalLevel(AppState& s) {
  Level off = LVL_GREEN; bool part = false;
  int r = userRegion();
  if (r >= 0 && s.regions[r] != '?') {
    off = regionLevel(s.regions[r]);
    part = s.regions[r] == 'y' || s.regions[r] == 'r';
  } else if (g_set.oblast >= 0) {
    // вся область: найвищий рівень серед її районів
    bool any = false, all = true;
    for (int i = 0; i < MAP_REGION_COUNT; i++) {
      if (MAP_REGIONS[i].oblast != g_set.oblast) continue;
      Level l = regionLevel(s.regions[i]);
      if (l > off) off = l;
      if (l > LVL_GREEN) any = true;
      if (l == LVL_GREEN || s.regions[i] == 'y' || s.regions[i] == 'r') all = false;
    }
    part = any && !all;
  }
  s.offLevel = off;
  s.offPartial = part;
  Level tg = s.tgLevel;
  Level res = off > LVL_GREEN ? (tg > off ? tg : off) : (s.tgScore >= 5 ? tg : LVL_GREEN);
  if (s.trkOk && s.trkLevel > res) res = s.trkLevel;   // трекер сервера: P(ціль пролетить поруч) вище порогу
  return res;
}
