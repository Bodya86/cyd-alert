// OTA: перевірка релізу GitHub (version.json) раз на 6 год (бета — раз на 2 год) і на старті; завантаження HTTPS
// у вільний слот, перевірка розміру та MD5 (Update.h), перезапуск. Ніколи не оновлюється під час помаранчевого/червоного.
// Канали: стабільний — releases/latest (лише після «витримки» на беті, workflow fw-promote); бета — релізний тег
// «beta» (кожна нова збірка одразу). Бета-пристрій перевіряє обидва маніфести й бере новішу версію.
// Відкат при старті: bootloader з CONFIG_APP_ROLLBACK_ENABLE; нова прошивка стартує як PENDING_VERIFY і позначається
// робочою лише після успішного старту (UI працює ≥60 с і отримано дані з мережі). Скидання/паніка/зависання до того —
// bootloader повертає попередню версію. Wi-Fi є, а даних 15 хв немає — відкат вручну (mark invalid). Версія, що двічі
// не стартувала, автоматично більше не встановлюється (вручну — можна).
#include "app.h"
#include "ota.h"
#include "version.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include <Update.h>
#include <esp_ota_ops.h>
#include <Preferences.h>

// Arduino-ядро за замовчуванням одразу позначає нову прошивку робочою (initArduino); беремо це на себе.
extern "C" bool verifyRollbackLater() { return true; }

OtaStatus g_ota;
static volatile bool s_req = false;
static uint32_t s_next = OTA_FIRST_MS;

static void say(const char* m) { strlcpy(g_ota.msg, m, sizeof(g_ota.msg)); g_ota.msgSeq++; Serial.printf("[ota] %s\n", m); }

int otaCompare(const char* a, const char* b) {
  int x[3] = {0}, y[3] = {0};
  sscanf(a, "%d.%d.%d", &x[0], &x[1], &x[2]);
  sscanf(b, "%d.%d.%d", &y[0], &y[1], &y[2]);
  for (int i = 0; i < 3; i++) if (x[i] != y[i]) return x[i] < y[i] ? -1 : 1;
  return 0;
}

void otaRequestCheck() { g_ota.manual = true; s_req = true; }

static bool s_pending = false;      // ця прошивка ще не підтверджена (PENDING_VERIFY)
static bool s_wifiSeen = false;
static char s_bad[16] = "";        // версія, що не стартувала (з NVS)
static uint8_t s_badN = 0;

void otaBootCheck() {
  const esp_partition_t* run = esp_ota_get_running_partition();
  esp_ota_img_states_t st;
  s_pending = run && esp_ota_get_state_partition(run, &st) == ESP_OK && st == ESP_OTA_IMG_PENDING_VERIFY;
  Preferences p;
  p.begin("cydota", false);
  String tried = p.getString("try", "");
  strlcpy(s_bad, p.getString("bad", "").c_str(), sizeof(s_bad));
  s_badN = p.getUChar("badn", 0);
  if (tried.length() && tried != FW_VERSION) {     // встановлювали іншу — а стартувала ця: був відкат
    if (tried == s_bad) s_badN++; else { strlcpy(s_bad, tried.c_str(), sizeof(s_bad)); s_badN = 1; }
    p.putString("bad", s_bad); p.putUChar("badn", s_badN);
    p.remove("try");
    char b[96]; snprintf(b, sizeof(b), "Оновлення v%s не запустилось — повернуто v%s", s_bad, FW_VERSION); say(b);
  }
  p.end();
  Serial.printf("[ota] boot v%s, %s%s\n", FW_VERSION, s_pending ? "PENDING_VERIFY (rollback armed)" : "valid",
                s_bad[0] ? (String(", bad v") + s_bad + " x" + s_badN).c_str() : "");
  if (!s_pending) {
    esp_ota_mark_app_valid_cancel_rollback();       // звичайний старт (або bootloader без rollback) — нічого не чекаємо
  }
}

static void confirm(const char* why) {
  esp_ota_mark_app_valid_cancel_rollback();
  s_pending = false;
  Preferences p;
  p.begin("cydota", false);
  p.remove("try");
  if (!strcmp(s_bad, FW_VERSION)) { p.remove("bad"); p.remove("badn"); s_bad[0] = 0; s_badN = 0; }
  p.end();
  Serial.printf("[ota] v%s confirmed valid (%s)\n", FW_VERSION, why);
}

void otaValidTick() {   // з loop() (core 1) — сам факт виклику означає, що UI живий
  if (!s_pending) return;
  uint32_t up = millis();
  if (WiFi.status() == WL_CONNECTED) s_wifiSeen = true;
  if (up < 60000) return;
  if (!g_set.configured() || !g_set.ssid.length()) { confirm("not configured"); return; }
  if (netDataAgeMs() < 120000) { confirm("UI + data ok"); return; }
  if (!s_wifiSeen && up > 10UL * 60UL * 1000UL) { confirm("no Wi-Fi to judge, UI ok 10 min"); return; }
  if (s_wifiSeen && up > 15UL * 60UL * 1000UL) {   // мережа є, а даних немає — нова версія, схоже, зламана
    Serial.println("[ota] no data 15 min with Wi-Fi -> rollback");
    esp_ota_mark_app_invalid_rollback_and_reboot();
    confirm("rollback impossible");   // сюди потрапляємо, лише якщо відкат неможливий
  }
}

bool otaPending() { return s_pending; }
void otaMarkValid() { confirm("manual"); }

static bool begin(HTTPClient& http, WiFiClientSecure& sec, const String& url) {
  sec.setInsecure();
  sec.setTimeout(20);
  if (!http.begin(sec, url)) return false;
  http.useHTTP10(true);
  http.setTimeout(20000);
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  http.setUserAgent("CYD-Alert/" FW_VERSION " (ESP32)");
  return true;
}

static bool alertActive() {
  stateLock();
  Level l = g_state.level;
  stateUnlock();
  return l >= LVL_ORANGE;
}

static void install(const String& url, int size, const String& md5) {
  WiFiClientSecure sec;
  HTTPClient http;
  g_ota.pct = 0;
  g_ota.state = OTA_DOWNLOADING;
  if (!begin(http, sec, url)) { say("Оновлення: не вдалося з'єднатись"); g_ota.state = OTA_FAILED; return; }
  int code = http.GET();
  int len = http.getSize();
  if (code != 200 || (len > 0 && len != size)) {
    char b[96]; snprintf(b, sizeof(b), "Оновлення: HTTP %d, розмір %d замість %d", code, len, size);
    say(b); http.end(); g_ota.state = OTA_FAILED; return;
  }
  if (!Update.begin(size, U_FLASH)) {
    say("Оновлення: немає місця (потрібна OTA-розмітка — прошийте кабелем)"); http.end(); g_ota.state = OTA_FAILED; return;
  }
  if (md5.length() == 32) Update.setMD5(md5.c_str());
  WiFiClient* s = http.getStreamPtr();
  static uint8_t buf[2048];
  int done = 0;
  uint32_t lastData = millis();
  while (done < size) {
    size_t av = s->available();
    if (av) {
      int n = s->readBytes(buf, av > sizeof(buf) ? sizeof(buf) : av);
      if (n <= 0) continue;
      if (Update.write(buf, n) != (size_t)n) break;
      done += n;
      g_ota.pct = (uint8_t)((int64_t)done * 100 / size);
      lastData = millis();
    } else {
      if (!http.connected() || millis() - lastData > 20000) break;
      vTaskDelay(pdMS_TO_TICKS(5));
    }
  }
  http.end();
  if (done != size) {
    Update.abort();
    char b[96]; snprintf(b, sizeof(b), "Оновлення перервано: %d з %d байт", done, size);
    say(b); g_ota.state = OTA_FAILED; return;
  }
  if (!Update.end(true)) {
    char b[96]; snprintf(b, sizeof(b), "Оновлення відхилено: %s", Update.errorString());
    say(b); g_ota.state = OTA_FAILED; return;
  }
  { Preferences p; p.begin("cydota", false); p.putString("try", g_ota.remote); p.end(); }
  say("Оновлено — перезапуск");
  g_ota.state = OTA_REBOOTING;
  vTaskDelay(pdMS_TO_TICKS(2500));
  ESP.restart();
}

void otaTick() {
  bool manual = s_req;
  if (!manual && (!g_set.autoUpdate || (int32_t)(millis() - s_next) < 0)) return;
  s_req = false;
  if (WiFi.status() != WL_CONNECTED) { s_next = millis() + OTA_RETRY_MS; Serial.println("[ota] check skipped: no Wi-Fi, retry in 10 min"); return; }
  if (alertActive()) {   // ніколи не оновлюємося під час загрози місту
    s_next = millis() + OTA_RETRY_MS;
    Serial.printf("[ota] check skipped: threat level %d (orange/red), retry in %lu min\n", (int)g_state.level, OTA_RETRY_MS / 60000UL);
    if (manual) { say("Зараз загроза — оновлення пізніше"); g_ota.manual = false; }
    return;
  }
  g_ota.state = OTA_CHECKING;
  // бета: спершу тестовий маніфест, потім стабільний — беремо новішу версію
  const char* urls[2] = {g_set.otaBeta ? OTA_BETA_MANIFEST_URL : OTA_MANIFEST_URL, g_set.otaBeta ? OTA_MANIFEST_URL : nullptr};
  JsonDocument doc;
  bool ok = false;
  for (const char* murl : urls) {
    if (!murl) continue;
    Serial.printf("[ota] checking %s (local v%s, %s)%s\n", murl, FW_VERSION, FW_ENV, manual ? " [manual]" : "");
    WiFiClientSecure sec;
    HTTPClient http;
    JsonDocument d;
    bool got = false;
    if (begin(http, sec, murl)) {
      int code = http.GET();
      if (code == 200) got = !deserializeJson(d, http.getStream());
      else Serial.printf("[ota] manifest HTTP %d\n", code);
      http.end();
    }
    if (!got) continue;
    if (!ok || otaCompare(d["version"] | "0", doc["version"] | "0") > 0) { doc = d; ok = true; }
  }
  if (!ok) {
    Serial.println("[ota] check failed: manifest not loaded, retry in 10 min");
    s_next = millis() + OTA_RETRY_MS;
    g_ota.state = OTA_IDLE;
    if (manual) say("Не вдалося перевірити оновлення");
    g_ota.manual = false;
    return;
  }
  s_next = millis() + (g_set.otaBeta ? OTA_BETA_CHECK_MS : OTA_CHECK_MS);
  const char* ver = doc["version"] | "";
  strlcpy(g_ota.remote, ver, sizeof(g_ota.remote));
  JsonObject e = doc["env"][FW_ENV];
  String url = e["url"] | "";
  int size = e["size"] | 0;
  String md5 = e["md5"] | "";
  g_ota.state = OTA_IDLE;
  Serial.printf("[ota] manifest: remote v%s, local v%s, file %s (%d B) -> %s\n", ver[0] ? ver : "?", FW_VERSION,
                url.length() ? "yes" : "no", size, (!ver[0] || otaCompare(ver, FW_VERSION) <= 0) ? "up to date" : "update available");
  if (!ver[0] || otaCompare(ver, FW_VERSION) <= 0) {
    if (manual) { char b[96]; snprintf(b, sizeof(b), "Встановлено останню версію v%s", FW_VERSION); say(b); }
    g_ota.manual = false;
    return;
  }
  if (!url.length() || size <= 0) { say("Маніфест без файлу для цієї плати"); g_ota.manual = false; return; }
  if (!manual && s_badN >= 2 && !strcmp(ver, s_bad)) {   // двічі не стартувала — лише вручну
    Serial.printf("[ota] v%s failed to boot %u times -> auto-install skipped (manual check still installs)\n", ver, s_badN);
    return;
  }
  if (alertActive()) { s_next = millis() + OTA_RETRY_MS; g_ota.manual = false; Serial.println("[ota] install skipped: threat level rose to orange/red, retry in 10 min"); return; }
  char b[96]; snprintf(b, sizeof(b), "Завантажую v%s…", ver); say(b);
  install(url, size, md5);
  g_ota.manual = false;
  if (g_ota.state == OTA_FAILED) s_next = millis() + OTA_RETRY_MS * 3;
}
