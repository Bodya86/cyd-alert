// Тест парсера t.me/s і аналізатора на ПК:  ./run_tests.sh [file.html]
#include "analyzer.h"
#include "tgparser.h"
#include <assert.h>

static const CityDef* city(const char* id) { for (int i = 0; i < CITY_COUNT; i++) if (!strcmp(CITIES[i].id, id)) return &CITIES[i]; return nullptr; }
static int dist(const CityDef* c, const char* id) { for (int i = 0; i < c->ndist; i++) if (!strcmp(c->districts[i].id, id)) return i; return -1; }

#include "cases_gen.h"
static int fails = 0;
// near: 1 = має бути «поруч», 0 = ні, -1 = не перевіряти
static void expect(const char* txt, const char* d, Level want, int near = -1, int far = -1, int ow = -1, const char* ch = "") {
  const CityDef* k = city("kremenchuk");
  bool remote = ch && ch[0] && !channelIsLocal(ch, k);
  PostEval e = analyzePost(txt, k, dist(k, d), remote, channelIsHome(ch, k) ? 1 : 0);
  bool ok = e.level == want && (near < 0 || (int)e.near == near) && (far < 0 || (int)e.far == far) &&
            (ow < 0 || (int)e.oblastWide == ow);
  if (!ok) fails++;
  printf("%s [%s] score=%d lvl=%s near=%d kind=%s clear=%d ign=%d | %s | %s\n", ok ? "OK  " : "FAIL", d, e.score, levelName(e.level), e.near, e.kind, e.clear, e.ignored, e.reason, txt);
}
const char* levelName(Level l) { return l == LVL_RED ? "RED" : l == LVL_ORANGE ? "ORANGE" : l == LVL_YELLOW ? "YELLOW" : "GREEN"; }

#include "outage.h"
#include <stdlib.h>
static void check(bool ok, const char* what) {
  printf("%s %s\n", ok ? "OK  " : "FAIL", what);
  if (!ok) fails++;
}
static void outageTests() {
  setenv("TZ", "EET-2EEST,M3.5.0/3,M10.5.0/4", 1); tzset();
  uint8_t two[2];
  outageGpvCells("first", two);  check(two[0] == OUT_OFF && two[1] == OUT_ON, "gpv first");
  outageGpvCells("msecond", two); check(two[0] == OUT_ON && two[1] == OUT_MAYBE, "gpv msecond");
  outageGpvCells("no", two);     check(two[0] == OUT_OFF && two[1] == OUT_OFF, "gpv no");
  check(outageYmdFromDayTs(1793138400L) == 20261028, "kyiv midnight key 2026-10-28");   // 27.10 22:00 UTC
  OutageInfo o; o.ndays = 2;
  o.days[0].ymd = 20261028; o.days[1].ymd = 20261029;
  for (int i = 0; i < 48; i++) { o.days[0].c[i] = (i >= 28 && i < 36) ? OUT_OFF : OUT_ON; o.days[1].c[i] = i < 4 ? OUT_MAYBE : OUT_ON; }
  time_t t = 1793138400L + 13 * 3600 + 10 * 60;   // 28.10 13:10 за Києвом
  time_t at; uint8_t nv;
  check(outageNow(o, t, &at, &nv) == OUT_ON && at == 1793138400L + 14 * 3600 && nv == OUT_OFF, "now on, off at 14:00");
  t = 1793138400L + 15 * 3600;
  check(outageNow(o, t, &at, &nv) == OUT_OFF && at == 1793138400L + 18 * 3600 && nv == OUT_ON, "now off until 18:00");
  t = 1793138400L + 20 * 3600;
  check(outageNow(o, t, &at, &nv) == OUT_ON && at == 1793138400L + 24 * 3600 && nv == OUT_MAYBE, "change on tomorrow 00:00");
  check(outageOffHalfHours(o.days[0]) == 8, "4 h off today");
  OutageInfo m; outageFromMask(m, (1ULL << 0) | (1ULL << 47), t);
  check(m.manual && m.ndays == 2 && m.days[0].c[0] == OUT_OFF && m.days[0].c[1] == OUT_ON && m.days[1].c[47] == OUT_OFF, "manual mask");
}

int main(int argc, char** argv) {
  outageTests();
  // ЗЕЛЕНИЙ = чисто, ЖОВТИЙ = загроза в області, ПОМАРАНЧЕВИЙ = курс на місто, ЧЕРВОНИЙ = ваш район / ракети поруч
  for (const Case& c : CASES) expect(c.t, c.d, c.l, c.near, c.far, c.ow, c.ch);   // test/analyzer_cases.json
  {  // 30.10 16:31 хибний червоний: прогноз-новина, підпис каналу, «балістика на область» без тривоги — не explicitCity
    const CityDef* k = city("kremenchuk");
    PostEval e = analyzePost("США попередили Україну про можливу масовану ракетну атаку протягом найближчих 48 годин За даними "
                             "розвідки, зафіксовано переміщення шести ракет «Іскандер» 📣 ППО Полтавщина", k, -1, false, 0);
    check(e.ignored && e.level == LVL_GREEN && !e.explicitCity, "forecast post ignored");
    e = analyzePost("Балістика на Полтавщину! 📣 ППО Полтавщина", k, -1, false, 0);
    check(e.level == LVL_RED && !e.explicitCity && capLevel(e.level, false, e.explicitCity) == LVL_YELLOW, "ballistic oblast capped w/o alert");
    e = analyzePost("Іскандер-М балістика 📣 ППО Полтавщина", k, -1, false, 0);
    check(!e.explicitCity, "signature is not a location");
    e = analyzePost("Балістика на Кременчук!", k, -1, false, 1);
    check(e.level == LVL_RED && e.explicitCity, "ballistic on city explicit");
    e = analyzePost("Загроза балістики! Можлива атака, всім в укриття", k, -1, false, 0);
    check(!e.ignored, "live warning not ignored");
    check(!isForecastPost(" совершени первие пусковие маневри со всех бортов ракети ждем в течении следующих 30-50 минут "), "launch in N min is live");
    // звіти постфактум (= server is_report; 30.10 07:26 хибний червоний — у прошивці не було)
    e = analyzePost("⚡️ ЗБИТО/ПОДАВЛЕНО 71 ВОРОЖИЙ БПЛА У ніч на 28 жовтня противник атакував балістичною ракетою Іскандер-М", k, -1, false, 1);
    check(e.ignored && e.level == LVL_GREEN, "report: zbyto/podavleno ignored");
    e = analyzePost("Уночі над Україною знешкодили 152 ворожі БпЛА. У ніч на 18 жовтня росія атакувала Україну балістичною ракетою", k, -1, false, 0);
    check(e.ignored && e.level == LVL_GREEN, "report: u nich na N ignored");
    e = analyzePost("Попередньо. вранці ворог застосував 6 крилатих ракет по Кременчуку.", k, -1, false, 1);
    check(e.ignored, "report: vrantsi voroh zastosuvav ignored");
    e = analyzePost("В ночь на 30 октября враг атаковал Кременчуг ракетами", k, -1, false, 1);
    check(e.ignored, "report RU: v noch na N");
    e = analyzePost("Ворог атакував Кременчук, працює ППО", k, -1, false, 1);
    check(!e.ignored && e.level >= LVL_ORANGE, "live attack not a report");
    e = analyzePost("Ворог атакував Кременчук 2 ракетами, ще підльот", k, -1, false, 1);
    check(!e.ignored, "pidlot is live");
    check(isReportPost(" за нич ворог застосував 9 ракет ") && !isReportPost(" ракета на кременчук "), "isReportPost");
    // 30.10 19:53: репост звернення «Були сьогодні влучання російської балістики на Полтавщині. Кременчук» → хибний червоний
    e = analyzePost("Були сьогодні влучання російської балістики на Полтавщині. Кременчук. Мішенню була нафтопереробна інфраструктура.", k, -1, false, 1);
    check(e.ignored, "retro past: buly vluchannia");
    check(isReportPost(" у лубнах було 2 влучання ") && isReportPost(" мишенню була енергетика "), "retroPast");
    e = analyzePost("Балістика на Кременчук! Були вибухи, в укриття", k, -1, false, 1);
    check(!e.ignored && e.level == LVL_RED, "retro past with live marker stays live");
    // «мКР» — малогабаритна КР (ракета), «мкр.» — мікрорайон
    e = analyzePost("❗️2 мКР на Кременчук", k, -1, false, 1);
    check(e.level == LVL_RED, "mKR is missile");
    e = analyzePost("мкр. Молодіжний — шахед", k, -1, false, 1);
    check(e.level != LVL_RED || strcmp(e.kind, "Ракети") != 0, "mkr. microdistrict not missile");
    char sb[96]; stripSignature("Шахед на Полтаву 📣 ППО Полтавщина", sb, sizeof(sb));
    check(!strcmp(sb, "Шахед на Полтаву"), "strip signature");
  }
  {  // балістика «хз звідки» — червоний 10 хв; «харьков ракета» — лише інформація
    static TgPost fp[2];
    time_t now = 1800000000;
    fp[0].id = 1; fp[0].ts = now - 5 * 60; strlcpy(fp[0].text, "Бублистика хз откуда", sizeof(fp[0].text));
    fp[1].id = 2; fp[1].ts = now - 4 * 60; strlcpy(fp[1].text, "харьков ракета", sizeof(fp[1].text));
    const CityDef* k = city("kremenchuk");
    check(analyzeFeed(fp, 2, k, dist(k, "molod"), now, nullptr, nullptr) == LVL_RED, "feed: ballistic oblast-wide red");
    fp[0].ts = now - 12 * 60;
    PostEval be;
    check(analyzeFeed(fp, 2, k, dist(k, "molod"), now, nullptr, &be) == LVL_GREEN && be.far, "feed: oblast-wide expires 10 min, far stays info");
  }

  {  // близькість «старіє»: дрон над вашим районом — червоний, через 10 хв помаранчевий, через 20 — жовтий
    static TgPost dp[1];
    time_t now = 1800000000;
    const CityDef* k = city("kremenchuk");
    dp[0].id = 1; dp[0].ts = now - 60; strlcpy(dp[0].text, "Шахед над Молодіжним", sizeof(dp[0].text));
    check(analyzeFeed(dp, 1, k, dist(k, "molod"), now, nullptr, nullptr) == LVL_RED, "feed: drone over district red");
    dp[0].ts = now - 12 * 60;
    check(analyzeFeed(dp, 1, k, dist(k, "molod"), now, nullptr, nullptr) == LVL_ORANGE, "feed: 12 min old -> orange");
    dp[0].ts = now - 22 * 60;
    check(analyzeFeed(dp, 1, k, dist(k, "molod"), now, nullptr, nullptr) == LVL_YELLOW, "feed: 22 min old -> yellow");
    dp[0].ts = now - 60; strlcpy(dp[0].text, "Шахед курсом на Кременчук", sizeof(dp[0].text));
    check(analyzeFeed(dp, 1, k, dist(k, "molod"), now, nullptr, nullptr) == LVL_ORANGE, "feed: course to city orange");
  }

  if (argc > 1) {
    FILE* f = fopen(argv[1], "rb");
    if (!f) { perror(argv[1]); return 1; }
    static TgPost posts[10];
    tgp::Parser p; p.out = posts; p.maxPosts = 10;
    int c; while ((c = fgetc(f)) != EOF) p.feed((char)c);
    p.commit(); fclose(f);
    printf("\n--- parsed %d posts ---\n", p.count);
    const CityDef* k = city("kremenchuk");
    for (int i = 0; i < p.count; i++) {
      PostEval e = analyzePost(posts[i].text, k, dist(k, "molod"));
      printf("#%u %ld %-6s s=%d ign=%d clr=%d | %.110s\n", posts[i].id, (long)posts[i].ts, levelName(e.level), e.score, e.ignored, e.clear, posts[i].text);
    }
    int best; PostEval be;
    Level L = analyzeFeed(posts, p.count, k, dist(k, "molod"), posts[p.count - 1].ts + 60, &best, &be);
    printf("FEED => %s (best #%d, %s)\n", levelName(L), best, be.reason);
  }
  printf(fails ? "\n%d FAILED\n" : "\nALL OK\n", fails);
  return fails ? 1 : 0;
}
