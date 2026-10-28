#pragma once
#include <Arduino.h>
#include <time.h>
#include "config.h"
#include "gen_data.h"
#include "map_meta.h"
#include "outage.h"

// ------------------------------- Рівні небезпеки -------------------------------
// ЗЕЛЕНИЙ — чисто; ЖОВТИЙ — дрони (шахеди/БпЛА) або тривога невідомого типу;
// ЧЕРВОНИЙ — ракети / балістика / КАБ / авіація (і інші небезпеки: обстріли тощо)
// Рівень = близькість загрози до адреси (екран фарбується відповідно, без модальних вікон):
// ЗЕЛЕНИЙ — чисто; ЖОВТИЙ — дрони в області / загальна загроза; ПОМАРАНЧЕВИЙ — ціль курсом на ваше місто,
// ракети/КАБ в області; ЧЕРВОНИЙ — ціль над/на ваш район, вибухи в місті, ракети поруч.
enum Level : uint8_t { LVL_GREEN = 0, LVL_YELLOW = 1, LVL_ORANGE = 2, LVL_RED = 3 };
const char* levelName(Level l);   // "ЧИСТО" / "ЗАГРОЗА" / "КУРС НА МІСТО" / "НЕБЕЗПЕКА"

// ------------------------------- Налаштування (NVS) -------------------------------
struct Settings {
  String ssid, pass;
  String token;        // alerts.in.ua
  String server;       // companion server base url
  String channel;      // додатковий власний telegram-канал (без @), порожньо — немає
  String ghToken;      // необов'язковий GitHub token (read-only) — швидше опитування api.github.com (5000/год замість 60)
  uint32_t chOn = 0;   // увімкнені канали з CHANNELS (біт i), якщо chCustom
  bool chCustom = false; // false — типові канали для вашої області
  int8_t oblast = -1;  // індекс у OBLASTS
  int16_t city = -1;   // індекс у CITIES, -1 = вся область
  int16_t district = -1; // індекс у CITIES[city].districts, -1 = все місто
  uint8_t brightness = 200;
  bool sound = true;
  bool alertOnYellow = true;   // звуковий сигнал на жовтому рівні (дрони в області)
  bool autoZoom = true;        // мапа сама наближається до області / району, коли ціль поруч
  bool showRoutes = false;     // шар «типові маршрути» (історична модель) на зумі області
  // графік відключень світла
  uint8_t outMode = OUT_MODE_AUTO;   // OUT_MODE_OFF / AUTO / MANUAL
  int8_t outGroup = -1;              // індекс у OUTAGE_GROUPS ("1.1".."6.2"), -1 — не обрано
  int8_t outProv = -1;               // індекс у OUTAGE_PROVS, -1 — авто за областю
  uint64_t outMask = 0;              // ручний графік: біт i = немає світла в півгодину i (0..47)
  bool configured() const { return oblast >= 0; }
};
extern Settings g_set;
void settingsLoad();
void settingsSave();
void settingsReset();
const CityDef* userCity();
const DistrictDef* userDistrict();
String userChannel();      // основний канал (власний або міський) — для підписів
bool channelEnabled(int i); // канал CHANNELS[i] увімкнено (власний вибір або типовий для області)
void channelSetEnabled(int i, bool on);
int channelsEnabledCount();
String userPlaceName();   // "Кременчук · Молодіжний"

// ------------------------------- Пости Telegram -------------------------------
#define TG_TEXT_MAX 360
struct TgPost {
  uint32_t id = 0;
  time_t ts = 0;
  char text[TG_TEXT_MAX] = {0};
};

// ------------------------------- Цілі з сервера (трекер) -------------------------------
#define MAX_TARGETS 6
struct MapTarget {
  char ch = 0;              // 'S' шахед, 'J' реактивний, 'D' розвідник, 'U' невідомо, 'C' крилата, 'K' КАБ
  float lat = 0, lon = 0;
  int16_t hd = -1;          // курс, градуси (−1 — невідомий)
  uint8_t p = 0;            // P(пролетить ≤ R км за 30 хв), %
  int8_t eta = -1;          // хв, −1 — немає
  uint8_t n = 1;            // кількість у групі
  uint8_t npt = 0;          // точки прогнозу (5/15/30 хв)
  float pla[3] = {0}, plo[3] = {0};
  uint8_t psd[3] = {0};     // поперечне σ, км (конус невизначеності)
};

// ------------------------------- Загальний стан -------------------------------
// Теплова карта типових маршрутів (з data/history_model.json, сервер вирізає вікно навколо області)
#define HEAT_MAX (28 * 18)
#define HEAT_STEP 0.25f
struct RouteHeat {
  float la0 = 0, lo0 = 0;           // північно-західний кут (центр першої комірки)
  uint8_t w = 0, h = 0;             // колонок / рядків
  uint8_t v[HEAT_MAX];              // 0..35 — інтенсивність
};

struct AppState {
  char oblasts[OBLAST_COUNT + 1];   // 'N','P','A','Y','R' ('?' — немає даних)
  // райони (порядок MAP_REGIONS): '.' немає, 'Y'/'y' дрони/невідомо (мала — частково, громади),
  // 'R'/'r' ракети/КАБ/авіація, '?' — немає даних
  char regions[MAP_REGION_COUNT + 1];
  bool regionsFine = false;         // true — дані з точністю до району (alerts.in.ua з токеном / сервер)
  bool alertsOk = false;
  uint32_t alertsAt = 0;            // millis() останнього успіху
  const char* alertsSrc = "—";

  Level tgLevel = LVL_GREEN;
  int tgScore = 0;
  char tgText[TG_TEXT_MAX] = {0};   // найрелевантніший свіжий пост
  time_t tgTime = 0;
  char tgReason[120] = {0};
  char tgKind[24] = {0};            // "Дрони", "Ракети", "КАБ"...
  bool tgNear = false;              // бал >= 7 — ціль поруч / курс на вас
  bool tgOk = false;
  uint32_t tgAt = 0;
  const char* tgSrc = "—";
  char tgChan[32] = {0};            // канал найважливішого посту
  uint8_t tgNch = 0, tgNok = 0;     // каналів увімкнено / прочитано успішно
  uint8_t tgAgree = 0;              // скільки каналів підтверджують подію

  Level offLevel = LVL_GREEN;       // офіційна тривога у вашому районі: жовтий (дрони/невідомо) / помаранчевий (ракети)
  bool offPartial = false;          // тривога лише в частині району (громади)

  // трекер цілей (лише з сервером-компаньйоном)
  bool trkOk = false;
  Level trkLevel = LVL_GREEN;       // рівень за ймовірністю прольоту цілей поруч
  uint8_t pNear = 0, pDrone = 0, pMissile = 0, pBallistic = 0;   // %, горизонт 30 хв (балістика — 5 хв)
  int8_t eta = -1;                  // хв до найближчого ймовірного прольоту
  uint8_t ntgt = 0;
  MapTarget tgt[MAX_TARGETS];
  char ltName[28] = {0};            // історична модель: найімовірніша ціль атаки поблизу ("Кременчук")
  uint8_t ltP = 0;                  // ... її ймовірність, %
  RouteHeat heat;                   // типові маршрути (комірки 0.25°) навколо вашої області

  Level level = LVL_GREEN;          // підсумковий рівень для адреси користувача
  OutageInfo outage;                // графік відключень (обраної черги)
  uint32_t version = 0;             // збільшується при кожній зміні
};
extern AppState g_state;
void stateLock();
void stateUnlock();
Level computeFinalLevel(AppState& s);   // викликати під lock (оновлює offLevel/offPartial)
char userOblastStatus();
int userRegion();                             // індекс у MAP_REGIONS або -1
void regionsFromOblasts(AppState& s);         // заповнити regions з oblasts (без точності до району)

// ------------------------------- Модулі -------------------------------
void hwInit();
void hwSetBrightness(uint8_t v);
void notifyInit();
void notifyLoop();
void notifyLevel(Level l, bool near = false);   // запуск звуку/LED для рівня (near — ціль поруч)
void notifySilence();        // вимкнути звук (LED лишається)
void notifyTest(Level l);

void netStart();             // запуск фонової задачі (core 0)
void netForceRefresh();
int netChannelStatus(int i, Level* lvl);   // 0 — ще не читали, 1 — ок, 2 — помилка (i = CHANNEL_COUNT — власний канал)
const char* netCloudSrc();                 // "GitHub API" / "GitHub raw" / "Pages" / "—"
void netOutageRefresh();     // перечитати графік відключень (змінилась черга/режим)

void uiInit();
void uiLoop();
