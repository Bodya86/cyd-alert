// Аналіз постів Telegram (той самий алгоритм, що й у server/analyzer.py — тримайте їх синхронними)
#include "analyzer.h"
#include "textutil.h"
#include <string.h>
#include <strings.h>
#include <stdio.h>
#include <math.h>

static float kmBetween(float la1, float lo1, float la2, float lo2) {
  float x = (lo2 - lo1) * 111.32f * cosf((la1 + la2) * 0.5f * 0.0174533f), y = (la2 - la1) * 110.57f;
  return sqrtf(x * x + y * y);
}

static bool anyKw(const char* t, const char* const* kw, int n) { return countWords(t, kw, n) > 0; }

// Знайти слово з початку (" kw") — повертає позицію або nullptr
static const char* findWordStart(const char* t, const char* kw) {
  for (const char* p = strstr(t, kw); p; p = strstr(p + 1, kw))
    if (p > t && p[-1] == ' ') return p;
  return nullptr;
}

// Попереднє слово перед позиції p (у нормалізованому тексті), копія в buf
static void prevWord(const char* t, const char* p, int back, char* buf, size_t n) {
  buf[0] = 0;
  const char* e = p;
  for (int k = 0; k < back; k++) {
    while (e > t && e[-1] == ' ') e--;
    const char* s = e;
    while (s > t && s[-1] != ' ') s--;
    if (k == back - 1) { size_t l = e - s < (long)n - 1 ? e - s : n - 1; memcpy(buf, s, l); buf[l] = 0; }
    e = s;
  }
}

static bool isFromWord(const char* w) {
  static const char* const F[] = {"с", "з", "из", "зи", "от", "вид", "со", "из-за", "через"};
  for (auto f : F) if (!strcmp(w, f)) return true;
  return false;
}

static void addReason(char* r, size_t n, const char* s) {
  if (r[0]) strlcat(r, ", ", n);
  strlcat(r, s, n);
}

bool channelIsHome(const char* ch, const CityDef* city) {
  if (!ch || !ch[0] || !city) return true;
  if (ch[0] == '@') ch++;
  if (city->channel && !strcasecmp(city->channel, ch)) return true;
  for (int i = 0; i < CHANNEL_COUNT; i++)
    if (!strcasecmp(CHANNELS[i].id, ch)) return CHANNELS[i].home[0] && !strcmp(CHANNELS[i].home, city->id);
  return false;
}

// «по нам», «над містом», «на город» — займенникові ключі (лише для міського каналу)
static bool isGenericKw(const char* kw) {
  static const char* const P[] = {"по ", "на ", "над ", "в ", "у ", "до "};
  for (auto p : P) if (!strncmp(kw, p, strlen(p))) return true;
  return false;
}
static bool endsWith(const char* s, size_t n, const char* suf) {
  size_t l = strlen(suf);
  return n >= l && !memcmp(s + n - l, suf, l);
}
// токен-прикметник / «-щина»: «кременчуцкий (район)», «полтавска», «кременчуччина» — район/область, не місто
static bool isAdjToken(const char* s, size_t n) {
  while (n && (s[n - 1] == '.' || s[n - 1] == '-' || s[n - 1] == '/')) n--;
  static const char* const E[] = {"ий", "ого", "ому", "им", "ими", "ом", "ая", "а", "ои", "ой", "ую", "у", "ие", "их", "е", "и"};
  static const char* const K[] = {"цк", "ск", "зк"};
  for (auto e : E) {
    if (!endsWith(s, n, e)) continue;
    size_t m = n - strlen(e);
    for (auto k : K) if (endsWith(s, m, k)) return true;
  }
  char tok[64]; size_t l = n < sizeof(tok) - 1 ? n : sizeof(tok) - 1;
  memcpy(tok, s, l); tok[l] = 0;
  return strstr(tok, "щин") || strstr(tok, "ччин");
}
// → місто згадано; *raion — згадано лише прикметником (район/область)
static bool cityMatch(const char* t, const CityDef* c, bool home, bool* raion) {
  bool hit = false, adj = false;
  for (int k = 0; k < c->nkw; k++) {
    const char* kw = c->kw[k];
    if (isGenericKw(kw)) { if (home && strstr(t, kw)) hit = true; continue; }
    for (const char* p = strstr(t, kw); p; p = strstr(p + 1, kw)) {
      const char* s = p; while (s > t && s[-1] != ' ') s--;
      const char* e = p; while (*e && *e != ' ') e++;
      if (isAdjToken(s, e - s)) adj = true; else hit = true;
    }
  }
  *raion = adj && !hit;
  return hit;
}
static bool isAmbigDistrictKw(const char* kw) {
  static const char* const A[] = {"центр", "соборн", "набережн", "площ", "правий берег", "правобереж", "нпз",
                                  "нефтезавод", "нафтозавод", "промзон", "автозавод"};
  for (auto a : A) if (!strcmp(kw, a)) return true;
  return false;
}

bool channelIsLocal(const char* ch, const CityDef* city) {
  if (!city || !ch || !ch[0]) return false;
  if (city->channel && !strcasecmp(city->channel, ch)) return true;
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    const ChannelDef& c = CHANNELS[i];
    if (strcasecmp(c.id, ch)) continue;
    if (c.home[0] && !strcmp(c.home, city->id)) return true;
    return c.scope == 1 && (c.obMask & (1u << city->oblast));
  }
  return false;
}

PostEval analyzePost(const char* text, const CityDef* city, int district, bool remote, int homeArg) {
  PostEval ev;
  bool home = homeArg < 0 ? !remote : homeArg != 0;
  static char low[TG_TEXT_MAX * 2];
  utf8Fold(text, low, sizeof(low));

  // 1) реклама / вакансії
  int ign = countWords(low, RULE_IGNORE.w, RULE_IGNORE.n);
  if (ign >= 2 || (ign >= 1 && strlen(low) > 350)) { ev.ignored = true; return ev; }

  // 2) тип загрози
  // вага — максимальна серед знайдених; тип (рівень) — найнебезпечніший
  // mask: підтип, що збігся, прибирає загальні слова лише для визначення типу («розвідувальний БпЛА» → не шахед)
  // (записи з mask оцінюються по повному тексту — mhit)
  bool masked = false;
  uint32_t mhit = 0;
  static_assert(THREAT_COUNT <= 32, "mhit — 32 біти");
  for (int i = 0; i < THREAT_COUNT; i++) {
    if (!THREATS[i].nmask) continue;
    if (!countWords(low, THREATS[i].kw, THREATS[i].nkw) && !fuzzyAny(low, THREATS[i].fz, THREATS[i].nfz)) continue;
    mhit |= 1u << i;
  }
  for (int i = 0; i < THREAT_COUNT; i++) {
    if (!(mhit & (1u << i))) continue;
    for (int j = 0; j < THREATS[i].nmask; j++) {
      size_t L = strlen(THREATS[i].mask[j]);
      for (char* q = strstr(low, THREATS[i].mask[j]); q; q = strstr(q + L, THREATS[i].mask[j])) { memset(q, ' ', L); masked = true; }
    }
  }
  int threatW = 0, best = -1;
  for (int i = 0; i < THREAT_COUNT; i++) {
    if (THREATS[i].nmask ? !(mhit & (1u << i))
                         : (!countWords(low, THREATS[i].kw, THREATS[i].nkw) && !fuzzyAny(low, THREATS[i].fz, THREATS[i].nfz))) continue;
    if (THREATS[i].weight > threatW) threatW = THREATS[i].weight;
    if (best < 0 || THREATS[i].level > THREATS[best].level ||
        (THREATS[i].level == THREATS[best].level && THREATS[i].weight > THREATS[best].weight)) best = i;
  }
  const char* threatLabel = best >= 0 ? THREATS[best].label : nullptr;
  if (masked) utf8Fold(text, low, sizeof(low));   // решта аналізу — по повному тексту

  bool approach = countWords(low, RULE_APPROACH.w, RULE_APPROACH.n) > 0;
  bool escalate = countWords(low, RULE_ESCALATE.w, RULE_ESCALATE.n) > 0;
  ev.clear = countWords(low, RULE_CLEAR.w, RULE_CLEAR.n) > 0;

  bool inCity = false, raionHit = false;
  int distBonus = 0, settlBonus = 0;
  bool settlHit = false;
  const char* placeHit = nullptr;
  if (city) {
    inCity = cityMatch(low, city, home, &raionHit);
    bool named = inCity;
    int userSector = (district >= 0 && district < city->ndist) ? city->districts[district].sector : -2;
    for (int d = 0; d < city->ndist; d++) {
      const DistrictDef& D = city->districts[d];
      bool dh = false;
      for (int k = 0; k < D.nkw && !dh; k++)
        if ((home || named || !isAmbigDistrictKw(D.kw[k])) && strstr(low, D.kw[k])) dh = true;
      if (!dh) continue;
      inCity = true;
      int b;
      if (d == district) b = 4;
      else if (userSector == -2 || userSector == -1 || D.sector == -1 || angleDiff(userSector, D.sector) <= 60) b = 2;
      else b = 1;
      if (b > distBonus) { distBonus = b; placeHit = D.name; }
    }
    int userSec = (district >= 0 && district < city->ndist) ? city->districts[district].sector : -1;
    for (int s = 0; s < city->nsettl; s++) {
      const SettlementDef& S = city->settl[s];
      if (!countWords(low, S.kw, S.nkw)) continue;
      settlHit = true;
      int b = S.km <= 15 ? 2 : (S.km <= 45 ? 1 : 0);
      if (b > 0 && userSec >= 0 && angleDiff(userSec, S.bearing) <= 60) b += 1;  // з вашого боку міста
      if (b > settlBonus) settlBonus = b;
    }
  }

  bool unknownDir = anyKw(low, RULE_UNKNOWN_DIR.w, RULE_UNKNOWN_DIR.n);
  // великі міста: роль «at» (де ціль) чи «from» (звідки летить)
  int nAt = 0; float nearestKm = 1e9f; const char* nearestName = "";
  bool oblHit = city && city->oblast < OBLAST_COUNT && OBLAST_KW[city->oblast].n &&
                countWords(low, OBLAST_KW[city->oblast].w, OBLAST_KW[city->oblast].n) > 0;
  for (int i = 0; i < PLACE_COUNT; i++) {
    const PlaceDef& P = PLACES[i];
    for (int k = 0; k < P.nkw; k++) {
      const char* pos = findWordStart(low, P.kw[k]);
      if (!pos) continue;
      char w1[16], w2[24];
      prevWord(low, pos, 1, w1, sizeof(w1));
      prevWord(low, pos, 2, w2, sizeof(w2));
      bool from = isFromWord(w1) ||
                  ((!strcmp(w2, "со") || !strcmp(w2, "зи") || !strcmp(w2, "з") || !strcmp(w2, "с")) &&
                   (!strncmp(w1, "сторон", 12) || !strncmp(w1, "боку", 8) || !strncmp(w1, "напрям", 12)));
      if (!from) {
        nAt++;
        float d = city ? kmBetween(city->lat, city->lon, P.lat, P.lon) : 0;
        if (d < nearestKm) { nearestKm = d; nearestName = P.name; }
      }
      break;
    }
  }
  ev.threat = best >= 0 ? (uint8_t)best : 255;
  ev.local = inCity || settlHit || distBonus || oblHit || raionHit || (nAt && nearestKm <= NEAR_PLACE_KM);
  ev.relevant = threatW > 0 || inCity || settlBonus > 0 || approach || unknownDir;
  if (!ev.relevant && !ev.clear) return ev;

  int score = threatW;
  if (inCity) score += 2;
  if (inCity && threatW >= 4) score += 1;          // ракета/КАБ по місту
  if (approach && inCity) score += 2;
  else if (approach && settlBonus) score += 1;
  score += distBonus + settlBonus;
  // «Дуже низько»/«вибухи» без прив'язки до вас із чужого каналу — не «над вами» (30.10: «мКР … в сторону с. Рокита … Дуже низько»)
  if (escalate) score += (home || inCity || settlBonus || distBonus || raionHit || (nAt && nearestKm <= NEAR_PLACE_KM)) ? 3 : 1;

  if (threatLabel) addReason(ev.reason, sizeof(ev.reason), threatLabel);
  if (distBonus == 4) addReason(ev.reason, sizeof(ev.reason), "ваш район");
  else if (placeHit) addReason(ev.reason, sizeof(ev.reason), placeHit);
  else if (inCity) addReason(ev.reason, sizeof(ev.reason), "місто");
  if (settlBonus) addReason(ev.reason, sizeof(ev.reason), "поруч");
  if (approach) addReason(ev.reason, sizeof(ev.reason), "курс");
  if (escalate) addReason(ev.reason, sizeof(ev.reason), "вибухи/ППО");

  if (ev.clear && !escalate && distBonus < 4 && !(remote && !ev.local)) {
    ev.score = 0; ev.level = LVL_GREEN;
    strlcpy(ev.reason, "відбій / ціль пролетіла", sizeof(ev.reason));
    return ev;
  }
  // «Харків ракета»: усі згадані місця далеко, про нас нічого → лише інформація
  if (city && nAt && !(inCity || settlHit || distBonus) && nearestKm > FAR_PLACE_KM) {
    ev.far = true; ev.farPlace = nearestName; ev.farKm = (int)(nearestKm + 0.5f);
    ev.score = score < 2 ? score : 2;
    ev.level = LVL_GREEN; ev.kind = ""; ev.near = false;
    char b[64];
    snprintf(b, sizeof(b), "далеко: %s ~%d км", nearestName, ev.farKm);
    ev.reason[0] = 0;
    if (threatLabel) addReason(ev.reason, sizeof(ev.reason), threatLabel);
    addReason(ev.reason, sizeof(ev.reason), b);
    return ev;
  }
  // загальнонаціональний / чужий обласний канал: без згадки вашої території — лише інформація
  if (remote && city && !ev.local) {
    ev.clear = false;
    ev.score = score < 2 ? score : 2;
    ev.level = LVL_GREEN; ev.kind = ""; ev.near = false;
    ev.reason[0] = 0;
    if (threatLabel) addReason(ev.reason, sizeof(ev.reason), threatLabel);
    addReason(ev.reason, sizeof(ev.reason), "не про вашу область");
    return ev;
  }
  // балістика без місця або «хз звідки» → червоний на всю область (коротко)
  if (best >= 0 && !strcmp(THREATS[best].type, "ballistic") && (unknownDir || !(nAt || inCity || settlHit || distBonus)) &&
      (!remote || oblHit || inCity)) {
    if (score < 6) score = 6;
    ev.oblastWide = true;
    addReason(ev.reason, sizeof(ev.reason), "напрямок невідомий — вся область");
  }
  ev.score = score;
  ev.near = score >= 7;
  if (score >= 3) {
    ev.cls = best >= 0 ? THREATS[best].level : CLS_DRONE;
    ev.kind = best >= 0 ? THREATS[best].kind : "Загроза";
    if (distBonus == 4 || (distBonus >= 2 && (approach || escalate)) || (inCity && escalate)) ev.prox = 2;
    else if (inCity || settlBonus >= 2 || ev.near) ev.prox = 1;
    bool ballisticObl = best >= 0 && !strcmp(THREATS[best].type, "ballistic") && oblHit;
    ev.level = levelOf(ev.cls, ev.prox, ev.oblastWide || ballisticObl, settlBonus > 0);
    if (best >= 0 && THREATS[best].cap && ev.level > THREATS[best].cap) ev.level = (Level)THREATS[best].cap;   // розвідник ≤ жовтий
    ev.explicitCity = inCity || distBonus || settlBonus >= 2 || ev.oblastWide || ballisticObl;
  }
  return ev;
}

Level levelOf(uint8_t cls, uint8_t prox, bool oblastWide, bool nearby) {
  if (cls == CLS_MISSILE) return (prox >= 1 || oblastWide || nearby) ? LVL_RED : LVL_ORANGE;
  return prox >= 2 ? LVL_RED : (prox == 1 ? LVL_ORANGE : LVL_YELLOW);
}

Level agedLevel(const PostEval& e, long ageMin) {
  if (e.level <= LVL_YELLOW || e.cls != CLS_DRONE || e.oblastWide) return e.level;
  int l = (int)e.level - (int)((ageMin > 0 ? ageMin : 0) / POST_AGE_STEP_MIN);
  return (Level)(l < LVL_YELLOW ? LVL_YELLOW : l);
}

Level analyzeFeed(const TgPost* posts, int n, const CityDef* city, int district, time_t now,
                  int* bestIdx, PostEval* bestEval, bool remote, int home) {
  Level cur = LVL_GREEN;
  int best = -1;
  PostEval bestE;
  if (now < 1600000000L) {   // годинник ще не синхронізований (NTP) — вік постів невідомий, старі пости не рахуємо
    if (bestIdx) *bestIdx = -1;
    if (bestEval) *bestEval = bestE;
    return LVL_GREEN;
  }
  for (int i = 0; i < n; i++) {
    const TgPost& p = posts[i];
    if (!p.text[0]) continue;
    long ageMin = (now > 100000 && p.ts) ? (long)(now - p.ts) / 60 : 0;
    if (ageMin > POST_YELLOW_TTL_MIN) continue;
    PostEval e = analyzePost(p.text, city, district, remote, home);
    if (e.ignored) continue;
    e.level = agedLevel(e, ageMin);
    if (ageMin > POST_EXPLICIT_TTL_MIN) e.explicitCity = false;
    if (e.clear && e.level == LVL_GREEN) { cur = LVL_GREEN; best = i; bestE = e; continue; }
    Level l = e.level;
    if (e.oblastWide && ageMin > POST_OBLAST_TTL_MIN) continue;
    if (e.cls == CLS_MISSILE && ageMin > POST_RED_TTL_MIN) continue;   // ракети швидкі — старий пост неактуальний
    if (l >= cur && l > LVL_GREEN) { cur = l; best = i; bestE = e; }
    else if (cur == LVL_GREEN && e.relevant) { best = i; bestE = e; }
  }
  if (bestIdx) *bestIdx = best;
  if (bestEval) *bestEval = bestE;
  return cur;
}
