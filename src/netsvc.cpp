// Фонова мережева задача: тривоги по районах (alerts.in.ua / ubilling / companion-сервер), Telegram,
// графік відключень світла
#include "app.h"
#include "analyzer.h"
#include "tgscrape.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>

static volatile bool s_force = false, s_outForce = true;
static String s_lmActive, s_lmIot;             // Last-Modified окремо для кожного ендпоінта
static TgPost s_posts[TG_MAX_POSTS];
static uint32_t s_srvAt = 0;  // останній успішний запит до сервера

void netForceRefresh() { s_force = true; s_outForce = true; }
void netOutageRefresh() { s_outForce = true; }

// ----------------------------------------------------------------------------------------------
// HTTP: тіло цілком (body) або потоковий розбір JSON (doc + filter)
struct Req {
  HTTPClient http;
  WiFiClientSecure sec;
  WiFiClient plain;
  int code = 0;
  bool begin(const String& url, const char* bearer = nullptr, String* lastMod = nullptr) {
    bool https = url.startsWith("https");
    if (https) { sec.setInsecure(); sec.setTimeout(12); }
    if (!(https ? http.begin(sec, url) : http.begin(plain, url))) return false;
    http.setTimeout(12000);
    http.useHTTP10(true);   // без chunked — можна читати потік
    http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
    http.setUserAgent("CYD-Alert/1.1 (ESP32)");
    if (bearer && bearer[0]) http.addHeader("Authorization", String("Bearer ") + bearer);
    if (lastMod && lastMod->length()) http.addHeader("If-Modified-Since", *lastMod);
    static const char* hdrs[] = {"Last-Modified"};
    http.collectHeaders(hdrs, 1);
    code = http.GET();
    if (code == 200 && lastMod) *lastMod = http.header("Last-Modified");
    return code == 200;
  }
  ~Req() { http.end(); }
};

static bool httpGet(const String& url, String& body, const char* bearer = nullptr, int* codeOut = nullptr,
                    String* lastMod = nullptr) {
  Req r;
  bool ok = r.begin(url, bearer, lastMod);
  if (codeOut) *codeOut = r.code;
  if (ok) body = r.http.getString();
  return ok;
}

static bool httpJson(const String& url, JsonDocument& doc, const JsonDocument& filter, const char* bearer = nullptr,
                     int* codeOut = nullptr, String* lastMod = nullptr) {
  Req r;
  bool ok = r.begin(url, bearer, lastMod);
  if (codeOut) *codeOut = r.code;
  if (!ok) return false;
  DeserializationError e = deserializeJson(doc, r.http.getStream(), DeserializationOption::Filter(filter),
                                           DeserializationOption::NestingLimit(12));
  if (e) Serial.printf("[json] %s: %s\n", url.c_str(), e.c_str());
  return !e;
}

// ----------------------------------------------------------------------------------------------
static void publishAlerts(const char* obl, const char* regions, bool fine, const char* src) {
  stateLock();
  memcpy(g_state.oblasts, obl, OBLAST_COUNT);
  if (regions) { memcpy(g_state.regions, regions, MAP_REGION_COUNT); g_state.regionsFine = fine; }
  else regionsFromOblasts(g_state);
  g_state.alertsOk = true;
  g_state.alertsAt = millis();
  g_state.alertsSrc = src;
  g_state.level = computeFinalLevel(g_state);
  g_state.version++;
  stateUnlock();
}

static int oblastByUid(int uid) {
  for (int i = 0; i < OBLAST_COUNT; i++) if (OBLASTS[i].uid == uid) return i;
  return -1;
}

// порівняння назв районів без апострофів і без слова " район"
static bool raionEq(const char* a, const char* b) {
  auto skip = [](const char*& p) {
    for (;;) {
      if (*p == '\'' || *p == '`') { p++; continue; }
      if ((uint8_t)p[0] == 0xE2 && (uint8_t)p[1] == 0x80 && (uint8_t)p[2] == 0x99) { p += 3; continue; }  // ’
      if ((uint8_t)p[0] == 0xCA && (uint8_t)p[1] == 0xBC) { p += 2; continue; }                          // ʼ
      if (!strncmp(p, " район", strlen(" район"))) { p += strlen(" район"); continue; }
      break;
    }
  };
  for (;;) {
    skip(a); skip(b);
    if (!*a || !*b) return !*a && !*b;
    if (*a != *b) return false;
    a++; b++;
  }
}

// alert_type + threats[] → 1 (жовтий: дрони/невідомо) або 2 (червоний: ракети/КАБ/авіація/обстріли)
static uint8_t alertKind(JsonObject a) {
  const char* t = a["alert_type"] | "air_raid";
  if (strcmp(t, "air_raid")) return 2;   // artillery_shelling, urban_fights, chemical, nuclear
  static const char* RED[] = {"ballistic_missiles", "cruise_missiles", "unspecified_missiles", "mig31k_departure",
                              "guided_aerial_bombs", "tactic_aircraft_activity", "strategic_aircraft_activity"};
  auto isRed = [&](const char* tt) {
    if (!tt) return false;
    for (auto r : RED) if (!strcmp(tt, r)) return true;
    return false;
  };
  if (isRed(a["threat_type"] | (const char*)nullptr)) return 2;
  for (JsonVariant th : a["threats"].as<JsonArray>())
    if (isRed(th["threat_type"] | (const char*)nullptr) || isRed(th.as<const char*>())) return 2;
  return 1;
}

// /v1/alerts/active.json — з точністю до району і з типом загрози
static int fetchAlertsActive() {
  JsonDocument filter;
  JsonObject f = filter["alerts"].add<JsonObject>();
  for (const char* k : {"location_type", "location_uid", "location_oblast_uid", "location_raion", "alert_type",
                        "finished_at", "threat_type"})
    f[k] = true;
  f["threats"].add<JsonObject>()["threat_type"] = true;
  JsonDocument doc;
  int code = 0;
  bool ok = httpJson("https://api.alerts.in.ua/v1/alerts/active.json", doc, filter, g_set.token.c_str(), &code, &s_lmActive);
  if (code == 304) { stateLock(); g_state.alertsAt = millis(); stateUnlock(); return 1; }
  if (!ok) { Serial.printf("[alerts.in.ua active] HTTP %d\n", code); s_lmActive = ""; return 0; }

  static uint8_t lvl[MAP_REGION_COUNT];
  static bool part[MAP_REGION_COUNT];
  memset(lvl, 0, sizeof(lvl)); memset(part, 0, sizeof(part));
  char obl[OBLAST_COUNT + 1];
  memset(obl, 'N', OBLAST_COUNT); obl[OBLAST_COUNT] = 0;
  auto mark = [&](int i, uint8_t L, bool partial) {
    if (partial) { if (!lvl[i] || part[i]) { if (L > lvl[i]) lvl[i] = L; part[i] = true; } }
    else { if (part[i]) { lvl[i] = L > lvl[i] ? L : lvl[i]; part[i] = false; } else if (L > lvl[i]) lvl[i] = L; }
  };
  for (JsonObject a : doc["alerts"].as<JsonArray>()) {
    if (!a["finished_at"].isNull()) continue;
    uint8_t L = alertKind(a);
    const char* lt = a["location_type"] | "";
    auto num = [](JsonVariant v) { return v.is<const char*>() ? atoi(v.as<const char*>()) : v.as<int>(); };
    int uid = num(a["location_uid"]);
    int ob = oblastByUid(num(a["location_oblast_uid"]));
    int obu = oblastByUid(uid);
    if (!strcmp(lt, "oblast") || (obu >= 0 && strcmp(lt, "raion"))) {
      int o = obu >= 0 ? obu : ob;
      for (int i = 0; i < MAP_REGION_COUNT; i++) if (MAP_REGIONS[i].oblast == o) mark(i, L, false);
      if (o >= 0) obl[o] = 'A';
      continue;
    }
    bool hit = false;
    if (!strcmp(lt, "raion"))
      for (int i = 0; i < MAP_REGION_COUNT; i++)
        if (MAP_REGIONS[i].uid == uid) { mark(i, L, false); hit = true; }
    if (!hit) {   // громада / місто — частково по району
      const char* rn = a["location_raion"] | "";
      for (int i = 0; i < MAP_REGION_COUNT && rn[0]; i++)
        if (MAP_REGIONS[i].oblast == ob && raionEq(MAP_REGIONS[i].name, rn)) { mark(i, L, true); hit = true; }
      if (!hit)
        for (int i = 0; i < MAP_REGION_COUNT; i++) if (MAP_REGIONS[i].oblast == ob) mark(i, L, true);
    }
    if (ob >= 0 && obl[ob] == 'N') obl[ob] = 'P';
  }
  char rs[MAP_REGION_COUNT + 1];
  for (int i = 0; i < MAP_REGION_COUNT; i++)
    rs[i] = !lvl[i] ? '.' : lvl[i] == 2 ? (part[i] ? 'r' : 'R') : (part[i] ? 'y' : 'Y');
  rs[MAP_REGION_COUNT] = 0;
  publishAlerts(obl, rs, true, "alerts.in.ua");
  return 1;
}

// запасний варіант з токеном: IoT-рядок по областях (тип загрози невідомий)
static bool fetchAlertsIot() {
  String body; int code = 0;
  bool ok = httpGet("https://api.alerts.in.ua/v2/iot/active_air_raid_alerts_by_oblast.json", body, g_set.token.c_str(), &code, &s_lmIot);
  if (code == 304) { stateLock(); g_state.alertsAt = millis(); stateUnlock(); return true; }
  if (!ok) { Serial.printf("[alerts.in.ua iot] HTTP %d\n", code); s_lmIot = ""; return false; }
  int q = body.indexOf('"');
  if (q < 0 || (int)body.length() < q + 1 + OBLAST_COUNT) return false;
  char st[OBLAST_COUNT + 1];
  for (int i = 0; i < OBLAST_COUNT; i++) {
    char c = body[q + 1 + i];
    st[i] = (c == 'A' || c == 'R' || c == 'Y' || c == 'P' || c == 'N') ? c : '?';
  }
  publishAlerts(st, nullptr, false, "alerts.in.ua");
  return true;
}

static bool fetchUbilling() {
  String body;
  if (!httpGet("https://ubilling.net.ua/aerialalerts/", body)) return false;
  JsonDocument doc;
  if (deserializeJson(doc, body)) return false;
  JsonObject states = doc["states"];
  if (states.isNull()) return false;
  char st[OBLAST_COUNT + 1];
  for (int i = 0; i < OBLAST_COUNT; i++) {
    const char* key = OBLASTS[i].ubilling;
    if (!key[0]) { st[i] = 'A'; continue; }  // АР Крим — окупована, постійна тривога
    JsonVariant v = states[key];
    st[i] = v.isNull() ? '?' : (v["alertnow"].as<bool>() ? 'A' : 'N');
  }
  publishAlerts(st, nullptr, false, "ubilling.net.ua");
  return true;
}

static Level parseLevel(const char* s) {
  if (!s) return LVL_GREEN;
  if (!strcmp(s, "red")) return LVL_RED;
  if (!strcmp(s, "yellow")) return LVL_YELLOW;
  return LVL_GREEN;
}

static String serverBase() {
  String url = g_set.server;
  if (url.endsWith("/")) url.remove(url.length() - 1);
  return url;
}

// Статичні дані оновлюються GitHub Actions раз на ~5–15 хв: беремо звідти прогноз цілей (фільтри частинок),
// а тривоги й Telegram ESP і далі читає сам напряму (свіжіше). Тривоги зі статики — лише якщо прямі джерела впали.
static uint32_t s_staticAt = 0;
static bool applyStatic(JsonDocument& doc, bool& gotOblasts) {
  time_t gen = (time_t)(doc["generated"] | 0L), nowT = time(nullptr);
  long age = (nowT > 100000 && gen > 0) ? (long)(nowT - gen) : 0;   // с
  bool fresh = age < 20 * 60;
  const char* ob = doc["oblasts"] | "";
  const char* rs = doc["regions"] | "";
  const char* src = doc["oblasts_src"] | "";
  stateLock();
  bool alertsOk = g_state.alertsOk;
  stateUnlock();
  if (!alertsOk && age < 10 * 60 && strlen(ob) >= OBLAST_COUNT) {
    bool fine = strlen(rs) == MAP_REGION_COUNT;
    publishAlerts(ob, fine ? rs : nullptr, fine && strcmp(src, "ubilling") != 0, "GitHub");
    gotOblasts = true;
  }
  stateLock();
  g_state.trkOk = fresh && !doc["p_near"].isNull();
  g_state.ntgt = 0;
  if (g_state.trkOk) {
    g_state.trkLevel = parseLevel(doc["tlevel"] | "green");
    g_state.pNear = doc["p_near"] | 0;
    g_state.pDrone = doc["p_drone"] | 0;
    g_state.pMissile = doc["p_missile"] | 0;
    g_state.pBallistic = doc["ballistic"] | 0;
    g_state.eta = (int8_t)constrain((int)(doc["eta"] | -1), -1, 99);
    for (JsonObject t : doc["tgt"].as<JsonArray>()) {
      if (g_state.ntgt >= MAX_TARGETS) break;
      MapTarget& m = g_state.tgt[g_state.ntgt++];
      const char* ch = t["c"] | "U";
      m.ch = ch[0];
      m.lat = t["la"] | 0.0f; m.lon = t["lo"] | 0.0f;
      m.hd = t["hd"] | -1;
      m.p = constrain((int)(t["p"] | 0), 0, 100);
      m.eta = (int8_t)constrain((int)(t["e"] | -1), -1, 99);
      m.n = constrain((int)(t["n"] | 1), 1, 99);
      m.npt = 0;
      for (JsonArray pt : t["pt"].as<JsonArray>()) {
        if (m.npt >= 3 || pt.size() < 3) break;
        m.pla[m.npt] = pt[0]; m.plo[m.npt] = pt[1]; m.psd[m.npt] = constrain((int)pt[2], 0, 99);
        m.npt++;
      }
    }
  } else {
    g_state.trkLevel = LVL_GREEN; g_state.pNear = g_state.pDrone = g_state.pMissile = g_state.pBallistic = 0;
    g_state.eta = -1;
  }
  g_state.level = computeFinalLevel(g_state);
  g_state.version++;
  stateUnlock();
  s_staticAt = millis();
  Serial.printf("[static] age %ld s, targets %d\n", age, g_state.ntgt);
  return true;
}

// Статичний сервер (GitHub Pages, build_static.py): файли /api/state/<city>[/<district>].json.
// Вмикається, якщо адреса на *.github.io / закінчується на "/static" або динамічний /api/state дав 404.
static bool s_staticDetected = false;
static bool serverIsStatic() {
  String b = serverBase();
  return s_staticDetected || b.indexOf("github.io") >= 0 || b.endsWith("/static");
}

// companion server: GET /api/state?city=..&district=..&oblast=..   (або статичний .json)
static bool fetchServer(bool& gotOblasts) {
  gotOblasts = false;
  const CityDef* c = userCity();
  const DistrictDef* d = userDistrict();
  String body;
  bool isStatic = serverIsStatic();
  if (!isStatic) {
    String url = serverBase() + "/api/state?oblast=" + String(g_set.oblast);
    if (c) url += String("&city=") + c->id;
    if (d) url += String("&district=") + d->id;
    if (g_set.channel.length()) url += "&channel=" + g_set.channel;
    int code = 0;
    if (!httpGet(url, body, nullptr, &code)) {
      if (code != 404) return false;
      s_staticDetected = isStatic = true;
      Serial.println("[server] 404 on /api/state -> static mode");
    }
  }
  if (isStatic) {
    String url = serverBase() + "/api/state";
    if (c) url += String("/") + c->id + (d ? String("/") + d->id : String("")) + ".json";
    else url += ".json";
    if (!httpGet(url, body) && !(c && httpGet(serverBase() + "/api/state.json", body))) return false;
  }
  JsonDocument doc;
  if (deserializeJson(doc, body)) return false;
  if (isStatic) return applyStatic(doc, gotOblasts);
  const char* ob = doc["oblasts"] | "";
  const char* rs = doc["regions"] | "";
  const char* src = doc["oblasts_src"] | "";
  if (strlen(ob) >= OBLAST_COUNT) {
    bool fine = strlen(rs) == MAP_REGION_COUNT;
    publishAlerts(ob, fine ? rs : nullptr, fine && strcmp(src, "ubilling") != 0, "сервер");
    gotOblasts = true;
  }
  stateLock();
  g_state.tgLevel = parseLevel(doc["level"] | "green");
  g_state.tgScore = doc["score"] | 0;
  g_state.tgNear = doc["near"] | false;
  strlcpy(g_state.tgKind, doc["kind"] | "", sizeof(g_state.tgKind));
  strlcpy(g_state.tgText, doc["text"] | "", sizeof(g_state.tgText));
  strlcpy(g_state.tgReason, doc["reason"] | "", sizeof(g_state.tgReason));
  g_state.tgTime = (time_t)(doc["ts"] | 0L);
  g_state.tgOk = true;
  g_state.tgAt = millis();
  g_state.tgSrc = "сервер";
  // трекер цілей
  g_state.trkOk = !doc["p_near"].isNull();
  g_state.trkLevel = parseLevel(doc["tlevel"] | "green");
  g_state.pNear = doc["p_near"] | 0;
  g_state.pDrone = doc["p_drone"] | 0;
  g_state.pMissile = doc["p_missile"] | 0;
  g_state.pBallistic = doc["ballistic"] | 0;
  g_state.eta = (int8_t)constrain((int)(doc["eta"] | -1), -1, 99);
  g_state.ntgt = 0;
  for (JsonObject t : doc["tgt"].as<JsonArray>()) {
    if (g_state.ntgt >= MAX_TARGETS) break;
    MapTarget& m = g_state.tgt[g_state.ntgt++];
    const char* c = t["c"] | "U";
    m.ch = c[0];
    m.lat = t["la"] | 0.0f; m.lon = t["lo"] | 0.0f;
    m.hd = t["hd"] | -1;
    m.p = constrain((int)(t["p"] | 0), 0, 100);
    m.eta = (int8_t)constrain((int)(t["e"] | -1), -1, 99);
    m.n = constrain((int)(t["n"] | 1), 1, 99);
    m.npt = 0;
    for (JsonArray pt : t["pt"].as<JsonArray>()) {
      if (m.npt >= 3 || pt.size() < 3) break;
      m.pla[m.npt] = pt[0]; m.plo[m.npt] = pt[1]; m.psd[m.npt] = constrain((int)pt[2], 0, 99);
      m.npt++;
    }
  }
  s_srvAt = millis();
  g_state.level = computeFinalLevel(g_state);
  g_state.version++;
  stateUnlock();
  return true;
}

static bool fetchTelegramDirect() {
  String ch = userChannel();
  if (!ch.length()) return false;
  int n = tgFetchChannel(ch.c_str(), s_posts, TG_MAX_POSTS);
  Serial.printf("[tg] %s: %d posts, heap %u\n", ch.c_str(), n, ESP.getFreeHeap());
  if (n < 0) return false;
  int best = -1; PostEval ev;
  Level l = analyzeFeed(s_posts, n, userCity(), g_set.district, time(nullptr), &best, &ev);
  stateLock();
  g_state.tgLevel = l;
  g_state.tgScore = ev.score;
  g_state.tgNear = ev.near;
  strlcpy(g_state.tgKind, ev.kind, sizeof(g_state.tgKind));
  if (best >= 0) {
    strlcpy(g_state.tgText, s_posts[best].text, sizeof(g_state.tgText));
    g_state.tgTime = s_posts[best].ts;
    strlcpy(g_state.tgReason, ev.reason, sizeof(g_state.tgReason));
  } else if (n > 0) {
    strlcpy(g_state.tgText, "", sizeof(g_state.tgText));
    g_state.tgTime = 0;
    strlcpy(g_state.tgReason, "немає свіжих повідомлень про загрози", sizeof(g_state.tgReason));
  }
  g_state.tgOk = true;
  g_state.tgAt = millis();
  g_state.tgSrc = "t.me";
  g_state.trkOk = false; g_state.trkLevel = LVL_GREEN; g_state.ntgt = 0; g_state.pNear = 0; g_state.eta = -1;
  g_state.level = computeFinalLevel(g_state);
  g_state.version++;
  stateUnlock();
  return true;
}

// ----------------------------------------------------------------------------------------------
//                                 Графік відключень світла
// ----------------------------------------------------------------------------------------------
static int32_t parseYmd(const char* s) {   // "2026-10-28..." → 20261028
  if (!s || strlen(s) < 10) return 0;
  return atoi(s) * 10000 + atoi(s + 5) * 100 + atoi(s + 8);
}

static void addDay(OutageInfo& o, int32_t ymd, const uint8_t* c) {
  int32_t today = outageYmd(time(nullptr));
  if (ymd < today || o.ndays >= 2) return;   // учорашній день не потрібен
  o.days[o.ndays].ymd = ymd;
  memcpy(o.days[o.ndays].c, c, 48);
  o.ndays++;
}

// формат DTEK / OE_OUTAGE_DATA: fact.data["<epoch дня>"]["GPV3.1"]["1".."24"] = yes/no/first/second/...
static bool fetchOutageGpv(const OutageProvDef& p, const char* group, OutageInfo& o) {
  String key = String("GPV") + group;
  JsonDocument filter;
  filter["fact"]["data"]["*"][key] = true;
  filter["fact"]["update"] = true;
  JsonDocument doc;
  int code = 0;
  if (!httpJson(p.url, doc, filter, nullptr, &code)) { snprintf(o.err, sizeof(o.err), "HTTP %d", code); return false; }
  JsonObject data = doc["fact"]["data"].as<JsonObject>();   // порожній масив [] → немає графіка
  uint8_t c[48];
  for (JsonPair kv : data) {
    JsonObject g = kv.value()[key];
    if (g.isNull()) continue;
    for (int h = 1; h <= 24; h++) {
      char hk[4]; snprintf(hk, sizeof(hk), "%d", h);
      outageGpvCells(g[hk] | "yes", &c[(h - 1) * 2]);
    }
    addDay(o, outageYmdFromDayTs(atol(kv.key().c_str())), c);
  }
  strlcpy(o.updated, doc["fact"]["update"] | "", sizeof(o.updated));
  return true;
}

// YASNO: {"3.1":{"today":{"slots":[{"start":0,"end":450,"type":"NotPlanned"},...],"date":"...","status":"..."},"tomorrow":{...}}}
static bool fetchOutageYasno(const OutageProvDef& p, const char* group, OutageInfo& o) {
  JsonDocument filter;
  filter[group] = true;
  JsonDocument doc;
  int code = 0;
  if (!httpJson(p.url, doc, filter, nullptr, &code)) { snprintf(o.err, sizeof(o.err), "HTTP %d", code); return false; }
  JsonObject g = doc[group];
  for (const char* dk : {"today", "tomorrow"}) {
    JsonObject d = g[dk];
    const char* st = d["status"] | "";
    if (d.isNull() || !strcmp(st, "WaitingForSchedule")) continue;
    uint8_t c[48]; memset(c, OUT_ON, 48);
    for (JsonObject s : d["slots"].as<JsonArray>()) {
      const char* t = s["type"] | "";
      uint8_t v = !strcmp(t, "Definite") ? OUT_OFF : !strcmp(t, "NotPlanned") ? OUT_ON : OUT_MAYBE;
      int a = (s["start"] | 0) / 30, b = ((s["end"] | 0) + 29) / 30;
      for (int i = a; i < b && i < 48; i++) c[i] = v;
    }
    addDay(o, parseYmd(d["date"] | ""), c);
  }
  strlcpy(o.updated, g["updatedOn"] | "", sizeof(o.updated));
  return true;
}

// companion server: /api/outage?oblast=&group=[&prov=]
static bool fetchOutageServer(const char* group, OutageInfo& o) {
  String url;
  if (serverIsStatic()) {
    url = serverBase() + "/api/outage/" + String(g_set.oblast) + "/" + group + ".json";
  } else {
    url = serverBase() + "/api/outage?oblast=" + String(g_set.oblast) + "&group=" + group;
    if (g_set.outProv >= 0) url += String("&prov=") + OUTAGE_PROVS[g_set.outProv].id;
  }
  JsonDocument filter;
  filter["days"][0]["date"] = true;
  filter["days"][0]["cells"] = true;
  filter["src"] = true; filter["updated"] = true; filter["err"] = true;
  JsonDocument doc;
  if (!httpJson(url, doc, filter)) return false;
  for (JsonObject d : doc["days"].as<JsonArray>()) {
    const char* cs = d["cells"] | "";
    if (strlen(cs) != 48) continue;
    uint8_t c[48];
    for (int i = 0; i < 48; i++) c[i] = cs[i] == '1' ? OUT_OFF : cs[i] == '2' ? OUT_MAYBE : OUT_ON;
    addDay(o, parseYmd(d["date"] | ""), c);
  }
  strlcpy(o.src, doc["src"] | "сервер", sizeof(o.src));
  strlcpy(o.updated, doc["updated"] | "", sizeof(o.updated));
  strlcpy(o.err, doc["err"] | "", sizeof(o.err));
  return o.ndays > 0 || !o.err[0];
}

// повертає true при успіху (навіть якщо графіка ще немає)
static bool fetchOutage() {
  static OutageInfo o;
  o = OutageInfo();
  bool ok = false;
  const char* group = OUTAGE_GROUPS[g_set.outGroup];
  if (g_set.server.length() && fetchOutageServer(group, o)) ok = true;
  for (int i = 0; !ok && i < OUTAGE_PROV_COUNT; i++) {
    const OutageProvDef& p = OUTAGE_PROVS[i];
    if (g_set.outProv >= 0 ? i != g_set.outProv : !(p.oblMask & (1UL << g_set.oblast))) continue;
    o = OutageInfo();
    strlcpy(o.src, p.name, sizeof(o.src));
    bool r = p.type == 1 ? fetchOutageYasno(p, group, o) : fetchOutageGpv(p, group, o);
    Serial.printf("[outage] %s %s: %s, %d days, heap %u\n", p.id, group, r ? "ok" : o.err, o.ndays, ESP.getFreeHeap());
    if (r && o.ndays) ok = true;
    else if (r) { snprintf(o.err, sizeof(o.err), "немає графіка для черги %s", group); ok = true; }
  }
  if (!o.src[0]) strlcpy(o.err, "немає джерела для області — задайте графік вручну", sizeof(o.err));
  o.ok = ok && o.ndays > 0;
  o.at = millis();
  stateLock();
  g_state.outage = o;
  g_state.version++;
  stateUnlock();
  return ok;
}

static void outageManualOrOff() {
  stateLock();
  OutageInfo& o = g_state.outage;
  if (g_set.outMode == OUT_MODE_MANUAL) {
    int32_t before = o.ndays ? o.days[0].ymd : 0;
    bool wasManual = o.manual;
    outageFromMask(o, g_set.outMask, time(nullptr));
    o.at = millis();
    if (!wasManual || before != (o.ndays ? o.days[0].ymd : 0)) g_state.version++;
  } else if (o.ndays || o.ok) {
    o = OutageInfo();
    g_state.version++;
  }
  stateUnlock();
}

// ----------------------------------------------------------------------------------------------
static void netTask(void*) {
  uint32_t lastAlerts = 0, lastTg = 0, lastSrv = 0, lastOut = 0, outEvery = OUTAGE_POLL_MS;
  bool first = true;
  int32_t outDay = 0;
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(500));
    if (WiFi.status() != WL_CONNECTED || !g_set.configured()) continue;
    uint32_t now = millis();
    bool force = s_force; s_force = false;
    bool haveServer = g_set.server.length() > 0;
    bool srvOblasts = false;
    uint32_t srvEvery = (haveServer && serverIsStatic()) ? POLL_STATIC_MS : POLL_SERVER_MS;
    if (haveServer && (first || force || now - lastSrv > srvEvery)) {
      lastSrv = now;
      if (!fetchServer(srvOblasts)) Serial.println("[server] fail -> fallback");
    }
    uint32_t alertsEvery = g_set.token.length() ? POLL_ACTIVE_MS : POLL_ALERTS_MS;
    if (!srvOblasts && !(haveServer && s_srvAt && now - s_srvAt < 3 * POLL_SERVER_MS) &&
        (first || force || now - lastAlerts > alertsEvery)) {
      lastAlerts = now;
      bool ok = false;
      if (g_set.token.length()) ok = fetchAlertsActive() || fetchAlertsIot();
      if (!ok) ok = fetchUbilling();
      if (!ok) { stateLock(); g_state.alertsOk = false; g_state.version++; stateUnlock(); }
    }
    bool serverTgFresh = haveServer && s_srvAt && millis() - s_srvAt < 3 * POLL_SERVER_MS;
    if (!serverTgFresh && (first || force || now - lastTg > POLL_TELEGRAM_MS)) {
      lastTg = now;
      fetchTelegramDirect();
    }
    // графік відключень
    int32_t today = time(nullptr) > 100000 ? outageYmd(time(nullptr)) : 0;
    if (g_set.outMode == OUT_MODE_AUTO && g_set.outGroup >= 0) {
      bool dayChanged = today && today != outDay;
      if (s_outForce || dayChanged || now - lastOut > outEvery) {
        s_outForce = false; lastOut = now; outDay = today;
        outEvery = fetchOutage() ? OUTAGE_POLL_MS : OUTAGE_RETRY_MS;
      }
    } else if (s_outForce || today != outDay || now - lastOut > 60000) {
      s_outForce = false; lastOut = now; outDay = today;
      outageManualOrOff();
    }
    first = false;
  }
}

void netStart() { xTaskCreatePinnedToCore(netTask, "net", 14336, nullptr, 1, nullptr, 0); }
