// Фонова мережева задача: тривоги по районах (alerts.in.ua / ubilling / companion-сервер), Telegram,
// графік відключень світла
#include "app.h"
#include "analyzer.h"
#include "tgscrape.h"
#include "ota.h"
#include "version.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include <new>

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
  String etag;           // ETag відповіді
  int rlRemain = -1;     // x-ratelimit-remaining (api.github.com)
  uint32_t rlReset = 0;  // x-ratelimit-reset (epoch)
  bool begin(const String& url, const char* bearer = nullptr, String* lastMod = nullptr,
             const char* accept = nullptr, const String* ifNoneMatch = nullptr) {
    bool https = url.startsWith("https");
    if (https) { sec.setInsecure(); sec.setTimeout(12); }
    if (!(https ? http.begin(sec, url) : http.begin(plain, url))) return false;
    http.setTimeout(12000);
    http.useHTTP10(true);   // без chunked — можна читати потік
    http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
    http.setUserAgent("CYD-Alert/" FW_VERSION " (ESP32)");
    if (bearer && bearer[0]) http.addHeader("Authorization", String("Bearer ") + bearer);
    if (lastMod && lastMod->length()) http.addHeader("If-Modified-Since", *lastMod);
    if (accept) http.addHeader("Accept", accept);
    if (ifNoneMatch && ifNoneMatch->length()) http.addHeader("If-None-Match", *ifNoneMatch);
    static const char* hdrs[] = {"Last-Modified", "ETag", "x-ratelimit-remaining", "x-ratelimit-reset"};
    http.collectHeaders(hdrs, 4);
    code = http.GET();
    if (code == 200 && lastMod) *lastMod = http.header("Last-Modified");
    etag = http.header("ETag");
    if (http.hasHeader("x-ratelimit-remaining")) rlRemain = http.header("x-ratelimit-remaining").toInt();
    if (http.hasHeader("x-ratelimit-reset")) rlReset = (uint32_t)http.header("x-ratelimit-reset").toInt();
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
  if (!strcmp(s, "orange")) return LVL_ORANGE;
  if (!strcmp(s, "yellow")) return LVL_YELLOW;
  return LVL_GREEN;
}

// Цілі трекера + історичний пріор (ймовірна ціль, теплова карта маршрутів). Викликати під stateLock.
static void parseTargets(JsonDocument& doc) {
  g_state.ntgt = 0;
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
  strlcpy(g_state.ltName, doc["lt"] | "", sizeof(g_state.ltName));
  g_state.ltP = constrain((int)(doc["lp"] | 0), 0, 100);
  strlcpy(g_state.thr, doc["thr"] | "", sizeof(g_state.thr));
  JsonObject hm = doc["hm"];
  const char* v = hm["v"] | "";
  int w = hm["w"] | 0, h = hm["h"] | 0;
  if (w > 0 && h > 0 && w * h <= HEAT_MAX && (int)strlen(v) == w * h) {
    RouteHeat& r = g_state.heat;
    r.la0 = hm["la"] | 0.0f; r.lo0 = hm["lo"] | 0.0f; r.w = w; r.h = h;
    for (int i = 0; i < w * h; i++) {
      char c = v[i];
      r.v[i] = c >= '0' && c <= '9' ? c - '0' : (c >= 'a' && c <= 'z' ? c - 'a' + 10 : 0);
    }
  } else if (!hm.isNull()) {
    g_state.heat.w = g_state.heat.h = 0;
  }
}

static String serverBase() {
  String url = g_set.server;
  if (url.endsWith("/")) url.remove(url.length() - 1);
  return url;
}

// Статичні дані оновлюються GitHub Actions раз на ~5–15 хв: беремо звідти прогноз цілей (фільтри частинок),
// а тривоги й Telegram ESP і далі читає сам напряму (свіжіше). Тривоги зі статики — лише якщо прямі джерела впали.
static uint32_t s_staticAt = 0;
static time_t s_staticGen = 0;   // поле generated останнього отриманого файлу (для перевірки віку при 304)
static long staticAgeS() {
  time_t nowT = time(nullptr);
  return (nowT > 1600000000L && s_staticGen > 0) ? (long)(nowT - s_staticGen) : 0;
}
static void cloudFromStatic(JsonDocument& doc, long age);
static void tgAggregate();
static bool applyStatic(JsonDocument& doc, bool& gotOblasts) {
  time_t gen = (time_t)(doc["generated"] | 0L), nowT = time(nullptr);
  s_staticGen = gen;
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
  cloudFromStatic(doc, age);
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
    parseTargets(doc);
  } else {
    g_state.trkLevel = LVL_GREEN; g_state.pNear = g_state.pDrone = g_state.pMissile = g_state.pBallistic = 0;
    g_state.eta = -1; g_state.ltP = 0; g_state.thr[0] = 0;
  }
  g_state.level = computeFinalLevel(g_state);
  g_state.version++;
  stateUnlock();
  s_staticAt = millis();
  tgAggregate();
  Serial.printf("[static] age %ld s, targets %d\n", age, g_state.ntgt);
  return true;
}

// ---------------------------------------------------------------------------------------------
// Хмарні дані (live.yml) лежать у гілці `data` репозиторію. Найсвіжіше джерело — GitHub contents API:
//   https://api.github.com/repos/<owner>/<repo>/contents/<path>?ref=data   (Accept: application/vnd.github.raw)
// CDN кешує max-age=60; ETag + If-None-Match → 304, і 304 НЕ рахується в ліміт 60 запитів/год без токена.
// Запасні: raw.githubusercontent.com/<owner>/<repo>/data/<path> (кеш до 5 хв), далі GitHub Pages.
static String s_ghEtag, s_ghEtagPath;
static uint32_t s_ghBlockedUntil = 0;   // millis(): ліміт API вичерпано → raw до скидання
static const char* s_ghSrc = "—";
static bool ghRepo(String& owner, String& repo) {   // https://<owner>.github.io/<repo>
  String b = serverBase();
  int p = b.indexOf("://"); if (p < 0) return false;
  String h = b.substring(p + 3);
  int dot = h.indexOf(".github.io"); if (dot <= 0) return false;
  owner = h.substring(0, dot);
  int sl = h.indexOf('/', dot); if (sl < 0) return false;
  repo = h.substring(sl + 1);
  int e = repo.indexOf('/'); if (e >= 0) repo.remove(e);
  return repo.length() > 0;
}
// → 200 (body заповнено), 304 (не змінилось) або код помилки
static int ghDataGet(const String& path, String& body) {
  String owner, repo;
  if (!ghRepo(owner, repo)) return -1;
  const char* tok = g_set.ghToken.length() ? g_set.ghToken.c_str() : nullptr;
  if (!s_ghBlockedUntil || (int32_t)(millis() - s_ghBlockedUntil) > 0) {
    s_ghBlockedUntil = 0;
    Req r;
    String et = path == s_ghEtagPath ? s_ghEtag : String();
    r.begin("https://api.github.com/repos/" + owner + "/" + repo + "/contents/" + path + "?ref=data", tok, nullptr,
            "application/vnd.github.raw", &et);
    if (r.rlRemain >= 0 && r.rlRemain <= 2) {   // майже вичерпано → raw до скидання ліміту
      time_t nowT = time(nullptr);
      uint32_t wait = (nowT > 100000 && r.rlReset > (uint32_t)nowT) ? (r.rlReset - nowT) * 1000UL : 600000UL;
      s_ghBlockedUntil = millis() + (wait < 3600000UL ? wait : 3600000UL);
      if (!s_ghBlockedUntil) s_ghBlockedUntil = 1;
    }
    Serial.printf("[gh api] %s -> %d (ліміт %d)\n", path.c_str(), r.code, r.rlRemain);
    if (r.code == 304) { s_ghSrc = "GitHub API"; return 304; }
    if (r.code == 200) {
      body = r.http.getString();
      s_ghEtag = r.etag; s_ghEtagPath = path; s_ghSrc = "GitHub API";
      return 200;
    }
    if (r.code == 403 || r.code == 429) { s_ghBlockedUntil = millis() + 600000UL; }
  }
  int code = 0;
  if (httpGet("https://raw.githubusercontent.com/" + owner + "/" + repo + "/data/" + path, body, nullptr, &code)) {
    s_ghSrc = "GitHub raw";
    return 200;
  }
  return code ? code : -1;
}
// інтервал опитування хмари: з токеном 20 с, без — 75 с (≤48 запитів/год < 60), raw/Pages — 60 с
static uint32_t staticPollMs() {
  if (s_ghBlockedUntil) return 60000UL;
  return g_set.ghToken.length() ? 20000UL : 75000UL;
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
    String path = "api/state";
    if (c) path += String("/") + c->id + (d ? String("/") + d->id : String("")) + ".json";
    else path += ".json";
    int r = ghDataGet(path, body);
    if (r == 304) {   // не змінилось. Але якщо live.yml зупинився, файл «застигає»: старі цілі/рівень не тримаємо
      long age = staticAgeS();
      if (age < 20 * 60) s_staticAt = millis();
      if (age >= 20 * 60) Serial.printf("[static] дані застарілі: %ld хв\n", age / 60);
      tgAggregate();
      return true;
    }
    if (r != 200) {
      String url = serverBase() + "/" + path;   // GitHub Pages (раз на годину)
      if (!httpGet(url, body) && !(c && httpGet(serverBase() + "/api/state.json", body))) return false;
      s_ghSrc = "Pages";
    }
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
  g_state.tgExplicit = doc["explicit"] | false;
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
  parseTargets(doc);
  s_srvAt = millis();
  g_state.level = computeFinalLevel(g_state);
  g_state.version++;
  stateUnlock();
  return true;
}

// ----------------------------------------------------------------------------------------------
//                     Telegram: кілька каналів по черзі (t.me/s/…, потоково)
// Ваші (міські/обласні) канали — раз на ~25 с, загальнонаціональні — раз на 60 с; один запит за раз,
// між запитами ≥ 4 с. Буфер постів спільний (s_posts), для кожного каналу зберігаємо лише короткий підсумок.
// Злиття: найвищий рівень серед каналів; ≥2 канали про ту саму загрозу (±10 хв) → бал +1;
// «відбій» з вашого каналу скасовує старіші загрози з інших; червоний лише з малодовірених каналів → жовтий.
// ----------------------------------------------------------------------------------------------
#define CH_EXTRA CHANNEL_COUNT           // власний канал користувача (якщо його немає у списку)
#define CH_TEXT 160
#define TG_GAP_MS 4000UL
#define TG_LOCAL_MS 25000UL
#define TG_NATIONAL_MS 60000UL
#define TG_AGREE_S 600
struct ChanRes {
  uint32_t at = 0;          // millis() останньої спроби
  bool ok = false;
  Level lvl = LVL_GREEN;
  int8_t score = 0;
  bool near = false;
  time_t ts = 0, clearTs = 0;
  uint8_t threat = 255;
  bool expl = false;        // свіжий пост прямо про ваше місто/район (див. capLevel)
  char kind[24] = {0};
  char reason[80] = {0};
  char text[CH_TEXT] = {0};
};
static ChanRes* s_ch = nullptr;            // CHANNEL_COUNT+1 записів у купі (~7 КБ; статична DRAM зайнята LVGL)
static ChanRes s_cloud;                 // підсумок хмари (live.yml злив усі канали)
static char s_cloudCh[32] = {0};
static uint32_t s_tgLastFetch = 0;
static int16_t s_chCity = -2;            // місто, для якого рахувались підсумки

static void utf8Copy(char* dst, const char* src, size_t n) {   // без розрізаних UTF-8 символів
  strlcpy(dst, src ? src : "", n);
  size_t l = strlen(dst);
  if (l == n - 1) { while (l && ((uint8_t)dst[l - 1] & 0xC0) == 0x80) l--; if (l && ((uint8_t)dst[l - 1] & 0xC0) == 0xC0) l--; dst[l] = 0; }
}
static const char* chanId(int i) {
  if (i < CHANNEL_COUNT) return CHANNELS[i].id;
  static String extra;
  extra = "";
  if (g_set.channel.length()) extra = g_set.channel;
  else if (userCity() && userCity()->channel) extra = userCity()->channel;
  for (int k = 0; k < CHANNEL_COUNT && extra.length(); k++) if (extra.equalsIgnoreCase(CHANNELS[k].id)) extra = "";
  return extra.c_str();
}
static bool chanOn(int i) { return i < CHANNEL_COUNT ? channelEnabled(i) : chanId(i)[0] != 0; }
static bool chanLocal(int i) { return i == CH_EXTRA || channelIsLocal(chanId(i), userCity()); }
static uint8_t chanWeight(int i) { return i < CHANNEL_COUNT ? CHANNELS[i].weight : 80; }

static void cloudFromStatic(JsonDocument& doc, long age) {
  if (doc["level"].isNull()) return;
  ChanRes& c = s_cloud;
  c.ok = age < 8 * 60;   // live.yml перезаписує файл щонайменше раз на 5 хв
  c.at = millis();
  c.lvl = parseLevel(doc["level"] | "green");
  c.score = doc["score"] | 0;
  c.near = doc["near"] | false;
  c.expl = doc["explicit"] | false;
  c.ts = (time_t)(doc["ts"] | 0L);
  c.clearTs = 0;
  utf8Copy(c.kind, doc["kind"] | "", sizeof(c.kind));
  utf8Copy(c.reason, doc["reason"] | "", sizeof(c.reason));
  utf8Copy(c.text, doc["text"] | "", sizeof(c.text));
  strlcpy(s_cloudCh, doc["channel"] | "хмара", sizeof(s_cloudCh));
}

static void tgAggregate() {
  if (!s_ch) { s_ch = new (std::nothrow) ChanRes[CHANNEL_COUNT + 1]; if (!s_ch) return; }
  time_t nowT = time(nullptr);
  time_t clearTs = 0;
  int nOn = 0, nOk = 0;
  for (int i = 0; i <= CH_EXTRA; i++) {
    if (!chanOn(i)) continue;
    nOn++;
    if (!s_ch[i].ok) continue;
    nOk++;
    if (chanLocal(i) && s_ch[i].clearTs > clearTs) clearTs = s_ch[i].clearTs;
  }
  if (s_staticGen && staticAgeS() >= 8 * 60) s_cloud.ok = false;   // хмара «застигла» (live.yml зупинився)
  int best = -1;   // CH_EXTRA+1 = хмара
  auto res = [&](int i) -> ChanRes& { return i > CH_EXTRA ? s_cloud : s_ch[i]; };
  auto weight = [&](int i) -> int { return i > CH_EXTRA ? 85 : chanWeight(i); };
  for (int i = 0; i <= CH_EXTRA + 1; i++) {
    if (i <= CH_EXTRA && !chanOn(i)) continue;
    ChanRes& r = res(i);
    if (!r.ok || r.lvl == LVL_GREEN) continue;
    if (r.ts && clearTs && r.ts < clearTs) continue;
    if (i > CH_EXTRA && nowT > 100000 && r.ts && nowT - r.ts > 40 * 60) continue;
    if (best < 0) { best = i; continue; }
    ChanRes& b = res(best);
    if (r.lvl != b.lvl ? r.lvl > b.lvl : (weight(i) != weight(best) ? weight(i) > weight(best) : r.ts > b.ts)) best = i;
  }
  stateLock();
  g_state.tgNch = nOn; g_state.tgNok = nOk;
  if (best < 0) {
    g_state.tgLevel = LVL_GREEN; g_state.tgScore = 0; g_state.tgNear = false; g_state.tgKind[0] = 0;
    g_state.tgText[0] = 0; g_state.tgTime = 0; g_state.tgChan[0] = 0; g_state.tgAgree = 0; g_state.tgExplicit = false;
    snprintf(g_state.tgReason, sizeof(g_state.tgReason), "немає свіжих загроз (%d/%d каналів)", nOk, nOn);
  } else {
    ChanRes& b = res(best);
    int agree = 0;
    for (int j = 0; j <= CH_EXTRA; j++) {
      if (!chanOn(j) || !s_ch[j].ok || s_ch[j].lvl == LVL_GREEN) continue;
      if (s_ch[j].ts && clearTs && s_ch[j].ts < clearTs) continue;
      bool same = b.threat != 255 ? s_ch[j].threat == b.threat : s_ch[j].lvl == b.lvl;
      long dt = (long)(s_ch[j].ts - b.ts);
      if (same && (!b.ts || !s_ch[j].ts || (dt < 0 ? -dt : dt) <= TG_AGREE_S)) agree++;
    }
    Level L = b.lvl;
    int score = b.score + (agree >= 2 ? 1 : 0);
    if (L == LVL_RED && agree < 2 && weight(best) < 50) L = LVL_ORANGE;   // лише малодовірене джерело — на щабель нижче
    g_state.tgLevel = L;
    g_state.tgExplicit = b.expl && (!b.ts || nowT < 1600000000L || nowT - b.ts <= POST_EXPLICIT_TTL_MIN * 60);
    g_state.tgScore = score;
    g_state.tgNear = b.near || score >= 7;
    g_state.tgAgree = agree;
    strlcpy(g_state.tgKind, b.kind, sizeof(g_state.tgKind));
    strlcpy(g_state.tgText, b.text, sizeof(g_state.tgText));
    g_state.tgTime = b.ts;
    strlcpy(g_state.tgChan, best > CH_EXTRA ? s_cloudCh : chanId(best), sizeof(g_state.tgChan));
    if (agree >= 2) snprintf(g_state.tgReason, sizeof(g_state.tgReason), "%s; %d канали", b.reason, agree);
    else strlcpy(g_state.tgReason, b.reason, sizeof(g_state.tgReason));
  }
  g_state.tgOk = nOk > 0 || s_cloud.ok;
  g_state.tgAt = millis();
  g_state.tgSrc = nOk ? "t.me" : "хмара";
  if (!(s_staticAt && millis() - s_staticAt < 20 * 60 * 1000UL)) {   // без хмари прогнозу цілей немає
    g_state.trkOk = false; g_state.trkLevel = LVL_GREEN; g_state.ntgt = 0; g_state.pNear = 0; g_state.eta = -1;
  }
  g_state.level = computeFinalLevel(g_state);
  g_state.version++;
  stateUnlock();
}

static bool fetchOneChannel(int i) {
  const char* ch = chanId(i);
  ChanRes& r = s_ch[i];
  r.at = millis();
  int n = tgFetchChannel(ch, s_posts, TG_MAX_POSTS);
  Serial.printf("[tg] %s: %d posts, heap %u\n", ch, n, ESP.getFreeHeap());
  if (n < 0) { r.ok = false; return false; }
  bool remote = !chanLocal(i);
  int best = -1; PostEval ev;
  Level l = analyzeFeed(s_posts, n, userCity(), g_set.district, time(nullptr), &best, &ev, remote,
                        (i == CH_EXTRA || channelIsHome(ch, userCity())) ? 1 : 0);
  r.ok = true;
  r.lvl = l; r.score = ev.score; r.near = ev.near; r.threat = ev.threat; r.expl = ev.explicitCity;
  utf8Copy(r.kind, ev.kind, sizeof(r.kind));
  utf8Copy(r.reason, ev.reason, sizeof(r.reason));
  r.clearTs = 0;
  if (best >= 0) {
    utf8Copy(r.text, s_posts[best].text, sizeof(r.text));
    r.ts = s_posts[best].ts;
    if (ev.clear && l == LVL_GREEN) r.clearTs = r.ts ? r.ts : time(nullptr);
  } else { r.text[0] = 0; r.ts = 0; }
  return true;
}

// викликається з netTask кожні 500 мс; повертає true, якщо був запит
static bool telegramTick(bool force) {
  if (!s_ch) { s_ch = new (std::nothrow) ChanRes[CHANNEL_COUNT + 1]; if (!s_ch) return false; }
  if (s_chCity != g_set.city || force) {   // змінилась локація / налаштування — усе заново
    for (int i = 0; i <= CH_EXTRA; i++) { if (s_chCity != g_set.city) s_ch[i] = ChanRes(); else s_ch[i].at = 0; }
    s_chCity = g_set.city;
  }
  uint32_t now = millis();
  if (s_tgLastFetch && now - s_tgLastFetch < TG_GAP_MS) return false;
  int pick = -1; int32_t over = 0;
  for (int i = 0; i <= CH_EXTRA; i++) {
    if (!chanOn(i)) { if (s_ch[i].ok) { s_ch[i].ok = false; } continue; }
    uint32_t period = chanLocal(i) ? TG_LOCAL_MS : TG_NATIONAL_MS;
    if (!s_ch[i].ok && s_ch[i].at) period = TG_NATIONAL_MS;   // помилка — повтор не частіше раз на хвилину
    int32_t o = s_ch[i].at ? (int32_t)(now - s_ch[i].at - period) : 0x3FFFFFFF - i * (chanLocal(i) ? 1 : 1000);
    if (o >= 0 && (pick < 0 || o > over)) { pick = i; over = o; }
  }
  if (pick < 0) return false;
  fetchOneChannel(pick);
  s_tgLastFetch = millis();
  tgAggregate();
  return true;
}

const char* netCloudSrc() { return s_ghSrc; }

// для екрана налаштувань: стан каналу (0 — ще не читали, 1 — ок, 2 — помилка), рівень
int netChannelStatus(int i, Level* lvl) {
  if (i < 0 || i > CH_EXTRA || !s_ch) return 0;
  if (lvl) *lvl = s_ch[i].lvl;
  return s_ch[i].ok ? 1 : (s_ch[i].at ? 2 : 0);
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
    uint32_t srvEvery = (haveServer && serverIsStatic()) ? staticPollMs() : POLL_SERVER_MS;
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
    if (!serverTgFresh) telegramTick(force);
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
    otaTick();   // раз на 6 год (і через 90 с після старту); не під час помаранчевого/червоного рівня
  }
}

void netStart() { xTaskCreatePinnedToCore(netTask, "net", 14336, nullptr, 1, nullptr, 0); }
